"""Machina Autonomic Engine — Self-Questioning (SQ) module.

Extracted from _engine.py for maintainability. All functions take an
`engine` (AutonomicEngine instance) as their first argument. The engine
class delegates via thin wrappers::

    def _llm_self_question(self):
        return llm_self_question(self)
"""

import json
import random
import time
from pathlib import Path

from machina_shared import (
    _jsonl_append, _jsonl_read, _call_engine_llm,
    _load_manifest_tools_full,
    MACHINA_ROOT, MEM_DIR,
    EXPERIENCE_STREAM, INSIGHTS_STREAM, SKILLS_STREAM,
)
from machina_autonomic._constants import (
    KNOWLEDGE_STREAM, _audit_log, logger,
)
from machina_autonomic._autoapprove import sq_auto_approved_tool
from machina_autonomic._web import DDGS, _deep_web_search

# ASK-permission tools: blocked in autonomous SQ (require human approval)
_SQ_BLOCKED_TOOLS = frozenset({
    "shell_exec", "shell", "code_exec", "execute_code",
    "file_delete", "http_get", "genesis_compile",
    "genesis_load", "project_build", "pip_install", "pip_uninstall",
})

_SQ_NOOP_MARKERS = (
    "이미 24시간 내 학습됨",
    "이미 학습됨",
    "dedup skip",
    "검색어 없음",
    "알 수 없는 액션",
    "차단 (ask 권한 필요)",
    "requires approval",
    "자율 모드에서",
    "빈 결과",
    "no output",
    "no such file or directory",
    "파일 없음",
)


def _is_meaningful_sq_result(action: str, result: dict) -> bool:
    """True only if SQ produced a non-trivial, reusable outcome."""
    if not isinstance(result, dict) or not result.get("success"):
        return False
    detail = str(result.get("detail", "") or "")
    detail_l = detail.lower()
    if any(m.lower() in detail_l for m in _SQ_NOOP_MARKERS):
        return False
    # Search that only confirms "already known" is not meaningful progress.
    if action == "search" and ("already" in detail_l and "learn" in detail_l):
        return False
    return True

# ---------------------------------------------------------------------------
# Main SQ loop
# ---------------------------------------------------------------------------

def llm_self_question(engine):
    """Ask the LLM what to do next, then execute it."""
    t0 = time.time()

    if engine._SQ_CONSECUTIVE_DEDUP >= 3:
        engine._sq_count = 999
        return

    # 1. Build context
    experiences = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=10)
    skills = _jsonl_read(MEM_DIR / f"{SKILLS_STREAM}.jsonl", max_lines=5)
    knowledge = _jsonl_read(MEM_DIR / f"{KNOWLEDGE_STREAM}.jsonl", max_lines=5)
    insights = _jsonl_read(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", max_lines=5)
    profile = engine._build_tool_profile()

    ctx_parts = []
    if experiences:
        recent_exp = [
            f"- {e.get('user_request', '?')[:40]} → "
            f"{'성공' if e.get('success') else '실패'}"
            for e in experiences[-3:]
        ]
        ctx_parts.append("최근 경험:\n" + "\n".join(recent_exp))
    if skills:
        ctx_parts.append(
            "보유 스킬: " + ", ".join(
                s.get("request", "?")[:30] for s in skills[-3:]))
    if knowledge:
        ctx_parts.append(
            "최근 학습: " + ", ".join(
                k.get("query", k.get("topic", "?"))[:30] for k in knowledge[-3:]))
    if insights:
        recent_ins = [
            f"- {i.get('topic', i.get('type', '?'))[:30]}: "
            f"{i.get('reflection', i.get('rules', '?'))[:40]}"
            for i in insights[-3:]
        ]
        ctx_parts.append("최근 인사이트 (활용 가능):\n" + "\n".join(recent_ins))
    if profile.get("hypotheses"):
        ctx_parts.append("도구 분석 가설 (우선 검증 대상):\n" +
                         "\n".join(f"- {h}" for h in profile["hypotheses"][:3]))
    if profile.get("untested"):
        names = ", ".join(t["name"] for t in profile["untested"][:5])
        ctx_parts.append(f"미사용 도구 ({len(profile['untested'])}개): {names}")
    if profile.get("high_fail"):
        hf = ", ".join(
            f"{t['name']}({t['fail_rate']:.0%})"
            for t in profile["high_fail"][:3])
        ctx_parts.append(f"고실패 도구: {hf}")
    if engine._sq_recent:
        ctx_parts.append("⚠️ 이미 한 행동 (절대 반복 금지):\n" +
                         "\n".join(f"- {a}" for a in engine._sq_recent[-8:]))

    context = "\n".join(ctx_parts) if ctx_parts else "시스템 초기 상태"

    tool_names = [t["name"] for t in profile.get("tools", [])[:12]]
    py_tool_names = ["memory_save", "memory_query", "code_exec",
                     "web_search", "file_read", "file_list"]
    all_tool_names = list(set(py_tool_names + tool_names))
    tool_list = ", ".join(all_tool_names[:15])

    # 2. Hypothesis-driven prompt
    seed_hints = []
    if profile.get("hypotheses"):
        seed_hints.append(random.choice(profile["hypotheses"])[:80])
    if profile.get("high_fail"):
        t = random.choice(profile["high_fail"])
        seed_hints.append(
            f"도구 '{t['name']}'의 실패 원인을 코드로 진단해")
    if profile.get("untested"):
        t = random.choice(profile["untested"])
        seed_hints.append(
            f"미사용 도구 '{t['name']}'를 테스트해 (입력: {', '.join(t['inputs'][:2])})")
    seed_hints.extend([
        "시스템 메모리 파일 크기와 중복률을 코드로 점검해",
        "최근 실패 경험에서 공통 패턴을 찾아 검색해",
        "보유 도구의 응답 시간을 벤치마크해",
    ])
    seed_hint = random.choice(seed_hints[:6])

    prompt = (
        f"나는 자율 학습 에이전트다. 현재 상태:\n{context}\n\n"
        f"제안: {seed_hint}\n"
        "위 상태를 기반으로 **가장 유용한** 행동 하나를 골라.\n"
        "가설: 이 행동으로 뭘 확인/개선할 수 있는지 reason에 명시.\n\n"
        "선택지:\n"
        '- {"action":"search","query":"영어 검색어","reason":"가설: ~를 확인하기 위해"}\n'
        f'- {{"action":"test_tool","tool":"도구명({tool_list} 중)","input":"테스트값","reason":"가설"}}\n'
        '- {"action":"code","code":"짧은 python 코드 (10줄 이내)","reason":"가설"}\n'
        '- {"action":"audit","tool":"도구명","test_type":"empty|max|schema|chain","reason":"가설"}\n'
        '- {"action":"reflect","topic":"주제","reason":"가설"}\n'
        "JSON만. code는 반드시 10줄 이내로."
    )

    try:
        raw = _call_engine_llm(
            prompt, system="자율 에이전트 행동 결정기. JSON만. 이전과 다른 새 행동.",
            max_tokens=1024, temperature=0.3,
            format_json=True, think=False)
        try:
            decision = json.loads(raw)
        except json.JSONDecodeError:
            # Truncated JSON recovery: try to extract action/reason before the broken part
            import re as _re
            m = _re.search(r'"action"\s*:\s*"(\w+)"', raw)
            if m:
                action_val = m.group(1)
                reason_m = _re.search(r'"reason"\s*:\s*"([^"]*)"', raw)
                query_m = _re.search(r'"query"\s*:\s*"([^"]*)"', raw)
                tool_m = _re.search(r'"tool"\s*:\s*"([^"]*)"', raw)
                topic_m = _re.search(r'"topic"\s*:\s*"([^"]*)"', raw)
                decision = {
                    "action": action_val,
                    "reason": reason_m.group(1) if reason_m else "truncated",
                    "query": query_m.group(1) if query_m else "",
                    "tool": tool_m.group(1) if tool_m else "",
                    "topic": topic_m.group(1) if topic_m else "",
                }
                # Skip code actions from truncated JSON (code would be incomplete)
                if action_val == "code":
                    logger.info("[SQ] Truncated code action — skipping (incomplete code)")
                    return
            else:
                raise
    except Exception as e:
        logger.warning(f"[SQ] LLM decision failed: {type(e).__name__}: {e} | raw={repr(raw[:200]) if 'raw' in dir() else '?'}")
        _audit_log("SQ", "self_question", f"LLM failed: {e}", success=False)
        return

    action = decision.get("action", "")
    reason = decision.get("reason", "")
    desc = f"{action}: {reason[:40]}" if reason else action

    # 3. Dedup check
    dedup_key = f"{action}:{decision.get('query', decision.get('tool', decision.get('topic', decision.get('code', '')[:20])))}"
    if dedup_key in engine._sq_recent:
        engine._SQ_CONSECUTIVE_DEDUP += 1
        _audit_log("SQ", "self_question", f"dedup skip: {dedup_key}", success=True)
        return
    engine._SQ_CONSECUTIVE_DEDUP = 0
    engine._sq_count += 1
    engine._sq_recent.append(dedup_key)
    if len(engine._sq_recent) > 20:
        engine._sq_recent.pop(0)

    # 3b. Novelty check — skip low-value questions during burst
    novelty_text = f"{action} {reason} {decision.get('query', '')} {decision.get('tool', '')} {decision.get('topic', '')}"
    novelty = engine.questioner._compute_novelty(novelty_text)
    if novelty < 0.3:
        engine.questioner._novelty_stats["skipped"] += 1
        _audit_log("SQ", "self_question",
                   f"novelty skip ({novelty:.2f}): {desc}", success=True)
        logger.info(f"[SQ] Novelty too low ({novelty:.2f}), skipping: {desc}")
        return
    elif novelty < 0.6:
        engine.questioner._novelty_stats["low"] += 1
        logger.info(f"[SQ] Low novelty ({novelty:.2f}): {desc}")
    else:
        engine.questioner._novelty_stats["high"] += 1
        logger.info(f"[SQ] High novelty ({novelty:.2f}): {desc}")

    # 4. Execute
    result = {"success": False, "detail": ""}
    try:
        if action == "search":
            query = decision.get("query", "")
            if query:
                result = sq_do_search(engine, query, reason)
            else:
                result = {"success": False, "detail": "검색어 없음"}
        elif action == "test_tool":
            tool = decision.get("tool", "")
            tool_input = decision.get("input", "")
            result = sq_do_tool_test(engine, tool, tool_input)
        elif action == "code":
            code = decision.get("code", "")
            result = sq_do_code(engine, code)
        elif action == "audit":
            tool = decision.get("tool", "")
            test_type = decision.get("test_type", "empty")
            result = sq_do_audit(engine, tool, test_type, reason)
        elif action == "reflect":
            topic = decision.get("topic", "")
            result = sq_do_reflect(engine, topic, reason)
        else:
            result = {"success": False, "detail": f"알 수 없는 액션: {action}"}
    except Exception as e:
        result = {"success": False, "detail": f"{type(e).__name__}: {e}"}

    meaningful = _is_meaningful_sq_result(action, result)
    engine._sq_last_ts = time.time()
    engine._last_action_productive = meaningful
    if meaningful:
        engine._sq_noop_streak = 0
        engine._sq_fail_streak = 0
    else:
        engine._sq_noop_streak = getattr(engine, "_sq_noop_streak", 0) + 1
        if not result.get("success", False):
            engine._sq_fail_streak = getattr(engine, "_sq_fail_streak", 0) + 1
        else:
            engine._sq_fail_streak = 0

    # 5. Record
    dur_ms = int((time.time() - t0) * 1000)
    should_record = (not result.get("success", False)) or meaningful
    if should_record:
        _jsonl_append(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", {
            "ts_ms": int(time.time() * 1000),
            "event": "self_question",
            "stream": EXPERIENCE_STREAM,
            "user_request": f"[자기질문] {desc}",
            "intent_type": action,
            "tool_used": action,
            "result_preview": str(result.get("detail", ""))[:500],
            "success": result.get("success", False),
            "elapsed_sec": dur_ms / 1000,
            "source": "autonomic_sq",
        })
    _audit_log("SQ", "self_question", f"{desc} | {result.get('detail', '')[:80]}",
               success=result.get("success", False), duration_ms=dur_ms)
    if not meaningful:
        _audit_log("SQ", "self_question_noop", f"{action} | {str(result.get('detail', ''))[:80]}",
                   success=True, duration_ms=dur_ms)
        if engine._sq_noop_streak >= 2:
            _audit_log("SQ", "self_question_backoff",
                       f"noop_streak={engine._sq_noop_streak}, fail_streak={engine._sq_fail_streak}",
                       success=True, duration_ms=dur_ms)

    status = "✅" if result.get("success") else "❌"
    detail = result.get("detail", "")[:200]
    # Silent mode: SQ results go to log only (not Telegram chat)
    # Milestone handles important outcomes; stream was noise
    suffix = " [NOOP]" if result.get("success") and not meaningful else ""
    logger.info(f"[SQ] {status} {desc}{suffix}" + (f" → {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# SQ action handlers
# ---------------------------------------------------------------------------

def sq_do_search(engine, query: str, reason: str) -> dict:
    """Self-question action: web search -> knowledge."""
    if DDGS is None:
        return {"success": False, "detail": "DDGS 미설치"}
    recent_k = _jsonl_read(MEM_DIR / f"{KNOWLEDGE_STREAM}.jsonl", max_lines=50)
    cutoff_ms = int((time.time() - 86400) * 1000)
    for k in recent_k:
        if k.get("query", "") == query and k.get("ts_ms", 0) > cutoff_ms:
            return {"success": True, "detail": f"'{query}' 이미 학습됨"}
    result = _deep_web_search(query, reason, "self_question_search")
    if result["success"]:
        engine._last_action_productive = True
    return result


def sq_do_tool_test(engine, tool: str, tool_input: str) -> dict:
    """Self-question action: test a tool via Python-level dispatch."""
    # Block ASK-permission tools in autonomous mode (no human to approve)
    tool_lower = tool.lower().replace(" ", "_").replace(".", "_")
    if tool_lower.startswith("aid_"):
        tool_lower = tool_lower[4:]
    if tool_lower in _SQ_BLOCKED_TOOLS and not sq_auto_approved_tool(tool_lower):
        return {"success": False, "detail": f"자율 정책상 '{tool}' 차단"}
    _PY_TOOLS = {
        "memory_save": lambda inp: sq_mem_save(engine, inp),
        "memory_query": lambda inp: sq_mem_query(engine, inp),
        "code_exec": lambda inp: sq_do_code(engine, inp),
        "execute_code": lambda inp: sq_do_code(engine, inp),
        "web_search": lambda inp: sq_do_search(engine, inp, "도구 테스트"),
        "file_read": lambda inp: sq_file_read(engine, inp),
        "file_list": lambda inp: sq_file_list(engine),
        "file_write": lambda inp: sq_mem_save(engine, inp),   # redirect to safe mem_save
        "shell_exec": lambda inp: sq_do_code(engine, inp),     # redirect to safe code_exec
    }
    # Normalize tool name: strip AID prefix, lowercase, underscores
    tool_key = tool.lower().replace(" ", "_").replace(".", "_")
    if tool_key.startswith("aid_"):
        tool_key = tool_key[4:]
    fn = _PY_TOOLS.get(tool_key)
    if fn:
        try:
            return fn(tool_input)
        except Exception as e:
            return {"success": False, "detail": f"{tool} 실패: {e}"}
    from machina_dispatch import run_machina_tool, TOOL_ALIASES
    aid = TOOL_ALIASES.get(tool.lower(), tool)
    try:
        r = run_machina_tool(aid, {"text": tool_input} if tool_input else {})
        ok = r.get("status") != "error" if isinstance(r, dict) else True
        preview = str(r)[:200] if r else "(빈 결과)"
        return {"success": ok, "detail": f"{aid}: {preview}"}
    except Exception as e:
        return {"success": False, "detail": f"{aid} 실패: {e}"}


def sq_mem_save(engine, text: str) -> dict:
    tag = f"sq_{int(time.time())}"
    _jsonl_append(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", {
        "ts_ms": int(time.time() * 1000), "event": "sq_mem_test",
        "stream": EXPERIENCE_STREAM, "user_request": text[:500],
        "success": True, "source": "autonomic_sq_test",
    })
    return {"success": True, "detail": f"메모리 저장 완료 (tag: {tag})"}


def sq_mem_query(engine, query: str) -> dict:
    results = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=5)
    preview = "; ".join(r.get("user_request", "?")[:30] for r in results[-3:])
    return {"success": True, "detail": f"최근 경험 {len(results)}건: {preview}"}


def sq_file_read(engine, path: str) -> dict:
    """Read memory files or own source code for self-debugging."""
    if not path or path in ("test.txt", "testfile.txt", "path", "hosts"):
        # LLM often hallucinates nonexistent filenames; default to real file
        path = "experiences.jsonl"
    if path.endswith(".py"):
        src = Path(MACHINA_ROOT) / path
        if src.exists():
            try:
                text = src.read_text(encoding="utf-8")
                lines = text.split("\n")
                return {"success": True,
                        "detail": f"{path}: {len(lines)} lines, {src.stat().st_size} bytes. "
                                  f"First: {lines[0][:80]}... Last: {lines[-1][:80]}"}
            except Exception as e:
                return {"success": False, "detail": f"읽기 실패: {e}"}
        return {"success": False, "detail": f"파일 없음: {path}"}
    fp = MEM_DIR / path
    if not fp.exists():
        return {"success": False, "detail": f"파일 없음: {fp.name}"}
    size = fp.stat().st_size
    return {"success": True, "detail": f"{fp.name}: {size} bytes"}


def sq_file_list(engine) -> dict:
    files = list(MEM_DIR.glob("*.jsonl"))
    info = ", ".join(f"{f.name}({f.stat().st_size//1024}K)" for f in files[:8])
    return {"success": True, "detail": f"메모리 파일 {len(files)}개: {info}"}


def sq_do_code(engine, code: str) -> dict:
    """Self-question action: execute Python code in sandbox."""
    from machina_tools import execute_code
    if not code or len(code) < 5:
        return {"success": False, "detail": "코드 없음"}
    # Pre-validate: reject obviously broken code (truncated, syntax error)
    try:
        compile(code, "<sq>", "exec")
    except SyntaxError as e:
        return {"success": False, "detail": f"구문 오류: {e}"}
    output = execute_code("python", code)
    # Check for actual failure markers (not just "error:" prefix)
    _fail_markers = ("error:", "traceback", "Error:", "Exception:", "NameError", "TypeError", "ValueError")
    ok = not any(m.lower() in output.lower()[:300] for m in _fail_markers)
    return {"success": ok, "detail": output[:500] or "(출력 없음)"}


def sq_do_reflect(engine, topic: str, reason: str) -> dict:
    """Self-question action: LLM deep reflection on a topic -> insight."""
    prompt = (
        f"'{topic}'에 대해 깊이 생각해보자.\n"
        f"이유: {reason}\n\n"
        "내가 알고 있는 것, 모르는 것, 다음에 확인해야 할 것을 정리해.\n"
        "한국어 5줄 이내."
    )
    reflection = _call_engine_llm(
        prompt, system="자율 에이전트의 자기성찰 엔진.",
        max_tokens=600, temperature=0.5, think=False)
    if reflection and len(reflection) > 10:
        _jsonl_append(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", {
            "ts_ms": int(time.time() * 1000),
            "event": "insight", "stream": INSIGHTS_STREAM,
            "type": "self_reflection",
            "topic": topic, "reason": reason,
            "reflection": reflection[:500],
            "source": "autonomic_sq",
        })
        return {"success": True, "detail": reflection[:500]}
    return {"success": False, "detail": "성찰 결과 없음"}


def sq_do_audit(engine, tool: str, test_type: str, reason: str) -> dict:
    """Self-question action: audit a specific tool with targeted test."""
    from machina_dispatch import run_machina_tool, TOOL_ALIASES

    aid = TOOL_ALIASES.get(tool.lower(), "")
    if not aid:
        manifest = _load_manifest_tools_full()
        for t in manifest:
            if tool.lower() in t["name"].lower() or tool.lower() in t["aid"].lower():
                aid = t["aid"]
                break
    if not aid:
        return sq_do_tool_test(engine, tool, "")

    _UNSAFE = {"SHELL", "GENESIS", "DELETE", "COMPILE", "PIP", "HTTP_GET", "BUILD"}
    if any(u in aid for u in _UNSAFE):
        return {"success": False, "detail": f"자율 모드에서 '{aid}' 감사 불가 (ASK 권한)"}

    results = []
    try:
        if test_type == "empty":
            r = run_machina_tool(aid, {})
            results.append(f"빈 입력: {str(r)[:100]}")
        elif test_type == "max":
            r = run_machina_tool(aid, {"text": "A" * 1000, "query": "A" * 500})
            results.append(f"최대 입력: {str(r)[:100]}")
        elif test_type == "schema":
            manifest = _load_manifest_tools_full()
            schema = next((t for t in manifest if t["aid"] == aid), {})
            test_input = {}
            for field in schema.get("inputs", []):
                test_input[field] = "test_value"
            r = run_machina_tool(aid, test_input)
            results.append(f"스키마 입력({list(test_input.keys())}): {str(r)[:100]}")
        elif test_type == "chain":
            r1 = run_machina_tool(aid, {"text": "audit_chain_test", "query": "test"})
            r1_str = str(r1)[:200] if r1 else "(빈 결과)"
            sq_mem_save(engine, f"audit chain result for {aid}: {r1_str[:50]}")
            results.append(f"체인 테스트: {aid} → memory_save 완료")
        else:
            r = run_machina_tool(aid, {"text": "audit_test"})
            results.append(f"기본 테스트: {str(r)[:100]}")
    except Exception as e:
        results.append(f"감사 에러: {type(e).__name__}: {e}")

    detail = "; ".join(results)
    ok = not any("에러" in r or "error" in r.lower() for r in results)

    if ok:
        _jsonl_append(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", {
            "ts_ms": int(time.time() * 1000),
            "event": "insight", "stream": INSIGHTS_STREAM,
            "type": "tool_audit",
            "topic": f"도구 감사: {aid}",
            "reason": reason,
            "reflection": f"{test_type} 테스트 결과: {detail[:500]}",
            "source": "autonomic_sq_audit",
        })

    return {"success": ok, "detail": detail[:500]}
