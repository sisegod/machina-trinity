"""Machina Telegram Bot --- Pulse Loop (handle_message).

The main autonomous execution loop. Extracted from telegram_bot.py for
maintainability. All shared state is imported from telegram_bot.py.
"""

import asyncio
import hashlib
import json
import logging
import os
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from machina_shared import (
    _jsonl_append,
    MEM_DIR,
    get_active_backend,
    get_brain_label,
    is_auto_route_enabled,
)
from machina_learning import (
    experience_record,
    skill_search,
    wisdom_retrieve,
    memory_search_recent,
)
from machina_dispatch import execute_intent
from policies.chat_driver import track_dialogue_state, extract_entities
from policies.chat_driver_util import resolve_intent_fast

from telegram_bot_handlers import (
    _compute_complexity,
    _detect_memorable_facts,
    _check_action_permissions,
    _is_multi_step_request,
    _is_all_tools_request,
    _build_all_tools_plan,
    _step_to_intent,
    _handle_blocked_code_approval,
    _coerce_response,
    _extract_embedded_action,
    _unwrap_json_response,
    _validate_continuation_actions,
)

logger = logging.getLogger(__name__)


async def _advance_step_queue(step_queue: list, all_cycle_results: list,
                              cycle_result: str, last_sent_result: str,
                              update: Update, _bot, chat_id: int,
                              log_prefix: str = "Plan step") -> tuple:
    """Pop next step from queue and prepare it for execution.

    Returns (next_intent, last_sent_result) if a valid step was popped,
    or (None, last_sent_result) if no valid step available.
    """
    if not step_queue:
        return None, last_sent_result
    next_step = step_queue.pop(0)
    next_intent = _step_to_intent(next_step)
    if not next_intent or next_intent.get("type") != "action":
        return None, last_sent_result
    if cycle_result and cycle_result != last_sent_result:
        await _bot.send_chunked(update, cycle_result)
        last_sent_result = cycle_result
    _step_desc = next_step.get("desc", next_step.get("tool", "?"))
    _done_count = len(all_cycle_results)
    _total_count = _done_count + len(step_queue)
    logger.info(f"[{chat_id}] {log_prefix} {_done_count}/{_total_count}: {_step_desc}")
    await update.message.reply_text(
        f"▶️ [{_done_count}/{_total_count}] {_step_desc}")
    return next_intent, last_sent_result


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Machina Pulse Loop --- autonomous execution until done.

    All shared globals are imported lazily from telegram_bot to avoid
    circular import issues (telegram_bot -> telegram_bot_pulse -> telegram_bot).
    """
    # Lazy import of shared globals from telegram_bot (defined there, re-exported here)
    import telegram_bot as _bot

    if not _bot.check_chat_allowed(update.effective_chat.id):
        return

    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id
    user_text = update.message.text

    # (4a) Minimal input handling: ".", "?", single char -> treat as ping/greeting
    stripped = user_text.strip()
    if len(stripped) <= 2 and not stripped.isalnum():
        await update.message.reply_text("안녕! 뭐 도와줄까? 😊")
        _bot.save_chat_log(chat_id, "user", user_text)
        _bot.save_chat_log(chat_id, "assistant", "안녕! 뭐 도와줄까? 😊")
        return

    logger.info(f"[{chat_id}] User: {user_text[:100]}")
    # Track last active chat for alert fallback (when ALLOWED_CHAT_ID is unset)
    _bot._last_active_chat_id = chat_id
    _bot.autonomic_touch()  # Reset idle timer --- user is active
    _bot.save_chat_log(chat_id, "user", user_text)

    # Autonomous: run until done. Safety caps only.
    # DEV profile: generous limits for development/testing
    _is_dev = os.getenv("MACHINA_DEV_EXPLORE") == "1" or os.getenv("MACHINA_PROFILE", "dev") == "dev"
    MAX_CYCLES = int(os.getenv("MACHINA_MAX_CYCLES", "100" if _is_dev else "30"))
    TOTAL_BUDGET = int(os.getenv("MACHINA_PULSE_BUDGET_S", "3600" if _is_dev else "600"))
    _bot._pulse_cancel[chat_id] = False  # Reset cancel flag for this request
    t_start = time.time()

    def budget_remaining():
        return max(0, TOTAL_BUDGET - (time.time() - t_start))

    def budget_for_phase(base_sec):
        return max(5, min(base_sec, budget_remaining() - 5))

    # Build conversation history (locked per chat_id to prevent races)
    async with _bot._chat_locks[chat_id]:
        history = _bot.conversation_history[chat_id]
        if not history:
            history = _bot.load_chat_history(chat_id)
            _bot.conversation_history[chat_id] = history
        history.append({"role": "user", "content": user_text, "_ts": time.time()})
        while len(history) > _bot.MAX_HISTORY * 2:
            history.pop(0)
        history = list(history)  # snapshot for this request

    # (4d) Context chain: generate/reuse session_id for conversation flow
    if chat_id not in _bot._session_ids or time.time() - (history[-2].get("_ts", 0) if len(history) > 1 else 0) > 1800:
        _bot._session_ids[chat_id] = f"s{chat_id}_{int(time.time())}"
    session_id = _bot._session_ids[chat_id]

    memory_context = ""
    wisdom_context = ""
    greeting_words = {"안녕", "하이", "헬로", "hi", "hello", "ㅎㅇ", "ㅎㅎ"}
    is_greeting = any(w in user_text.lower() for w in greeting_words)
    if not is_greeting:
        memory_context = memory_search_recent(user_text, session_id=session_id)
        if memory_context:
            logger.info(f"[{chat_id}] Auto-recalled memory: {memory_context[:100]}")
        wisdom_context = wisdom_retrieve(user_text)
        if wisdom_context:
            logger.info(f"[{chat_id}] Wisdom injected: {wisdom_context[:100]}")

    skill_hint = ""
    if not is_greeting:
        skill_hint = skill_search(user_text, limit=2)
        if skill_hint:
            logger.info(f"[{chat_id}] Skill hint: {skill_hint[:100]}")

    # Phase C: Dialogue State Tracking + Entity Memory
    dst_state = track_dialogue_state(history, _bot._dst_states.get(chat_id))
    entities = extract_entities(user_text)
    _bot._dst_states[chat_id] = dst_state
    if dst_state.get("topic"):
        logger.info(f"[{chat_id}] DST topic={dst_state['topic']}, "
                     f"chain={dst_state.get('intent_chain', [])[-3:]}, "
                     f"turns={dst_state.get('turn_count', 0)}")
    if any(entities.get(k) for k in ("files", "urls", "numbers", "names")):
        logger.info(f"[{chat_id}] Entities: {json.dumps(entities, ensure_ascii=False)[:150]}")

    cur_backend = get_active_backend()
    cur_brain = get_brain_label()

    # --- Auto-routing: complexity-based backend upgrade ---
    _routed_backend = None  # tracks if we temporarily switched
    if is_auto_route_enabled() and not is_greeting:
        complexity = _compute_complexity(user_text, history)
        _bot._auto_route_stats["total_scored"] += 1
        is_local = cur_backend in ("oai_compat", "ollama")
        has_claude = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

        if complexity >= 0.6 and is_local and has_claude:
            # Upgrade: local -> Claude for this complex request
            # Thread-safe: use per-chat override instead of mutating os.environ
            _routed_backend = cur_backend  # save original to restore later
            with _bot._backend_override_lock:
                _bot._backend_override[chat_id] = "anthropic"
            cur_backend = "anthropic"
            cur_brain = get_brain_label()
            _bot._auto_route_stats["routed_to_claude"] += 1
            logger.info(f"[{chat_id}] Auto-route: UPGRADE to Claude "
                        f"(complexity={complexity:.2f}, threshold=0.6)")
        else:
            _bot._auto_route_stats["stayed_local"] += 1
            if complexity < 0.3 and cur_backend == "anthropic":
                logger.info(f"[{chat_id}] Auto-route: simple query on Claude "
                            f"(complexity={complexity:.2f}) --- keeping Claude "
                            f"(no auto-downgrade)")

    # MCP tools prompt injection (if MCP is started)
    _mcp_tools_desc = ""
    try:
        from machina_mcp import mcp_manager
        if mcp_manager.is_started and mcp_manager.tool_count > 0:
            _mcp_tools_desc = mcp_manager.get_tool_list_for_prompt(max_tools=15)
    except Exception as e: logger.debug(f"MCP tools prompt: {type(e).__name__}: {e}")

    session_info = {
        "platform": "telegram",
        "language": "korean",
        "current_brain": cur_brain,
        "current_backend": cur_backend,
        "memory_context": memory_context if memory_context else "없음",
        "wisdom": wisdom_context if wisdom_context else "",
        "skill_hint": skill_hint if skill_hint else "",
        "dst_state": dst_state,
        "entities": entities,
        "mcp_tools": _mcp_tools_desc,
        "capabilities": ["대화", "코딩(Python/Bash/C++)", "시스템(shell/파일/GPU)",
                         "조사(웹검색/URL)", "기억(저장/검색)", "Genesis(C++도구)", "유틸리티",
                         "MCP(외부도구)"] if _mcp_tools_desc else
                        ["대화", "코딩(Python/Bash/C++)", "시스템(shell/파일/GPU)",
                         "조사(웹검색/URL)", "기억(저장/검색)", "Genesis(C++도구)", "유틸리티"],
    }
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception as e: logger.debug(f"Typing indicator: {type(e).__name__}: {e}")
    fp = resolve_intent_fast(user_text)
    intent = fp if fp else await asyncio.to_thread(
        _bot.call_chat_driver, "intent", history,
        timeout_sec=int(budget_for_phase(25)), session=session_info)
    if fp:
        logger.info(f"[{chat_id}] FastPath: {fp.get('_fast_path','')}")
    logger.info(f"[{chat_id}] Intent: {json.dumps(intent, ensure_ascii=False)[:200]}")

    intent_type = intent.get("type", "")
    response = ""
    last_sent_result = None

    if intent_type == "reply":
        response = _coerce_response(intent.get("content", ""))
        if response:
            embedded, prefix = _extract_embedded_action(response)
            if embedded:
                logger.info(f"[{chat_id}] Self-correction: extracted embedded action from reply")
                if prefix:
                    await update.message.reply_text(prefix)
                intent = embedded
                intent_type = "action"
            if intent_type != "action":
                response = _unwrap_json_response(response)
                if not response or not response.strip():
                    msgs_with_memory = list(history)
                    if memory_context:
                        msgs_with_memory.insert(0, {
                            "role": "system",
                            "content": f"[과거 기억]\n{memory_context}"
                        })
                    response = await asyncio.to_thread(_bot.call_llm, msgs_with_memory)

    elif intent_type == "config":
        from machina_shared import save_runtime_config
        changes = intent.get("changes", [])
        applied = []
        CONFIG_ALLOWLIST = {
            "OAI_COMPAT_BASE_URL", "OAI_COMPAT_MODEL", "OAI_COMPAT_API_KEY",
            "MACHINA_CHAT_BACKEND", "MACHINA_CHAT_TEMPERATURE", "MACHINA_CHAT_MAX_TOKENS",
            "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "MACHINA_SELECTOR",
            "MACHINA_PERMISSION_MODE", "MACHINA_PERMISSION_OVERRIDES",
        }
        for change in changes:
            key = change.get("key", "")
            value = change.get("value", "")
            if key in CONFIG_ALLOWLIST and value:
                os.environ[key] = value
                applied.append(f"{key}={value[:20]}{'...' if len(value)>20 else ''}")
                logger.info(f"[{chat_id}] Config changed: {key}={value[:20]}")
        if applied:
            save_runtime_config()  # Persist config change to survive restart
            response = intent.get("content", "") + f"\n✅ 변경됨: {', '.join(applied)}"
        else:
            response = intent.get("content", "설정 변경할 게 없어.")

    elif intent_type != "action":
        logger.warning(f"[{chat_id}] chat_driver returned empty/unknown (type={intent_type}), fallback to direct LLM")
        recent_msgs = history[-12:] if len(history) > 12 else list(history)
        msgs_with_memory = list(recent_msgs)
        if memory_context:
            msgs_with_memory.insert(0, {
                "role": "system",
                "content": f"[과거 기억]\n{memory_context[:500]}"
            })
        response = await asyncio.to_thread(_bot.call_llm, msgs_with_memory)

    if intent_type == "action":
        # -- Plan Phase: multi-step detection -> step queue --
        _step_queue: list = []
        if _is_multi_step_request(user_text):
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception as e: logger.debug(f"Typing indicator: {type(e).__name__}: {e}")
            if _is_all_tools_request(user_text):
                _step_queue = _build_all_tools_plan(session_info)
                logger.info(f"[{chat_id}] Plan: all-tools ({len(_step_queue)} steps)")
            else:
                plan_result = await asyncio.to_thread(
                    _bot.call_chat_driver, "plan", history,
                    timeout_sec=30, session=session_info)
                if plan_result.get("type") == "plan" and plan_result.get("steps"):
                    _step_queue = plan_result["steps"]
                    logger.info(f"[{chat_id}] Plan: LLM ({len(_step_queue)} steps)")
            if _step_queue:
                plan_text = f"📋 {len(_step_queue)}단계 실행 계획:\n"
                plan_text += "\n".join(
                    f"  {i+1}. {s.get('desc', s.get('tool', '?'))}"
                    for i, s in enumerate(_step_queue))
                await update.message.reply_text(plan_text)
                # Replace initial intent with first planned step
                first_step = _step_queue.pop(0)
                first_intent = _step_to_intent(first_step)
                if first_intent and first_intent.get("type") == "action":
                    intent = first_intent

        all_cycle_results = []
        _initial_single_action = bool(
            intent_type == "action"
            and len(intent.get("actions", [])) == 1
            and not intent.get("_next")
        )
        _prev_tool = None  # duplicate detection
        _same_tool_count = 0
        _consecutive_errors = 0  # error self-repair tracking
        _session_approved_aids: set = set()  # AIDs approved in this pulse session
        _used_tools: list = []  # track tools used so far for multi-step awareness
        _empty_recovery_count = 0
        _repair_rounds = 0
        _max_empty_recovery = max(
            0, min(5, int(os.getenv("MACHINA_PULSE_EMPTY_RECOVERY_MAX", "2"))))
        _max_repair_rounds = max(
            0, min(5, int(os.getenv("MACHINA_PULSE_REPAIR_ROUNDS", "2"))))
        _continue_on_step_error = os.getenv(
            "MACHINA_PLAN_CONTINUE_ON_STEP_ERROR", "1"
        ).lower() in ("1", "true", "yes", "on")

        for cycle in range(MAX_CYCLES):
            # -- Guard checks --
            if budget_remaining() < 10:
                logger.info(f"[{chat_id}] Pulse: budget exhausted at cycle {cycle}")
                if all_cycle_results:
                    response = all_cycle_results[-1]
                break

            # User interrupt: /stop command
            if _bot._pulse_cancel.get(chat_id):
                logger.info(f"[{chat_id}] Pulse: user cancelled at cycle {cycle}")
                _bot._pulse_cancel[chat_id] = False
                await update.message.reply_text(f"작업 중단 (사이클 {cycle+1}에서 멈춤)")
                if all_cycle_results:
                    response = all_cycle_results[-1]
                break

            # Permission pre-check for action tools (skip already-approved in this session)
            if intent.get("actions"):
                # Validate current intent actions BEFORE execution to prevent
                # empty/invalid shell/code calls from entering dead-end loops.
                if not _validate_continuation_actions(intent.get("actions", [])):
                    logger.info(f"[{chat_id}] Pulse: invalid current action payload, stopping safely")
                    response = all_cycle_results[-1] if all_cycle_results else "작업 명령이 비어 있어서 중단했어. 구체적으로 다시 요청해줘."
                    break
                actions_to_check = []
                auto_approved = []
                for a in intent["actions"]:
                    aid = a.get("aid", "")
                    if aid in _session_approved_aids:
                        auto_approved.append(a)
                    else:
                        actions_to_check.append(a)
                newly_approved = await _check_action_permissions(
                    actions_to_check, chat_id, context,
                    _bot._pending_approvals, _bot.APPROVAL_TIMEOUT) if actions_to_check else []
                for a in newly_approved:
                    _session_approved_aids.add(a.get("aid", ""))
                approved_actions = auto_approved + newly_approved
                if not approved_actions:
                    response = "모든 작업이 거부되었어."
                    break
                intent["actions"] = approved_actions

            # Show "executing" message AFTER permission check (not before — avoids button confusion)
            if cycle == 0:
                prefix = intent.get("assistant_prefix", "작업 실행 중... ⏳")
                await update.message.reply_text(prefix if prefix else "작업 실행 중... ⏳")
            logger.info(f"[{chat_id}] Pulse cycle {cycle+1}/{MAX_CYCLES}")
            cur_tool = (intent.get("actions", [{}])[0].get("aid", "")
                        if intent.get("actions") else "")
            # If CODE.EXEC was already approved in this session, skip blocklist check
            _code_approved = any(a == "AID.CODE.EXEC.v1"
                                 for a in _session_approved_aids)
            cycle_result = await asyncio.to_thread(
                execute_intent, intent, user_text,
                force_code=_code_approved, allow_net=_code_approved)

            # Blocked/Network code -> show code preview -> ask user -> re-execute
            if cycle_result and (cycle_result.startswith("BLOCKED_PATTERN_ASK:")
                                 or cycle_result.startswith("NETWORK_CODE_ASK:")):
                cycle_result = await _handle_blocked_code_approval(
                    cycle_result, intent, user_text,
                    chat_id, context,
                    _bot._pending_approvals, _bot.APPROVAL_TIMEOUT,
                    _session_approved_aids,
                )

            # Guard: if dispatch returned "no command"/empty, try recovery before done
            if cycle_result and "no command" in cycle_result.lower():
                if _empty_recovery_count < _max_empty_recovery and budget_remaining() > 15:
                    _empty_recovery_count += 1
                    logger.info(
                        f"[{chat_id}] Pulse: empty command result, attempting recovery "
                        f"({_empty_recovery_count}/{_max_empty_recovery})")
                    _continue_session = dict(session_info)
                    _continue_session["used_tools"] = _used_tools
                    _continue_session["cycle_num"] = cycle + 1
                    _continue_session["failed_tool"] = cur_tool
                    _continue_session["repair_required"] = True
                    _continue_session["avoid_tools"] = [cur_tool] if cur_tool else []
                    recovery_obs = (
                        (cycle_result or "")
                        + "\n[REPAIR_REQUIRED] Previous action returned empty/no command. "
                          "Choose a different valid next action and continue.")
                    continue_result = await asyncio.to_thread(
                        _bot.call_chat_driver,
                        "continue", history,
                        timeout_sec=int(budget_for_phase(20)),
                        observation=recovery_obs,
                        session=_continue_session,
                    )
                    if continue_result.get("type") == "action" and _validate_continuation_actions(continue_result.get("actions", [])):
                        intent = continue_result
                        continue
                logger.info(f"[{chat_id}] Pulse: empty command result, forcing done")
                if all_cycle_results:
                    response = all_cycle_results[-1]
                else:
                    response = cycle_result
                break
            _empty_recovery_count = 0
            all_cycle_results.append(cycle_result)

            # -- Track used tools for multi-step awareness --
            if cur_tool and cur_tool not in _used_tools:
                _used_tools.append(cur_tool)
            # -- Duplicate detection: same tool 3x in a row -> force done --
            # Skip duplicate check when step queue is driving (each step is pre-planned)
            if cur_tool == _prev_tool:
                _same_tool_count += 1
            else:
                _same_tool_count = 1
                _prev_tool = cur_tool
            if _same_tool_count >= 3 and not _step_queue:
                logger.info(f"[{chat_id}] Pulse: duplicate tool 3x, forcing done")
                response = cycle_result
                break
            # Send intermediate result
            if cycle > 0 and cycle_result and cycle_result != last_sent_result:
                await _bot.send_chunked(update, cycle_result)
                last_sent_result = cycle_result

            # -- Error detection -> self-repair (not stop!) --
            result_lower = (cycle_result or "").lower()[:500]
            has_error = ("error" in result_lower or "failed" in result_lower
                         or "traceback" in result_lower)
            if has_error:
                _consecutive_errors += 1
                # 5 consecutive errors -> give up
                if _consecutive_errors >= 5:
                    logger.info(f"[{chat_id}] Pulse: 5 consecutive errors, stopping")
                    response = cycle_result
                    break
                # Otherwise: feed error to LLM for self-repair (skip _next chain)
                logger.info(f"[{chat_id}] Error detected, attempting self-repair "
                            f"(attempt {_consecutive_errors}/5)")
                # Planned step execution: continue to next step on single-step failure
                # to avoid getting stuck in repair loops at N/(N+1).
                if _step_queue and _continue_on_step_error:
                    if cycle_result and cycle_result != last_sent_result:
                        await _bot.send_chunked(update, cycle_result)
                        last_sent_result = cycle_result
                    _next_i, last_sent_result = await _advance_step_queue(
                        _step_queue, all_cycle_results, "", last_sent_result,
                        update, _bot, chat_id, "Plan step error-skip -> next")
                    if _next_i:
                        intent = _next_i
                        continue
            else:
                _consecutive_errors = 0

            # -- Continuation: Marker -> Heuristic -> LLM fallback --
            # (1) _next marker: pre-planned next step -> skip LLM call
            #     But skip if step_queue is driving (plan takes priority)
            next_marker = intent.get("_next") if not _step_queue else None
            # Validate _next has required fields (cmd for shell, code for code)
            if next_marker and isinstance(next_marker, dict):
                _nt = next_marker.get("tool", "")
                if _nt in ("shell",) and not next_marker.get("cmd"):
                    next_marker = None  # Drop invalid _next
                elif _nt in ("code",) and not next_marker.get("code"):
                    next_marker = None
            if next_marker and isinstance(next_marker, dict) and not has_error:
                logger.info(f"[{chat_id}] Chain: _next={next_marker.get('tool', '?')}")
                from policies.chat_intent_map import _intent_to_machina_action
                chain_intent = _intent_to_machina_action(
                    {"type": "run", **{k: v for k, v in next_marker.items()
                                       if k != "_next"}})
                if chain_intent.get("type") == "action":
                    await _bot.send_chunked(update, cycle_result)
                    last_sent_result = cycle_result
                    if next_marker.get("_next"):
                        chain_intent["_next"] = next_marker["_next"]
                    intent = chain_intent
                    np = chain_intent.get("assistant_prefix", "")
                    if np:
                        await update.message.reply_text(np)
                    continue

            # (1.5) Step queue: planned steps -> skip heuristic + LLM continue
            if _step_queue and not has_error:
                _next_i, last_sent_result = await _advance_step_queue(
                    _step_queue, all_cycle_results, cycle_result, last_sent_result,
                    update, _bot, chat_id, "Plan step")
                if _next_i:
                    intent = _next_i
                    continue

            # (2) Heuristic: success + no more chain + single-step request -> done
            _is_multi_step = any(kw in user_text for kw in (
                "하나씩", "다 ", "전부", "모두", "순서대로", "차례대로",
                "all ", "each", "every", "one by one"))
            # For single-action intents (fast-path/simple commands), end on first
            # successful result unless explicit multi-step markers exist.
            if (cycle == 0 and _initial_single_action and not has_error
                    and not next_marker and not _is_multi_step and not _step_queue):
                logger.info(f"[{chat_id}] Heuristic: single-action success, done")
                response = cycle_result
                break
            if not has_error and not next_marker and not _is_multi_step and not _step_queue:
                # Strict patterns only: JSON ok field or explicit test results
                if any(p in result_lower for p in ('"ok": true', '"ok":true',
                        'all ok', 'all pass')):
                    logger.info(f"[{chat_id}] Heuristic: success, done")
                    response = cycle_result
                    break

            # (3) LLM continue: uncertain or error -> ask LLM
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception as e: logger.debug(f"Typing indicator: {type(e).__name__}: {e}")
            # Build continue context: used tools + available tools
            _continue_session = dict(session_info)
            _continue_session["used_tools"] = _used_tools
            _continue_session["cycle_num"] = cycle + 1
            continue_result = await asyncio.to_thread(
                _bot.call_chat_driver,
                "continue", history,
                timeout_sec=int(budget_for_phase(25)),
                observation=(cycle_result or ""),
                session=_continue_session,
            )

            if continue_result.get("type") == "done":
                # Override done when step queue has remaining items
                if _step_queue:
                    _next_i, last_sent_result = await _advance_step_queue(
                        _step_queue, all_cycle_results, cycle_result, last_sent_result,
                        update, _bot, chat_id, "Plan override done -> step")
                    if _next_i:
                        intent = _next_i
                        continue
                if has_error and _repair_rounds < _max_repair_rounds and budget_remaining() > 15:
                    _repair_rounds += 1
                    logger.info(
                        f"[{chat_id}] Continue requested done after error, forcing repair action "
                        f"({_repair_rounds}/{_max_repair_rounds})")
                    _continue_session = dict(session_info)
                    _continue_session["used_tools"] = _used_tools
                    _continue_session["cycle_num"] = cycle + 1
                    _continue_session["failed_tool"] = cur_tool
                    _continue_session["repair_required"] = True
                    _continue_session["avoid_tools"] = [cur_tool] if cur_tool else []
                    repair_obs = (
                        (cycle_result or "")
                        + "\n[REPAIR_REQUIRED] Error detected. Do not finish yet. "
                          "Provide one concrete recovery action different from the failed tool.")
                    repair_result = await asyncio.to_thread(
                        _bot.call_chat_driver,
                        "continue", history,
                        timeout_sec=int(budget_for_phase(20)),
                        observation=repair_obs,
                        session=_continue_session,
                    )
                    if repair_result.get("type") == "action" and _validate_continuation_actions(repair_result.get("actions", [])):
                        intent = repair_result
                        continue
                final_summary = continue_result.get("content", "")
                response = final_summary if final_summary else cycle_result
                break

            elif continue_result.get("type") == "continue_signal":
                # Claude wanted to continue but JSON failed --- use step queue if available
                if _step_queue:
                    _next_i, last_sent_result = await _advance_step_queue(
                        _step_queue, all_cycle_results, cycle_result, last_sent_result,
                        update, _bot, chat_id, "continue_signal -> plan step")
                    if _next_i:
                        intent = _next_i
                        continue
                # No step queue --- treat as done
                response = cycle_result
                break

            elif continue_result.get("type") == "action":
                # Validate continuation action has required fields before executing
                if not _validate_continuation_actions(continue_result.get("actions", [])):
                    logger.info(f"[{chat_id}] Continue: invalid action (missing required fields), forcing done")
                    response = cycle_result
                    break
                if cycle_result and cycle_result != last_sent_result:
                    await _bot.send_chunked(update, cycle_result)
                    last_sent_result = cycle_result
                intent = continue_result
                next_prefix = intent.get("assistant_prefix", "")
                if next_prefix:
                    await update.message.reply_text(next_prefix)
                continue
            else:
                response = cycle_result
                break

        if not response and all_cycle_results:
            response = all_cycle_results[-1]

    if not response or response.strip() in ("(no output)", "(출력 없음)", ""):
        try:  # Fallback: empty → conversational LLM
            response = await asyncio.to_thread(_bot.call_llm, history[-8:])
        except Exception as e:
            logger.warning(f"[{chat_id}] Fallback LLM failed: {type(e).__name__}: {e}")
        if not response or not response.strip():
            response = "작업을 처리하지 못했어. 다시 시도해줘."
    # --- Auto-routing: restore original backend after temporary upgrade ---
    if _routed_backend is not None:
        with _bot._backend_override_lock:
            _bot._backend_override.pop(chat_id, None)
        logger.info(f"[{chat_id}] Auto-route: restored backend to {_routed_backend}")
        # Prepend routing indicator to response for transparency
        route_model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
        response = f"[Claude {route_model}]\n{response}"

    # Auto-memory: detect memorable facts from user message (reply-type only)
    if intent_type in ("reply", "", "config") and not is_greeting and budget_remaining() > 15:
        try:
            auto_facts = await asyncio.to_thread(_detect_memorable_facts, user_text)
            if auto_facts:
                from machina_learning import memory_save
                for fact in auto_facts[:3]:
                    fact_hash = hashlib.sha256(fact.encode()).hexdigest()
                    if fact_hash in _bot._auto_memory_seen:
                        continue
                    _bot._auto_memory_seen.add(fact_hash)
                    if len(_bot._auto_memory_seen) > 10000:
                        _bot._auto_memory_seen.clear()  # prevent unbounded growth (threshold raised from 5000)
                    memory_save(fact, stream="auto_memory", session_id=session_id)
                    # Graph Memory: also ingest auto-detected facts
                    try:
                        from machina_graph import graph_ingest
                        graph_ingest(fact, metadata={"source": "auto_memory"})
                    except Exception as e: logger.debug(f"Graph ingest auto_memory: {type(e).__name__}: {e}")
                    logger.info(f"[{chat_id}] Auto-memorized: {fact[:60]}")
        except Exception as e:
            logger.debug(f"Auto-memory detection error: {e}")

    async with _bot._chat_locks[chat_id]:
        _bot.conversation_history[chat_id].append({"role": "assistant", "content": response})
    _bot.save_chat_log(chat_id, "assistant", response)

    try:
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        mem_entry = {
            "ts_ms": int(time.time() * 1000),
            "event": "telegram_conversation",
            "text": f"User: {user_text[:500]} | Bot: {response[:500]}",
            "session_id": session_id,
        }
        _jsonl_append(MEM_DIR / "telegram.jsonl", mem_entry)
        # Graph Memory: ingest user message for entity/relation extraction
        try:
            from machina_graph import graph_ingest
            graph_ingest(user_text, metadata={"source": "telegram_user"})
        except Exception as e: logger.debug(f"Graph ingest telegram_user: {type(e).__name__}: {e}")
    except Exception as e:
        logger.error(f"Memory auto-save error: {e}")

    elapsed = time.time() - t_start
    # Defensive: coerce response to string (LLM may return dict/list)
    response = _coerce_response(response) if response else ""
    _fail_markers = ("처리하지 못했", "파싱 실패", "연결에 문제가", "LLM 연결에 문제")
    success = bool(response and response.strip()
                   and not any(m in response for m in _fail_markers))
    experience_record(user_text, intent, response, success, elapsed)
    logger.info(f"[{chat_id}] Bot: {response[:100]} ({elapsed:.1f}s)")
    if response != last_sent_result:
        await _bot.send_chunked(update, response)
