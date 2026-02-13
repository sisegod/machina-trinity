"""Machina Telegram Bot — Handler helpers.

Approval, permissions, planning, complexity scoring, auto-memory detection,
and the /stop command. Extracted from telegram_bot.py for maintainability.

The main pulse loop (handle_message) is in telegram_bot_pulse.py.
"""

import asyncio
import json
import logging
import os
import re
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from machina_permissions import (
    check_permission, grant_session, format_approval_message,
    ASK, DENY,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Complexity scoring for auto-routing
# ---------------------------------------------------------------------------

def _compute_complexity(text: str, history: list) -> float:
    """Score message complexity 0.0 (trivial) to 1.0 (complex).

    Factors:
    - Message length (longer = more complex)
    - Code/reasoning keywords (Korean + English)
    - Multi-step indicators
    - Conversation depth (more turns = more complex context)
    """
    score = 0.0
    lower = text.lower()

    # Length factor (0-0.3): 500+ chars = max
    score += min(len(text) / 500, 0.3)

    # Complexity keywords — reasoning, analysis, architecture (0-0.3)
    complex_kw = {
        "알고리즘", "설계", "분석", "비교", "아키텍처", "최적화", "리팩토링",
        "보안", "마이그레이션", "디버깅", "성능", "원리", "차이점", "장단점",
        "algorithm", "design", "architecture", "optimize", "explain", "analyze",
        "compare", "debug", "refactor", "security", "migrate", "performance",
        "왜", "어떻게", "원인", "이유",
    }
    hits = sum(1 for kw in complex_kw if kw in lower)
    score += min(hits * 0.1, 0.3)

    # Multi-step indicators (0-0.2)
    multi_kw = {
        "그리고", "다음에", "먼저", "또한", "추가로", "그다음",
        "and then", "also", "step", "first", "next", "finally",
    }
    multi_hits = sum(1 for kw in multi_kw if kw in lower)
    score += min(multi_hits * 0.1, 0.2)

    # Conversation depth factor (0-0.2): long conversations = more context needed
    if len(history) > 6:
        score += 0.1
    if len(history) > 12:
        score += 0.1

    return min(score, 1.0)


# ---------------------------------------------------------------------------
# Auto-memory: detect memorable facts from user messages
# ---------------------------------------------------------------------------

AUTO_MEMORY_PROMPT = """사용자 메시지에서 장기 기억할 가치가 있는 사실만 추출해.
저장 대상: 개인정보(생일,이름,나이,직업), 환경(OS,장비,언어), 선호(좋아하는것), 중요 사실.
무시 대상: 인사,감정표현,일회성질문,잡담,명령,요청.
사실이 있으면: {"facts":["사실1","사실2"]}
없으면: {"facts":[]}
JSON만 출력."""


def _detect_memorable_facts(user_text: str) -> list[str]:
    """Detect facts worth remembering from user message via lightweight LLM call.

    Respects current backend --- uses Ollama for local models, or skips if
    running on Anthropic (auto-memory detection is a lightweight background task,
    not worth a paid API call).
    """
    if len(user_text) < 5:
        return []
    # Auto-memory uses _call_ollama (local) regardless of chat backend --- no API cost.
    try:
        from machina_shared import _call_ollama
        raw = _call_ollama(
            f"사용자: {user_text[:300]}",
            system=AUTO_MEMORY_PROMPT,
            max_tokens=200, temperature=0.1, timeout=30,
            think=False,  # Disable thinking mode --- saves tokens for simple extraction
        )
        # Extract JSON from free text (Qwen3 thinking mode + format_json conflict)
        if raw and not raw.strip().startswith("{"):
            match = re.search(r'\{[^{}]*"facts"[^{}]*\}', raw)
            raw = match.group(0) if match else "{}"
        parsed = json.loads(raw.strip() if raw else "{}")
        facts = parsed.get("facts", [])
        return [f for f in facts if isinstance(f, str) and len(f) > 3]
    except Exception as e:
        # L15: timeout or failure in fact detection should be visible in logs
        logger.warning(f"Auto-memory fact detection error: {e}")
        return []


# ---------------------------------------------------------------------------
# Approval / Permission handlers
# ---------------------------------------------------------------------------

async def request_approval(chat_id: int, aid: str, inputs: dict,
                           context: ContextTypes.DEFAULT_TYPE,
                           pending_approvals: dict,
                           approval_timeout: int) -> bool:
    """Send InlineKeyboard approval request and wait for user response.

    Returns True if approved, False if denied or timed out.
    """
    # Short approval_id to stay within Telegram's 64-byte callback_data limit
    _short_ts = hex(int(time.time() * 100) % 0xFFFFFFFF)[2:]
    approval_id = f"p{_short_ts}"
    msg_text = format_approval_message(aid, inputs)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("허용", callback_data=f"a:{approval_id}"),
            InlineKeyboardButton("거부", callback_data=f"d:{approval_id}"),
            InlineKeyboardButton("항상허용", callback_data=f"A:{approval_id}"),
        ]
    ])
    event = asyncio.Event()
    pending_approvals[approval_id] = {"event": event, "approved": False, "aid": aid}
    logger.info(f"[Approval] request: id={approval_id}, aid={aid}")
    try:
        await context.bot.send_message(chat_id=chat_id, text=msg_text,
                                       reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        try:
            # Fallback without markdown
            await context.bot.send_message(chat_id=chat_id, text=msg_text,
                                           reply_markup=keyboard)
        except Exception as e2:
            # Approval message failed to send --- auto-deny to prevent hang
            logger.error(f"Approval send failed: {type(e2).__name__}: {e2}")
            pending_approvals.pop(approval_id, None)
            return False
    # Wait for user response with timeout
    try:
        await asyncio.wait_for(event.wait(), timeout=approval_timeout)
    except asyncio.TimeoutError:
        await context.bot.send_message(chat_id=chat_id, text="⏰ 시간 초과 — 요청 거부됨.")
        pending_approvals.pop(approval_id, None)
        return False
    result = pending_approvals.pop(approval_id, {})
    return result.get("approved", False)


async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             pending_approvals: dict):
    """Handle InlineKeyboard button press for permission approval.

    Callback data format: a:ID (approve), d:ID (deny), A:ID (always-approve).
    AID is stored in pending_approvals[ID]["aid"], not in callback_data (64-byte limit).
    """
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 2:
        return
    action = parts[0]
    approval_id = parts[1]
    logger.info(f"[Approval] callback: id={approval_id}, action={action}")
    if approval_id not in pending_approvals:
        await query.edit_message_text("⚠️ 만료된 요청이야.")
        return
    entry = pending_approvals[approval_id]
    if action in ("a", "approve"):
        entry["approved"] = True
        aid = entry.get("aid", "")
        if aid:
            grant_session(aid)  # Auto-grant for session (no re-ask)
        await query.edit_message_text("✅ 허용됨!")
    elif action in ("A", "always"):
        entry["approved"] = True
        aid = entry.get("aid", "")
        if aid:
            grant_session(aid)
        await query.edit_message_text(f"✅ 항상 허용됨: {aid}")
    else:
        entry["approved"] = False
        await query.edit_message_text("❌ 거부됨.")
    entry["event"].set()


async def _check_action_permissions(actions: list, chat_id: int,
                                     context: ContextTypes.DEFAULT_TYPE,
                                     pending_approvals: dict,
                                     approval_timeout: int) -> list:
    """Pre-check permissions for all actions. Returns filtered list (approved only)."""
    from machina_dispatch import resolve_alias
    approved = []
    for action in actions:
        kind = action.get("kind", "")
        if kind != "tool":
            approved.append(action)
            continue
        aid = resolve_alias(action.get("aid", ""))
        inputs = action.get("inputs", {})
        perm = check_permission(aid)
        if perm == DENY:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🚫 차단됨: `{aid}` (권한 모드에서 거부됨)",
                parse_mode="Markdown")
            continue
        if perm == ASK:
            ok = await request_approval(chat_id, aid, inputs, context,
                                        pending_approvals, approval_timeout)
            if not ok:
                continue
        approved.append(action)
    return approved


# ---------------------------------------------------------------------------
# /stop command
# ---------------------------------------------------------------------------

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       pulse_cancel: dict):
    """Stop a running pulse loop for this chat."""
    chat_id = update.effective_chat.id
    pulse_cancel[chat_id] = True
    await update.message.reply_text("작업 중단 요청. 현재 사이클 완료 후 멈출게.")
    logger.info(f"[{chat_id}] User requested pulse stop")


# ---------------------------------------------------------------------------
# Plan-then-Execute: Multi-step detection + plan generation
# ---------------------------------------------------------------------------
_MULTI_STEP_KW = ("하나씩", "전부", "모두", "순서대로", "차례대로",
                   "쭉 ", "다 해", "다 사용", "다 써", "다 실행", "다 돌려",
                   "all ", "each", "every", "one by one", "try all")
_ALL_TOOLS_KW = ("다 사용", "다 써봐", "다 해봐", "전부 사용", "전부 실행",
                  "모든 도구", "전체 도구", "하나씩 다", "use all", "try all",
                  "test all", "다 돌려")


def _is_multi_step_request(text: str) -> bool:
    return any(kw in text for kw in _MULTI_STEP_KW)


def _is_all_tools_request(text: str) -> bool:
    return any(kw in text for kw in _ALL_TOOLS_KW)


def _build_all_tools_plan(session_info: dict) -> list:
    """Build programmatic plan to test all available tools."""
    steps = [
        {"tool": "shell", "cmd": "echo '=== System ===' && uname -a && echo '' && free -h | head -3",
         "desc": "Shell: 시스템 정보"},
        {"tool": "search", "query": "latest technology news 2026",
         "desc": "Search: 웹 검색"},
        {"tool": "code", "lang": "python",
         "code": "import math\nresults = []\nfor i in range(1, 11):\n    results.append(str(i) + '! = ' + str(math.factorial(i)))\nprint('\\n'.join(results))",
         "desc": "Code: 팩토리얼 계산"},
        {"tool": "memory_save", "text": "tool demo: all tools working at " + time.strftime("%H:%M"),
         "desc": "Memory: 기억 저장"},
        {"tool": "memory_find", "text": "tool demo",
         "desc": "Memory: 기억 검색"},
        {"tool": "file_write", "path": "work/tool_demo.txt",
         "content": "Machina Tool Demo\nAll tools operational!",
         "desc": "File: 파일 쓰기"},
        {"tool": "file_read", "path": "work/tool_demo.txt",
         "desc": "File: 파일 읽기"},
        {"tool": "file_list", "path": "work",
         "desc": "File: 목록 조회"},
        {"tool": "util_list",
         "desc": "Util: 유틸리티 목록"},
    ]
    # Add MCP tools dynamically if connected
    try:
        from machina_mcp import mcp_manager
        if mcp_manager.is_started:
            for sname, conn in mcp_manager.servers.items():
                for tname, tinfo in conn.tools.items():
                    schema = tinfo.get("inputSchema", {}) or {}
                    sample_args = _build_mcp_sample_args(schema)
                    required = schema.get("required", []) or []
                    missing_required = [k for k in required if k not in sample_args]
                    if missing_required:
                        logger.info(
                            "Skip MCP plan step (insufficient sample args): %s.%s missing=%s",
                            sname, tname, missing_required[:3],
                        )
                        continue
                    steps.append({
                        "tool": "mcp",
                        "mcp_server": sname,
                        "mcp_tool": tname,
                        "args": sample_args,
                        "desc": f"MCP: {tname} ({sname})",
                    })
    except Exception: pass
    return steps


def _sample_mcp_value(param_name: str, prop_info: dict):
    """Best-effort sample value for MCP schema property."""
    ptype = (prop_info or {}).get("type", "string")
    n = (param_name or "").strip().lower()
    if ptype == "boolean":
        return True
    if ptype in ("integer", "number"):
        return 1
    if ptype == "array":
        return []
    if ptype == "object":
        return {}
    # string/default fallback
    if any(k in n for k in ("url", "uri", "link", "href", "website")):
        return "https://example.com"
    if any(k in n for k in ("search_query", "query", "keyword", "kw", "q", "question")):
        return "latest technology news 2026"
    if any(k in n for k in ("text", "prompt", "content", "message", "input")):
        return "test message"
    if any(k in n for k in ("lang", "locale", "language")):
        return "en"
    return "test"


def _build_mcp_sample_args(schema: dict) -> dict:
    """Build robust sample args from MCP inputSchema.

    Preference:
    1) fill all required fields first
    2) then fill up to 2 optional fields
    """
    props = (schema or {}).get("properties", {}) or {}
    required = (schema or {}).get("required", []) or []
    out = {}
    for name in required:
        if name in props:
            out[name] = _sample_mcp_value(name, props.get(name, {}))
    optional_filled = 0
    for name, info in props.items():
        if name in out:
            continue
        if optional_filled >= 2:
            break
        out[name] = _sample_mcp_value(name, info or {})
        optional_filled += 1
    return out


def _validate_continuation_actions(actions: list) -> bool:
    """Check continuation action has required fields (cmd for shell, code for code)."""
    if not actions:
        return False
    for a in actions:
        aid = str(a.get("aid", "")).upper()
        inp = a.get("inputs", {})
        if "SHELL" in aid:
            cmd = inp.get("cmd")
            if isinstance(cmd, str):
                if not cmd.strip():
                    return False
            elif isinstance(cmd, list):
                # Empty list or all-empty tokens are invalid.
                if not cmd or not any(str(x).strip() for x in cmd):
                    return False
            else:
                if not cmd:
                    return False
        if "CODE" in aid and not str(inp.get("code", "")).strip():
            return False
    return True


def _step_to_intent(step: dict) -> dict:
    """Convert a plan step dict to an executable intent."""
    from policies.chat_intent_map import _intent_to_machina_action
    raw = {"type": "run", **{k: v for k, v in step.items() if k != "desc"}}
    # MCP convenience: allow plan step to pass top-level query/url/text and coerce into args.
    if raw.get("tool") == "mcp":
        args = raw.get("args", {})
        if not isinstance(args, dict):
            args = {}
        for k in ("search_query", "query", "url", "text", "keyword"):
            if k in raw and k not in args:
                args[k] = raw[k]
        raw["args"] = args
    return _intent_to_machina_action(raw)


def _coerce_response(response) -> str:
    """Coerce non-string LLM response to string."""
    if not isinstance(response, str):
        if isinstance(response, dict):
            return response.get("content", "") or json.dumps(response, ensure_ascii=False)
        elif isinstance(response, list):
            return "\n".join(str(r) for r in response)
        else:
            return str(response)
    return response


def _extract_embedded_action(response: str) -> tuple:
    """Extract embedded action JSON from a reply string.

    Returns (embedded_action_dict, prefix_text) if found, else (None, "").
    The embedded_action_dict is a valid intent with type="action" and actions list.
    """
    action_idx = response.find('"type"')
    if action_idx < 0:
        action_idx = response.find("'type'")
    if action_idx < 0:
        return None, ""
    brace_start = response.rfind("{", 0, action_idx + 1)
    if brace_start < 0:
        return None, ""
    depth, end = 0, brace_start
    for i in range(brace_start, len(response)):
        if response[i] == "{":
            depth += 1
        elif response[i] == "}":
            depth -= 1
        if depth == 0:
            end = i + 1
            break
    json_str = response[brace_start:end]
    if not json_str:
        return None, ""
    try:
        embedded = json.loads(json_str)
        if embedded.get("type") == "action" and embedded.get("actions"):
            prefix = response[:brace_start].strip()
            return embedded, prefix
    except (json.JSONDecodeError, TypeError):
        pass
    return None, ""


def _unwrap_json_response(response: str) -> str:
    """If response is a JSON wrapper like {"content": "..."}, unwrap it."""
    if response and response.strip().startswith("{"):
        try:
            inner = json.loads(response)
            if isinstance(inner, dict) and "content" in inner:
                return inner["content"]
        except (json.JSONDecodeError, TypeError):
            pass
    return response


async def _handle_blocked_code_approval(
    cycle_result: str, intent: dict, user_text: str,
    chat_id: int, context,
    pending_approvals: dict, approval_timeout: int,
    session_approved_aids: set,
) -> str:
    """Handle BLOCKED_PATTERN_ASK / NETWORK_CODE_ASK: show code preview, ask user.

    Returns the (possibly re-executed) cycle_result.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from machina_dispatch import execute_intent

    marker, patterns = cycle_result.split(":", 1)
    is_blocked = marker == "BLOCKED_PATTERN_ASK"
    is_net = marker == "NETWORK_CODE_ASK"
    code_full = (intent.get("actions", [{}])[0]
                 .get("inputs", {}).get("code", ""))
    # Build readable approval message with code preview
    label = "⚠️ 코드 실행 승인 요청"
    detail_lines = [f"감지: {patterns}"]
    if code_full:
        preview = code_full[:500]
        if len(code_full) > 500:
            preview += "\n..."
        detail_lines.append(f"```\n{preview}\n```")
    msg_text = f"{label}\n" + "\n".join(detail_lines) + "\n\n허용하시겠습니까?"
    # Send approval with inline keyboard (short IDs for 64-byte limit)
    _short_ts = hex(int(time.time() * 100) % 0xFFFFFFFF)[2:]
    approval_id = f"p{_short_ts}"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("허용", callback_data=f"a:{approval_id}"),
            InlineKeyboardButton("거부", callback_data=f"d:{approval_id}"),
            InlineKeyboardButton("항상허용", callback_data=f"A:{approval_id}"),
        ]
    ])
    event = asyncio.Event()
    pending_approvals[approval_id] = {
        "event": event, "approved": False, "aid": "CODE.EXEC"}
    try:
        await context.bot.send_message(
            chat_id=chat_id, text=msg_text, reply_markup=keyboard)
    except Exception:
        await context.bot.send_message(
            chat_id=chat_id, text=msg_text.replace("```", ""),
            reply_markup=keyboard)
    try:
        await asyncio.wait_for(event.wait(), timeout=approval_timeout)
    except asyncio.TimeoutError:
        await context.bot.send_message(
            chat_id=chat_id, text="⏰ 시간 초과 — 코드 실행 거부됨.")
        pending_approvals.pop(approval_id, None)
        return "시간 초과로 코드 실행이 거부됐어."

    result_entry = pending_approvals.pop(approval_id, {})
    if result_entry.get("approved"):
        logger.info(f"[{chat_id}] User approved code execution "
                    f"(blocked={is_blocked}, net={is_net})")
        session_approved_aids.add("AID.CODE.EXEC.v1")
        cycle_result = await asyncio.to_thread(
            execute_intent, intent, user_text,
            force_code=True, allow_net=True)
        return cycle_result
    else:
        logger.info(f"[{chat_id}] User denied code execution")
        return "사용자가 코드 실행을 거부했어."
