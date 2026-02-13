#!/usr/bin/env python3
"""Machina Telegram Command Handlers.

/start /clear /status /gpu /models /use /auto_status /auto_route
  — defined here.

/mcp_status /mcp_reload /mcp_enable /mcp_disable /mcp_add /mcp_remove
/dev_mode /tools /graph_status
  — defined in telegram_commands_ext.py, re-exported below.

Separated from telegram_bot.py for maintainability.
All handlers are async (python-telegram-bot v20+).
"""

import asyncio
import json
import logging
import os
import urllib.request

from telegram import Update
from telegram.ext import ContextTypes

from machina_shared import (
    MACHINA_ROOT,
    CHAT_LOG_FILE,
    save_runtime_config,
    get_active_model,
    get_active_backend,
    get_brain_label,
    is_auto_route_enabled,
    set_auto_route,
)

logger = logging.getLogger(__name__)

# These will be set by telegram_bot.py at import time
AVAILABLE_TOOLS = []
AVAILABLE_GOALS = []
ALLOWED_CHAT_ID = ""
conversation_history = {}


def init(tools, goals, allowed_chat_id, conv_history):
    """Initialize module-level references from telegram_bot.py."""
    global AVAILABLE_TOOLS, AVAILABLE_GOALS, ALLOWED_CHAT_ID, conversation_history
    AVAILABLE_TOOLS = tools
    AVAILABLE_GOALS = goals
    ALLOWED_CHAT_ID = allowed_chat_id
    conversation_history = conv_history


def check_chat_allowed(chat_id: int) -> bool:
    if not ALLOWED_CHAT_ID:
        return True
    return str(chat_id) == str(ALLOWED_CHAT_ID)


# Lazy imports to avoid circular dependency
_call_llm = None
_run_machina_goal = None
_send_chunked = None


def _get_call_llm():
    global _call_llm
    if _call_llm is None:
        from telegram_bot import call_llm
        _call_llm = call_llm
    return _call_llm


def _get_run_machina_goal():
    global _run_machina_goal
    if _run_machina_goal is None:
        from machina_tools import run_machina_goal
        _run_machina_goal = run_machina_goal
    return _run_machina_goal


def _get_send_chunked():
    global _send_chunked
    if _send_chunked is None:
        from telegram_bot import send_chunked
        _send_chunked = send_chunked
    return _send_chunked


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_chat_allowed(update.effective_chat.id):
        return
    tools_list = ", ".join(t["name"] for t in AVAILABLE_TOOLS[:8]) or "없음"
    goals_list = ", ".join(AVAILABLE_GOALS) or "없음"
    brain = get_brain_label()
    dev_explore = os.getenv("MACHINA_DEV_EXPLORE", "") == "1"
    mode_str = "DEV EXPLORE 🟢" if dev_explore else "PRODUCTION 🔵"
    await update.message.reply_text(
        f"안녕! Machina Trinity 봇이야 👋\n"
        f"🧠 두뇌: {brain}\n"
        f"🏷 모드: {mode_str}\n\n"
        "편하게 말 걸어 — 알아서 판단하고 실행해.\n\n"
        "💬 이런거 돼:\n"
        "• 대화, 코딩 질문\n"
        "• 코드 작성+실행 (\"피보나치 짜줘\")\n"
        "• 시스템 점검 (\"에러 봐줘\", \"GPU 어때?\")\n"
        "• 웹 검색 (\"비트코인 가격\")\n"
        "• 파일/메모리 관리 (자동 기억)\n"
        "• 도구 자가 생성 (Genesis)\n\n"
        f"🔧 도구 {len(AVAILABLE_TOOLS)}개 | 🎯 골 {len(AVAILABLE_GOALS)}개\n\n"
        "📌 명령어:\n"
        "  /tools — 사용 가능한 도구 전체 목록\n"
        "  /dev_mode — 학습 모드 전환 (DEV↔PROD)\n"
        "  /models — 모델 목록 (번호로 선택)\n"
        "  /use <번호> — 두뇌 변경\n"
        "  /auto_route — 자동 라우팅 (간단→로컬, 복잡→Claude)\n"
        "  /status — 시스템 상태\n"
        "  /auto_status — 자율 엔진 상태\n"
        "  /gpu — GPU 확인\n"
        "  /clear — 대화 초기화\n"
        "  /stop — 실행 중단\n"
        "  /mcp_status — MCP 서버 상태\n"
        "  /mcp_reload — MCP 설정 리로드\n\n"
        "💾 대화는 자동 기억돼."
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_chat_allowed(update.effective_chat.id):
        return
    chat_id = update.effective_chat.id
    conversation_history[chat_id] = []
    await update.message.reply_text("대화 기록 초기화했어! 🗑️\n(파일 기록은 유지돼)")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_chat_allowed(update.effective_chat.id):
        return
    chat_id = update.effective_chat.id
    hist_count = len(conversation_history.get(chat_id, []))
    log_lines = 0
    if CHAT_LOG_FILE.exists():
        try:
            with open(CHAT_LOG_FILE, "r") as f:
                log_lines = sum(1 for _ in f)
        except Exception as e:
            logger.debug(f"status_command: log line count: {type(e).__name__}: {e}")
            pass
    backend = get_active_backend()
    brain_label = get_brain_label()
    profile = os.getenv("MACHINA_PROFILE", "dev")
    dev_explore = os.getenv("MACHINA_DEV_EXPLORE", "") == "1"
    mode_str = "🟢 DEV EXPLORE" if dev_explore else (f"🔵 {profile.upper()}")
    # Autonomic engine status
    try:
        from telegram_bot import _autonomic_engine
        auto_str = "v5 ACTIVE" if _autonomic_engine else "DISABLED"
    except Exception as e:
        logger.debug(f"status_command: autonomic check: {type(e).__name__}: {e}")
        auto_str = "UNKNOWN"
    status = (
        f"🧠 두뇌: {brain_label}\n"
        f"🔗 백엔드: {backend}\n"
        f"🏷 모드: {mode_str} ({profile})\n"
        f"🤖 자율 엔진: {auto_str}\n"
        f"💬 현재 대화: {hist_count}건\n"
        f"💾 저장된 대화: {log_lines}건\n"
        f"🔧 도구: {len(AVAILABLE_TOOLS)}개\n"
        f"🎯 골: {len(AVAILABLE_GOALS)}개\n"
        f"📁 로그: {CHAT_LOG_FILE}\n"
        f"⚙️ 엔진: {MACHINA_ROOT}"
    )
    await update.message.reply_text(status)


async def gpu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_chat_allowed(update.effective_chat.id):
        return
    await update.message.reply_text("GPU 확인 중... ⏳")
    output = await asyncio.to_thread(_get_run_machina_goal(), "goal.GPU_SMOKE.v1")
    summary = await asyncio.to_thread(
        _get_call_llm(),
        [{"role": "user", "content": f"GPU 상태 결과를 한국어로 짧게 요약해줘:\n{output[:1500]}"}],
        "한국어만 사용해. 기술 결과를 간결하게 요약해. 핵심만."
    )
    await _get_send_chunked()(update, summary)


def _fetch_ollama_models() -> list:
    """Fetch Ollama model list: [(raw_name, display_str), ...]"""
    try:
        base = os.getenv("OAI_COMPAT_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        req = urllib.request.Request(f"{base}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        return [(m.get("name", ""), f"{m.get('name', '')} ({m.get('size', 0) // 1024 // 1024}MB)")
                for m in data.get("models", [])]
    except Exception:
        return []


async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available models and current brain. Numbers can be used with /use."""
    if not check_chat_allowed(update.effective_chat.id):
        return

    cur = get_brain_label()
    model_list = _fetch_ollama_models()

    lines = [f"🧠 현재 두뇌: {cur}", ""]
    if model_list:
        lines.append("📋 Ollama 모델:")
        for i, (_, display) in enumerate(model_list, 1):
            lines.append(f"  {i}. {display}")
    else:
        lines.append("📋 Ollama: (연결 불가)")
    claude_num = len(model_list) + 1
    lines.extend([
        "",
        f"  {claude_num}. ☁️ Claude (claude-opus-4-6)",
        "",
        f"💡 변경: /use <번호> 또는 /use <모델명>",
        f"   예) /use 1",
        f"   예) /use claude",
    ])
    await update.message.reply_text("\n".join(lines))


async def auto_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show autonomic engine state: idle, level, curriculum, curiosity."""
    if not check_chat_allowed(update.effective_chat.id):
        return
    try:
        from telegram_bot import _autonomic_engine
        if not _autonomic_engine:
            await update.message.reply_text("자율 엔진이 비활성 상태야.")
            return
        st = _autonomic_engine.get_status()
        idle = st["idle_sec"]
        idle_str = f"{idle//60}분 {idle%60}초" if idle >= 60 else f"{idle}초"
        rates = st.get("curriculum_rates", {})
        level_done = st.get("level_done", {})

        def _ago(sec):
            if sec < 0:
                return "아직 없음"
            if sec < 60:
                return f"{sec}초 전"
            if sec < 3600:
                return f"{sec//60}분 전"
            return f"{sec//3600}시간 전"

        mode_str = "🟢 개발 탐색" if st.get("dev_explore") else "🔵 운영"
        burst_str = "🔥 자율 작업 중" if st.get("in_burst") else "—"
        stasis_str = "⏸ 정체" if st.get("stasis") else "▶ 활동"
        web_str = "✅ 가능" if st.get("web_search") else "❌ 미설치"
        brain_str = get_brain_label()

        # Translate engine level labels to Korean
        _level_kr = {
            "L1 (Reflect)": "L1 (반성)",
            "L2 (Test)": "L2 (테스트)",
            "L3 (Heal)": "L3 (치유)",
            "L5 (Curiosity)": "L5 (탐구)",
            "Idle (user active)": "대기 (사용자 활동 중)",
        }
        level_label = _level_kr.get(st["current_level"], st["current_level"])

        lines = [
            f"🤖 자율 엔진 v5 상태",
            f"",
            f"🏷 모드: {mode_str}",
            f"⏱ 유휴: {idle_str}",
            f"📊 현재 단계: {level_label}",
            f"🔄 상태: {stasis_str}",
            f"⏸ 일시정지: {'예' if st['paused'] else '아니오'}",
            f"🔥 버스트: {burst_str}",
            f"",
            f"📈 커리큘럼 성적:",
            f"  초급: {rates.get('easy_success_rate', 0):.0%}",
            f"  중급: {rates.get('medium_success_rate', 0):.0%}",
            f"  고급: {rates.get('hard_success_rate', 0):.0%}",
            f"",
            f"🕐 마지막 실행:",
            f"  반성: {_ago(level_done.get('reflect', -1))}",
            f"  테스트: {_ago(level_done.get('test', -1))}",
            f"  치유: {_ago(level_done.get('heal', -1))}",
            f"  정리: {_ago(level_done.get('hygiene', -1))}",
            f"  탐구: {_ago(level_done.get('curiosity', -1))}",
            f"",
            f"🔬 탐구: 오늘 {st['curiosity_daily']}/{st['curiosity_max']}회",
            f"🌐 웹 탐색: {web_str}",
            f"🧠 두뇌: {brain_str}",
            f"🧠 엔진 LLM: {st.get('engine_backend', '?')} (오늘 {st.get('engine_daily_calls', 0)}회, {st.get('engine_daily_tokens', 0)} 토큰)",
        ]
        # Tool introspection profile (v5.1)
        tp = st.get("tool_profile", {})
        if tp.get("total"):
            lines.extend([
                f"",
                f"🔧 도구 내성:",
                f"  등록: {tp['total']}개 | 테스트됨: {tp.get('tested', 0)}개",
                f"  고실패: {tp.get('high_fail', 0)}개 | 가설: {tp.get('hypotheses', 0)}개",
            ])
        # Quality metrics (v5.2)
        qm = st.get("quality_metrics", {})
        if qm:
            lines.extend([
                f"",
                f"📊 자기개선 품질:",
                f"  지식→행동: {qm.get('knowledge_action_ratio', 0):.0%} ({qm.get('knowledge_total', 0)}건)",
                f"  인사이트 신선도: {qm.get('insight_novelty', 0):.0%} ({qm.get('insight_total', 0)}건)",
                f"  탐구 성공률: {qm.get('curiosity_success_rate', 0):.0%} ({qm.get('curiosity_attempted', 0)}회)",
                f"  경험 성공률: {qm.get('experience_success_rate', 0):.0%} ({qm.get('experience_total', 0)}건)",
                f"  메모리: {qm.get('memory_kb', 0)}KB",
            ])
        await _get_send_chunked()(update, "\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"상태 조회 실패: {e}")


async def use_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch brain model. Usage: /use <number or model_name>"""
    if not check_chat_allowed(update.effective_chat.id):
        return

    args = (update.message.text or "").split(None, 1)
    if len(args) < 2 or not args[1].strip():
        await update.message.reply_text("사용법: /use <번호 또는 모델명>\n예) /use 1\n예) /use claude\n\n/models 로 목록 확인")
        return

    model = args[1].strip()
    chat_id = update.effective_chat.id

    # Number-based selection — fetch live if cached list empty
    model_list = context.bot_data.get("_model_list") or _fetch_ollama_models()
    if model.isdigit():
        idx = int(model)
        claude_num = len(model_list) + 1
        if idx == claude_num:
            model = "claude"
        elif 1 <= idx <= len(model_list):
            model = model_list[idx - 1][0]  # raw name
        else:
            await update.message.reply_text(f"잘못된 번호야. /models 로 목록 확인해줘.")
            return

    if model.lower() in ("claude", "anthropic", "claude-opus", "opus"):
        os.environ["MACHINA_CHAT_BACKEND"] = "anthropic"
        save_runtime_config()
        cur_model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
        logger.info(f"[{chat_id}] Brain switched to Claude ({cur_model})")
        await update.message.reply_text(f"🧠 Claude ({cur_model})로 전환!")
        try:
            greeting = await asyncio.to_thread(
                _get_call_llm(),
                [{"role": "user", "content": "두뇌가 방금 교체됐어. 너 자신을 한줄로 소개하고 뭘 잘하는지 말해봐."}],
                "너는 Machina Trinity AI 비서야. 한국어 반말로 짧게 답해."
            )
            await update.message.reply_text(greeting[:2000])
        except Exception as e:
            logger.error(f"Claude greeting failed: {type(e).__name__}: {e}")
            await update.message.reply_text(f"(Claude 인사 실패: {e})")
    else:
        os.environ["MACHINA_CHAT_BACKEND"] = "oai_compat"
        os.environ["OAI_COMPAT_MODEL"] = model
        save_runtime_config()
        logger.info(f"[{chat_id}] Brain switched to Ollama ({model})")
        await update.message.reply_text(f"🧠 Ollama ({model})로 전환!")
        try:
            greeting = await asyncio.to_thread(
                _get_call_llm(),
                [{"role": "user", "content": "두뇌가 방금 교체됐어. 너 자신을 한줄로 소개하고 뭘 잘하는지 말해봐."}],
                "너는 Machina Trinity AI 비서야. 한국어 반말로 짧게 답해."
            )
            await update.message.reply_text(greeting[:2000])
        except Exception as e:
            await update.message.reply_text(f"(모델 응답 없음: {e})")


async def auto_route_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle automatic multi-model routing (local for simple, Claude for complex).

    Usage: /auto_route         -- toggle on/off
           /auto_route on      -- enable
           /auto_route off     -- disable
           /auto_route status  -- show stats
    """
    if not check_chat_allowed(update.effective_chat.id):
        return

    args = (update.message.text or "").split(None, 1)
    sub = args[1].strip().lower() if len(args) > 1 else ""

    current = is_auto_route_enabled()

    # Sub-command: status
    if sub == "status":
        try:
            from telegram_bot import _auto_route_stats
            stats = _auto_route_stats
        except ImportError:
            stats = {"routed_to_claude": 0, "stayed_local": 0, "total_scored": 0}
        state_str = "ON" if current else "OFF"
        backend = get_active_backend()
        brain = get_brain_label()
        has_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
        lines = [
            f"Auto-Route: {state_str}",
            f"Current brain: {brain} ({backend})",
            f"Claude API key: {'set' if has_key else 'NOT set'}",
            f"",
            f"Stats (this session):",
            f"  Scored: {stats['total_scored']}",
            f"  Routed to Claude: {stats['routed_to_claude']}",
            f"  Stayed local: {stats['stayed_local']}",
            f"",
            f"Threshold: complexity >= 0.6 -> Claude",
            f"Rule: only upgrade (local->Claude), never downgrade",
        ]
        await update.message.reply_text("\n".join(lines))
        return

    # Toggle or explicit on/off
    if sub in ("on", "켜", "활성"):
        new_state = True
    elif sub in ("off", "꺼", "비활성"):
        new_state = False
    else:
        new_state = not current  # toggle

    set_auto_route(new_state)
    state_str = "ON" if new_state else "OFF"
    has_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

    msg = f"Auto-Route: {state_str}"
    if new_state and not has_key:
        msg += "\n(ANTHROPIC_API_KEY not set -- routing to Claude will not work)"
    if new_state:
        msg += "\n\nSimple queries -> local model (free)"
        msg += "\nComplex queries (>= 0.6) -> Claude (smart)"
        msg += "\nNever auto-downgrades from Claude to local"

    logger.info(f"[{update.effective_chat.id}] Auto-route set to {state_str}")
    await update.message.reply_text(msg)


# ── Re-export extended commands (MCP, dev, tools, graph) ─────────────────────
# telegram_bot.py accesses these via telegram_commands.X attribute style,
# so re-exporting here keeps all existing imports working.
from telegram_commands_ext import (  # noqa: F401, E402
    mcp_status_command,
    mcp_reload_command,
    mcp_enable_command,
    mcp_disable_command,
    mcp_add_command,
    mcp_remove_command,
    dev_mode_command,
    tools_command,
    graph_status_command,
)
