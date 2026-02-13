#!/usr/bin/env python3
"""Machina Trinity Telegram Bot — LLM-driven agent via chat_driver pipeline.

Pulse Loop (multi-turn): Intent → Execute → Continue, autonomous until done.
Commands are optional shortcuts — all features accessible via natural language.
Modules: machina_shared, machina_learning, machina_dispatch, machina_tools,
telegram_commands, machina_autonomic, policies/chat_driver.

Key features:
- Autonomous execution: LLM chains actions until done (max 100 cycles, 1hr budget)
- Tool lifecycle: create/register/use/update/delete (util_save/util_run/util_delete/util_update)
- Auto-memory: personal facts + conversation context
- Autonomic Engine v5: heartbeat via PTB JobQueue, 1hr burst sessions
- Clean alert format: structured status messages without noise

Structure (split for maintainability):
- telegram_bot.py: Config, globals, utilities, LLM/dispatch, chunking, autonomic, main()
- telegram_bot_handlers.py: Approval, permissions, planning, complexity, auto-memory
- telegram_bot_pulse.py: handle_message() — the main Pulse Loop
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Suppress noisy MCP/httpx logs (405 reconnect spam from streamable_http GET polling)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from machina_shared import (
    _jsonl_append,
    CHAT_LOG_DIR,
    CHAT_LOG_FILE,
    MEM_DIR,
    load_runtime_config,
    save_runtime_config,
    get_active_model,
    get_active_backend,
    get_brain_label,
    is_auto_route_enabled,
    set_auto_route,
)

from machina_learning import (
    experience_record,
    skill_search,
    wisdom_retrieve,
    memory_search_recent,
)
# Add policies/ to sys.path so chat_driver can find its siblings (chat_llm, etc.)
import sys as _sys
_policies_dir = str(Path(__file__).resolve().parent / "policies")
if _policies_dir not in _sys.path:
    _sys.path.insert(0, _policies_dir)
from machina_tools import load_available_tools_and_goals

from telegram import Update
from telegram.error import InvalidToken, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from machina_permissions import (
    check_permission, grant_session, format_approval_message,
    get_permission_summary, clear_session_grants,
    ASK, DENY,
)

import telegram_commands

# ===========================================================================
# Configuration
# ===========================================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
MACHINA_ROOT = os.getenv("MACHINA_ROOT", os.path.dirname(os.path.abspath(__file__)))
_last_active_chat_id = None  # fallback for alert delivery when ALLOWED_CHAT_ID unset
CHAT_DRIVER_CMD = os.getenv("MACHINA_CHAT_CMD", f"python3 {MACHINA_ROOT}/policies/chat_driver.py")
MAX_HISTORY = 20
conversation_history: dict[int, list] = defaultdict(list)
_chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_auto_memory_seen: set = set()  # hash-based dedup for auto-memory facts
_session_ids: dict[int, str] = {}  # chat_id -> session_id for context chain
_dst_states: dict[int, dict] = {}  # chat_id -> DST state for dialogue tracking

AVAILABLE_TOOLS = []
AVAILABLE_GOALS = []

# ---------------------------------------------------------------------------
# Auto-Routing: complexity-based multi-model routing stats
# ---------------------------------------------------------------------------
_auto_route_stats = {"routed_to_claude": 0, "stayed_local": 0, "total_scored": 0}

# Thread-safe backend override for auto-routing (avoids os.environ race condition
# when concurrent_updates=True). Per-request override stored here; None = no override.
import threading
_backend_override: dict[int, str | None] = {}  # chat_id -> backend override
_backend_override_lock = threading.Lock()

# Pending approval requests: {approval_id: asyncio.Event, result: bool}
_pending_approvals: dict[str, dict] = {}
APPROVAL_TIMEOUT = int(os.getenv("MACHINA_PERMISSION_TIMEOUT_S", "180"))

# User interrupt: set per-chat flag to stop running pulse loop
_pulse_cancel: dict[int, bool] = {}  # chat_id -> cancel flag

SYSTEM_PROMPT = """너는 마키나(Machina), 뭐든 도와주는 범용 AI야. 반말로 편하게 대화해.
대화, 계산, 조사, 코딩, 시스템 관리 전부 가능해. 경험이 쌓일수록 더 똑똑해져.
규칙: 한국어, 짧고 자연스럽게 (500자 이내), 이모지 적당히."""


# ===========================================================================
# Core utility functions (used by handlers and pulse loop)
# ===========================================================================

def save_chat_log(chat_id: int, role: str, content: str):
    """Append a chat message to the JSONL log file."""
    try:
        CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts_ms": int(time.time() * 1000),
            "chat_id": chat_id,
            "role": role,
            "content": content,
        }
        _jsonl_append(CHAT_LOG_FILE, entry)
    except Exception as e:
        logger.error(f"Failed to save chat log: {e}")

def load_chat_history(chat_id: int, max_entries: int = MAX_HISTORY * 2) -> list:
    """Load recent conversation history from JSONL (tail-read for efficiency)."""
    if not CHAT_LOG_FILE.exists():
        return []
    try:
        # Tail-read: only last ~200KB (enough for recent history)
        tail_bytes = 200_000
        with open(CHAT_LOG_FILE, "rb") as fb:
            fb.seek(0, 2)
            size = fb.tell()
            fb.seek(max(0, size - tail_bytes))
            if size > tail_bytes:
                fb.readline()  # skip partial first line
            raw = fb.read().decode("utf-8", errors="replace")

        entries = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("chat_id") == chat_id and entry.get("role") in ("user", "assistant"):
                    entries.append({
                        "role": entry["role"],
                        "content": entry["content"],
                    })
            except json.JSONDecodeError:
                continue
        return entries[-max_entries:]
    except Exception as e:
        logger.error(f"Failed to load chat history: {e}")
        return []

def call_llm(messages: list, system: str = SYSTEM_PROMPT) -> str:
    """Call LLM — auto-selects Anthropic/Ollama/OAI via chat_llm layer."""
    from policies.chat_llm import (
        _call_anthropic as _llm_anthropic,
        _call_ollama_text, _call_oai_compat_text, _is_ollama,
    )
    backend = os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")
    try:
        if backend == "anthropic" and os.getenv("ANTHROPIC_API_KEY", "").strip():
            return _llm_anthropic(system, messages)
        if _is_ollama():
            return _call_ollama_text(system, messages)
        return _call_oai_compat_text(system, messages)
    except Exception as e:
        logger.error(f"LLM call error: {e}")
        return "미안, LLM 연결에 문제가 있어 😥 잠시 후 다시 해봐."


def call_chat_driver(mode: str, conversation: list, timeout_sec: int = 90, **kwargs) -> dict:
    """Call chat_driver.py subprocess. Modes: intent, summary, chat, continue."""
    payload = {
        "mode": mode,
        "conversation": conversation,
        "available_tools": AVAILABLE_TOOLS,
        "available_goals": AVAILABLE_GOALS,
        "session": {},
    }
    payload.update(kwargs)

    payload_json = json.dumps(payload, ensure_ascii=False)
    import shlex
    argv = shlex.split(CHAT_DRIVER_CMD)

    try:
        result = subprocess.run(
            argv,
            input=payload_json,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=MACHINA_ROOT,
        )
        if result.stderr.strip():
            logger.warning(f"chat_driver stderr: {result.stderr.strip()[:300]}")
        if result.returncode != 0:
            logger.error(f"chat_driver error (rc={result.returncode}): {result.stderr[:200]}")
            return {}

        # Find first JSON line in output
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        logger.warning(f"chat_driver: no JSON in stdout: {result.stdout[:200]}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"chat_driver JSON parse error: {e}")
        return {}
    except Exception as e:
        logger.error(f"chat_driver call error: {e}")
        return {}


def check_chat_allowed(chat_id: int) -> bool:
    if not ALLOWED_CHAT_ID:
        return True
    return str(chat_id) == str(ALLOWED_CHAT_ID)


# ===========================================================================
# Chunking: Telegram message splitting
# ===========================================================================

TG_MAX = 4000  # safe margin below 4096


def _fence_lang(text: str, pos: int) -> str:
    """Extract code fence language from opening ``` at *pos*."""
    start = pos + 3
    end = text.find("\n", start)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def smart_chunk(text: str, max_len: int = TG_MAX) -> list[str]:
    """Split text into Telegram-safe chunks preserving code fences.

    1. Prefer splitting outside code blocks (fence_count even).
    2. If split lands inside a code block, auto-close before cut
       and re-open (with language tag) in the next chunk.
    Priority: code fence > paragraph > newline > space > hard cut.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    threshold = int(max_len * 0.3)

    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        part = text[:max_len]
        cut = -1

        # Priority: code fence > paragraph > newline > space > hard cut
        for sep, strip_nl in [("\n```", True), ("\n\n", True), ("\n", False)]:
            pos = part.rfind(sep)
            if pos > threshold:
                cut = pos
                head = text[:cut].rstrip() if strip_nl else text[:cut]
                tail = text[cut:].lstrip("\n") if strip_nl else text[cut + 1:]
                break
        else:
            sp = part.rfind(" ")
            if sp > threshold:
                cut = sp
                head, tail = text[:cut], text[cut + 1:]
            else:
                cut = max_len
                head, tail = text[:cut], text[cut:]

        # --- Code-fence safety: count ``` in head ---
        fence_count = head.count("```")
        if fence_count % 2 == 1:
            # Head ends inside a code block.
            # Find the opening fence to detect its language.
            last_open = head.rfind("```")
            lang = _fence_lang(head, last_open) if last_open >= 0 else ""
            head += "\n```"
            tail = f"```{lang}\n" + tail

        chunks.append(head)
        text = tail

    return chunks


async def send_chunked(update: Update, text: str):
    """Send text split into smart chunks with inter-chunk delay."""
    if not text:
        return
    parts = smart_chunk(text)
    for i, chunk in enumerate(parts):
        if chunk.strip():
            try:
                await update.message.reply_text(chunk)
            except Exception as e:
                logger.error(f"send_chunked error: {e}")
            if i < len(parts) - 1:
                await asyncio.sleep(0.3)  # avoid Telegram rate limit


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")


# ===========================================================================
# Autonomic Engine integration
# ===========================================================================

try:
    from machina_autonomic import AutonomicEngine as _AutonomicEngineClass, set_alert_callback
    _autonomic_engine = _AutonomicEngineClass()
    logger.info("Autonomic Engine v5 loaded")
except ImportError as _ie:
    logger.warning(f"Autonomic disabled: {_ie}")
    _autonomic_engine = None
    set_alert_callback = None

# Alert queue: autonomic engine runs in a thread, so we queue alerts
# and deliver them from the async event loop.
_alert_queue: list[str] = []
_alert_queue_lock = None

try:
    import threading
    _alert_queue_lock = threading.Lock()
    _tick_lock = threading.Lock()
except ImportError:
    _tick_lock = None


def _autonomic_alert_enqueue(message: str):
    """Thread-safe enqueue of alert messages from autonomic engine."""
    if _alert_queue_lock:
        with _alert_queue_lock:
            if len(_alert_queue) < 50:  # cap to prevent memory issues
                _alert_queue.append(message)

if set_alert_callback:
    set_alert_callback(_autonomic_alert_enqueue)


def autonomic_touch():
    """Call on every user message to reset idle timer."""
    if _autonomic_engine:
        _autonomic_engine.touch()


_tick_thread = None  # Track tick thread for graceful shutdown


async def autonomic_heartbeat(context: ContextTypes.DEFAULT_TYPE):
    """Heartbeat — fires tick() in non-daemon thread, delivers alerts every interval.

    Design:
    - tick() runs in a NON-daemon thread (safe for file writes on shutdown)
    - Protected by _tick_lock — only one tick() runs at a time
    - This callback returns FAST (no await on tick), so APScheduler never skips it
    - Alerts flow to Telegram every heartbeat, even during 30-min burst sessions
    """
    global _tick_thread
    if not _autonomic_engine:
        return

    # Fire tick in background thread if not already running
    if _tick_lock and _tick_lock.acquire(blocking=False):
        def _run_tick():
            try:
                _autonomic_engine.tick(
                    abort_check=lambda: _autonomic_engine.idle_seconds() < 60)
            except Exception as e:
                logger.error(f"[Autonomic] tick error: {e}")
            finally:
                _tick_lock.release()
        t = threading.Thread(target=_run_tick, name="autonomic-tick")
        t.daemon = False  # Non-daemon: file writes complete on shutdown
        t.start()
        _tick_thread = t

    # Always deliver queued alerts — even during long tick/burst
    target_chat = ALLOWED_CHAT_ID or (_last_active_chat_id and str(_last_active_chat_id))
    if _alert_queue_lock and target_chat:
        with _alert_queue_lock:
            pending = list(_alert_queue)
            _alert_queue.clear()
        for msg in pending[:10]:  # up to 10 alerts per heartbeat for burst verbosity
            for _retry in range(3):
                try:
                    chunks = smart_chunk(msg)
                    for ci, chunk in enumerate(chunks):
                        if chunk.strip():
                            await context.bot.send_message(
                                chat_id=int(target_chat),
                                text=chunk,
                            )
                            if ci < len(chunks) - 1:
                                await asyncio.sleep(0.3)
                    break  # success
                except Exception as ae:
                    if _retry < 2:
                        await asyncio.sleep(1 << _retry)  # 1s, 2s backoff
                    else:
                        logger.warning(f"Alert delivery failed after 3 retries: {ae}")


# ===========================================================================
# Re-exports from split modules (for backward compatibility)
# ===========================================================================
# These must be at the BOTTOM of the file, after all globals are defined,
# to avoid circular import issues.

from telegram_bot_handlers import (  # noqa: E402,F401
    _compute_complexity,
    _detect_memorable_facts,
    AUTO_MEMORY_PROMPT,
    _is_multi_step_request,
    _is_all_tools_request,
    _build_all_tools_plan,
    _step_to_intent,
    _coerce_response,
    _extract_embedded_action,
    _unwrap_json_response,
    _validate_continuation_actions,
)

from telegram_bot_pulse import handle_message  # noqa: E402,F401


async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wrapper for approval_callback that passes shared _pending_approvals."""
    from telegram_bot_handlers import approval_callback as _ac
    return await _ac(update, context, _pending_approvals)


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wrapper for stop_command that passes shared _pulse_cancel."""
    from telegram_bot_handlers import stop_command as _sc
    return await _sc(update, context, _pulse_cancel)


async def request_approval(chat_id: int, aid: str, inputs: dict,
                           context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Wrapper for request_approval that passes shared state."""
    from telegram_bot_handlers import request_approval as _ra
    return await _ra(chat_id, aid, inputs, context,
                     _pending_approvals, APPROVAL_TIMEOUT)


async def _check_action_permissions(actions: list, chat_id: int,
                                     context: ContextTypes.DEFAULT_TYPE) -> list:
    """Wrapper for _check_action_permissions that passes shared state."""
    from telegram_bot_handlers import _check_action_permissions as _cap
    return await _cap(actions, chat_id, context,
                      _pending_approvals, APPROVAL_TIMEOUT)


# ===========================================================================
# Main entry point
# ===========================================================================

def main():
    load_runtime_config()  # Restore persisted config (model/backend) from last session
    brain = get_brain_label()
    backend = get_active_backend()
    logger.info(f"Starting Machina Bot | {brain} | {backend} | chat={ALLOWED_CHAT_ID or 'all'}")
    global AVAILABLE_TOOLS, AVAILABLE_GOALS
    AVAILABLE_TOOLS, AVAILABLE_GOALS = load_available_tools_and_goals()
    telegram_commands.init(AVAILABLE_TOOLS, AVAILABLE_GOALS, ALLOWED_CHAT_ID, conversation_history)
    async def _post_init(application):
        """Post-init hook: connect MCP servers and register tools."""
        try:
            from machina_dispatch import register_mcp_tools
            await register_mcp_tools()
            from machina_mcp import mcp_manager
            if mcp_manager.tool_count > 0:
                logger.info(f"  MCP Bridge: {mcp_manager.tool_count} tools from "
                            f"{len(mcp_manager.servers)} server(s)")
        except Exception as e:
            logger.warning(f"  MCP Bridge: init failed: {type(e).__name__}: {e}")

    app = (Application.builder()
           .token(BOT_TOKEN)
           .concurrent_updates(True)
           .post_init(_post_init)
           .build())
    app.add_handler(CommandHandler("start", telegram_commands.start_command))
    app.add_handler(CommandHandler("clear", telegram_commands.clear_command))
    app.add_handler(CommandHandler("status", telegram_commands.status_command))
    app.add_handler(CommandHandler("gpu", telegram_commands.gpu_command))
    app.add_handler(CommandHandler("models", telegram_commands.models_command))
    app.add_handler(CommandHandler("use", telegram_commands.use_command))
    app.add_handler(CommandHandler("auto_status", telegram_commands.auto_status_command))
    app.add_handler(CommandHandler("auto_route", telegram_commands.auto_route_command))
    app.add_handler(CommandHandler("mcp_status", telegram_commands.mcp_status_command))
    app.add_handler(CommandHandler("mcp_reload", telegram_commands.mcp_reload_command))
    app.add_handler(CommandHandler("mcp_enable", telegram_commands.mcp_enable_command))
    app.add_handler(CommandHandler("mcp_disable", telegram_commands.mcp_disable_command))
    app.add_handler(CommandHandler("mcp_add", telegram_commands.mcp_add_command))
    app.add_handler(CommandHandler("mcp_remove", telegram_commands.mcp_remove_command))
    app.add_handler(CommandHandler("graph_status", telegram_commands.graph_status_command))
    app.add_handler(CommandHandler("dev_mode", telegram_commands.dev_mode_command))
    app.add_handler(CommandHandler("tools", telegram_commands.tools_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CallbackQueryHandler(approval_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    job_queue = app.job_queue
    if job_queue and _autonomic_engine:
        _hb = _autonomic_engine._t.get("heartbeat", 60)
        job_queue.run_repeating(autonomic_heartbeat, interval=_hb, first=min(_hb * 2, 120))
        _mode = "DEV EXPLORE" if _autonomic_engine._dev else "PRODUCTION"
        logger.info(f"  Autonomic Engine v5: ACTIVE ({_mode}, heartbeat={_hb}s)")
    elif not _autonomic_engine:
        logger.warning("  Autonomic Engine: DISABLED")
    else:
        logger.warning("  Autonomic Engine: DISABLED (no JobQueue)")

    # Graceful shutdown: save state + wait for tick thread
    import atexit

    def _graceful_shutdown():
        logger.info("[Shutdown] Saving autonomic state...")
        if _autonomic_engine:
            _autonomic_engine._save_state()
        if _tick_thread and _tick_thread.is_alive():
            logger.info("[Shutdown] Waiting for tick thread (max 10s)...")
            _tick_thread.join(timeout=10)
        # MCP cleanup
        try:
            from machina_mcp import mcp_manager
            if mcp_manager.is_started:
                logger.info("[Shutdown] Stopping MCP servers...")
        except Exception as e:
            logger.debug(f"[Shutdown] MCP cleanup: {type(e).__name__}: {e}")
            pass
    atexit.register(_graceful_shutdown)

    retry_delay_s = max(2, int(os.getenv("MACHINA_BOT_RETRY_DELAY_S", "5")))
    retry_max_delay_s = max(
        retry_delay_s, int(os.getenv("MACHINA_BOT_RETRY_MAX_DELAY_S", "30"))
    )
    max_retries = max(0, int(os.getenv("MACHINA_BOT_MAX_RETRIES", "0")))  # 0 = infinite
    attempt = 0
    while True:
        try:
            logger.info("Bot polling started...")
            app.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
            break  # graceful shutdown path
        except InvalidToken:
            logger.exception("Bot polling failed: invalid telegram token")
            raise
        except NetworkError as e:
            attempt += 1
            if max_retries and attempt >= max_retries:
                logger.error(
                    "Bot polling failed with NetworkError (%d/%d): %s",
                    attempt, max_retries, e,
                )
                raise
            logger.warning(
                "Bot polling network error (attempt %d): %s; retrying in %ds",
                attempt, e, retry_delay_s,
            )
            time.sleep(retry_delay_s)
            retry_delay_s = min(retry_delay_s * 2, retry_max_delay_s)
        except Exception as e:
            # Keep process alive for transient runtime faults (event loop hiccups, DNS flaps, etc.)
            attempt += 1
            if max_retries and attempt >= max_retries:
                logger.exception(
                    "Bot polling failed with fatal error (%d/%d): %s",
                    attempt, max_retries, e,
                )
                raise
            logger.exception(
                "Bot polling runtime error (attempt %d), retrying in %ds",
                attempt, retry_delay_s,
            )
            time.sleep(retry_delay_s)
            retry_delay_s = min(retry_delay_s * 2, retry_max_delay_s)


if __name__ == "__main__":
    # Prevent dual-module bug: __main__ vs telegram_bot
    # Without this, `import telegram_bot` in submodules creates a SECOND
    # module instance with separate globals (_pending_approvals etc.)
    import sys as _sys
    _sys.modules.setdefault("telegram_bot", _sys.modules[__name__])
    main()
