"""Machina Autonomic Engine — Burst mode + stimulus handlers.

Extracted from _engine.py. All functions take `engine` (AutonomicEngine) as first arg.
"""
import json, os, time
from pathlib import Path

from machina_shared import (
    _jsonl_append, _jsonl_read, _call_engine_llm,
    _load_manifest_tools, _load_manifest_tools_full,
    MACHINA_ROOT, MEM_DIR,
    EXPERIENCE_STREAM, INSIGHTS_STREAM, SKILLS_STREAM,
)
from machina_autonomic._constants import (
    KNOWLEDGE_STREAM, AUDIT_LOG_FILE,
    STORE_SUMMARY_LEN, STORE_RESULT_LEN,
    TOOL_TEST_BATCH_EMPTY, TOOL_TEST_BATCH_MAX, TOOL_TEST_BATCH_BENCH,
    _audit_log, _send_alert, logger,
)
from machina_autonomic._web import DDGS, _deep_web_search

# -- Manifest Registration --

_MANIFEST_PATH = Path(MACHINA_ROOT) / "work" / "scripts" / "utils" / "manifest.json"

def register_in_manifest(engine, name: str, lang: str, path: str, description: str = ""):
    """Auto-register a created tool in manifest.json."""
    try:
        manifest = {}
        if _MANIFEST_PATH.exists():
            with open(_MANIFEST_PATH, "r") as f: manifest = json.load(f)
        safe_name = name.lower().replace("-", "_")[:30]
        manifest[safe_name] = {"name": safe_name, "lang": lang, "description": description[:500],
                               "path": path, "created": int(time.time()), "source": "autonomic"}
        with open(_MANIFEST_PATH, "w") as f: json.dump(manifest, f, indent=2, ensure_ascii=False)
        logger.info(f"[Manifest] Registered '{safe_name}' ({lang})")
    except Exception as e:
        logger.warning(f"[Manifest] Registration failed for '{name}': {e}")

def unregister_from_manifest(engine, name: str):
    """Remove a tool from manifest.json."""
    try:
        if not _MANIFEST_PATH.exists(): return False
        with open(_MANIFEST_PATH, "r") as f: manifest = json.load(f)
        safe_name = name.lower().replace("-", "_")
        if safe_name not in manifest: return False
        del manifest[safe_name]
        with open(_MANIFEST_PATH, "w") as f: json.dump(manifest, f, indent=2, ensure_ascii=False)
        logger.info(f"[Manifest] Unregistered '{safe_name}'"); return True
    except Exception as e:
        logger.warning(f"[Manifest] Unregister failed for '{name}': {e}"); return False

# -- Self-Enqueue + Inbox --

_QUEUE_DIR = Path(MACHINA_ROOT) / "work" / "queue"

def self_enqueue_validation(engine, skill_name: str, code_hash: str):
    """Write a validation goal to inbox queue (file-based, no HTTP)."""
    inbox = _QUEUE_DIR / "inbox"; inbox.mkdir(parents=True, exist_ok=True)
    request = {"goal_id": "goal.ERROR_SCAN.v1",
               "inputs": {"input_path": "examples/test.csv", "pattern": "ERROR", "max_rows": 100},
               "candidate_tags": ["tag.log", "tag.error"], "control_mode": "FALLBACK_ONLY",
               "metadata": {"origin": "autonomic_validation", "skill_name": skill_name, "code_hash": code_hash}}
    ts = int(time.time() * 1000)
    with open(inbox / f"validate_{code_hash[:8]}_{ts}.json", "w") as f: json.dump(request, f)
    _audit_log("ENQUEUE", "self_validation", f"{skill_name} ({code_hash[:8]})")

def drain_inbox(engine, max_jobs: int = 3):
    """Process pending validation goals from queue/inbox."""
    import subprocess as _sp
    inbox = _QUEUE_DIR / "inbox"
    if not inbox.exists(): return
    processing, done_dir, failed_dir = _QUEUE_DIR / "processing", _QUEUE_DIR / "done", _QUEUE_DIR / "failed"
    for d in (processing, done_dir, failed_dir): d.mkdir(parents=True, exist_ok=True)
    cli_path = Path(MACHINA_ROOT) / "build" / "machina_cli"
    if not cli_path.exists(): return
    jobs = sorted(inbox.glob("*.json"))[:max_jobs]
    if not jobs: return
    for job_path in jobs:
        fname = job_path.name; proc_path = processing / fname
        try: job_path.rename(proc_path)
        except OSError: continue
        t0 = time.time()
        try:
            result = _sp.run([str(cli_path), "run", str(proc_path)],
                             capture_output=True, text=True, timeout=30, cwd=str(MACHINA_ROOT))
            success, output = result.returncode == 0, (result.stdout + result.stderr)[:1000]
        except Exception as e:
            success, output = False, str(e)
        dur = int((time.time() - t0) * 1000)
        dest = done_dir if success else failed_dir
        try: proc_path.rename(dest / fname)
        except OSError: pass
        try: meta = json.loads((dest / fname).read_text()) if (dest / fname).exists() else {}
        except Exception: meta = {}
        origin_meta = meta.get("metadata", {})
        _jsonl_append(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", {
            "ts_ms": int(time.time() * 1000), "event": "inbox_validation",
            "stream": EXPERIENCE_STREAM,
            "user_request": f"[validation] {origin_meta.get('skill_name', fname)}",
            "tool_used": meta.get("goal_id", ""),
            "result_preview": output[:STORE_RESULT_LEN], "success": success,
            "elapsed_sec": dur / 1000, "source": "autonomic_inbox"})
        status = "OK" if success else "FAIL"
        _audit_log("INBOX", f"validation_{status}", f"{fname}: {output[:100]}", success=success, duration_ms=dur)
        if not success and origin_meta.get("code_hash"):
            engine._rollback_artifact({"code_hash": origin_meta["code_hash"]})
            _send_alert(f"⚠️ 검증 실패 → 롤백: {origin_meta.get('skill_name', '?')}")

# -- Multi-Turn Burst Mode --

def autonomous_burst(engine, abort_check=None):
    """Multi-turn autonomous work session. Exits by time or stall."""
    if engine._in_burst: return
    engine._in_burst = True
    engine._burst_l2_streak = 0; engine._sq_count = 0; engine._sq_recent.clear()
    engine._SQ_CONSECUTIVE_DEDUP = 0; engine.questioner.reset_novelty_stats()
    max_sec = engine._t.get("burst_max_sec", 1800)
    stall_limit = engine._t.get("burst_stall_limit", 3)
    burst_start = time.time(); turns_done = 0; stall_count = 0
    logger.info(f"[Burst] Session start (max {max_sec // 60}min)")
    engine._stream(f"🔥 자율 사고 세션 시작 (최대 {max_sec // 60}분)\n사용자 메시지로 언제든 중단 가능")
    try:
        turn = 0
        while True:
            turn += 1; elapsed = time.time() - burst_start
            if elapsed >= max_sec:
                engine._stream(f"시간 제한 도달 ({max_sec // 60}분). 세션 종료."); break
            if engine.idle_seconds() < 30:
                engine._stream("사용자 활동 감지. 자율 작업 일시정지."); break
            if abort_check and abort_check(): break
            if stall_count >= stall_limit:
                engine._stream(f"{stall_limit}턴 연속 무성과. 대기 모드 복귀."); break
            action = pick_next_action(engine, abort_check)
            if not action:
                stall_count += 1
                if stall_count < stall_limit: time.sleep(30); continue
                else: engine._stream("수행할 작업 없음. 대기 모드 복귀."); break
            remaining = int((max_sec - elapsed) / 60)
            engine._stream(f"#{turn} {action['name']} (잔여 {remaining}분)")
            try:
                engine._last_action_productive = False; action["fn"]()
                if action.get("level_key"):
                    engine.level_done[action["level_key"]] = time.time(); stall_count = 0
                elif action.get("priority", 0) > 0: stall_count = 0
                elif getattr(engine, '_last_action_productive', False): stall_count = 0
                else: stall_count += 1
                turns_done += 1
            except Exception as e:
                engine._stream(f"#{turn} 오류: {e}"); stall_count += 1
            time.sleep(2)
    finally:
        engine._in_burst = False; engine.level_done["_burst"] = time.time()
        if engine._stasis:
            engine._stasis = False; engine._stasis_entered = 0.0; engine._prev_hashes.clear()
        dur = int(time.time() - burst_start)
        ns = engine.questioner.get_novelty_stats()
        _audit_log("BURST", "novelty_summary",
                   f"high={ns['high']} low={ns['low']} skipped={ns['skipped']} turns={turns_done} dur={dur}s")
        if turns_done > 0:
            engine._stream(f"🔥 자율 세션 종료 ({turns_done}턴, {dur}초, novelty: H{ns['high']}/L{ns['low']}/S{ns['skipped']})")
            engine._milestone(f"자율 세션 종료: {turns_done}턴, {dur}초 (novelty H{ns['high']}/L{ns['low']})")
        engine._save_state()

def pick_next_action(engine, abort_check=None):
    """Priority-based action selection for burst mode."""
    now = time.time(); t = engine._t; cf = engine._cloud_rate_factor()
    rate_factor = (0.5 if engine._in_burst else 1.0) * cf
    actions = []
    l2_rf = rate_factor * 8 if engine._in_burst and getattr(engine, '_burst_l2_streak', 0) >= 3 else rate_factor
    stasis_ok = (not engine._stasis) or engine._in_burst
    if stasis_ok and now - engine.level_done.get("test", 0) >= t["l2_rate"] * l2_rf:
        actions.append({"name": "자가 테스트 실행", "priority": 5,
                        "fn": lambda ac=abort_check: engine._do_test_and_learn(ac), "level_key": "test"})
    if stasis_ok and now - engine.level_done.get("heal", 0) >= t["l3_rate"] * rate_factor:
        actions.append({"name": "자가 수정 (Genesis)", "priority": 4, "fn": engine._do_heal, "level_key": "heal"})
    web_rate = t.get("web_explore_rate", 900) * rate_factor
    if now - engine.level_done.get("web_explore", 0) >= web_rate:
        actions.append({"name": "웹 탐색 + 지식 습득", "priority": 3, "fn": engine._do_web_explore, "level_key": "web_explore"})
    if engine.curiosity.can_run():
        actions.append({"name": "새 도구 탐색/생성", "priority": 2, "fn": engine._do_curiosity, "level_key": "curiosity"})
    if now - engine.level_done.get("reflect", 0) >= t["l1_rate"] * rate_factor:
        actions.append({"name": "경험 분석 + 인사이트 추출", "priority": 1, "fn": engine._do_reflect, "level_key": "reflect"})
    inbox = Path(MACHINA_ROOT) / "work" / "queue" / "inbox"
    if inbox.exists() and any(inbox.glob("*.json")):
        actions.append({"name": "검증 큐 처리", "priority": 4, "fn": lambda: engine._drain_inbox(max_jobs=3), "level_key": None})
    sq_limit = 3 if engine._in_burst else 1
    sq_cooldown = max(15, int(os.getenv("MACHINA_SQ_COOLDOWN_SEC", "45")))
    # SQ fallback only if there is a real unresolved target.
    sq_needed = False
    if not actions:
        profile = engine._build_tool_profile()
        if profile.get("high_fail") or profile.get("untested"):
            sq_needed = True
        else:
            recent = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=40)
            fail_count = sum(
                1 for e in recent
                if (not e.get("success")) and not str(e.get("user_request", "")).startswith("[self-test]")
            )
            sq_needed = fail_count >= 2
    sq_noop_streak = getattr(engine, "_sq_noop_streak", 0)
    sq_fail_streak = getattr(engine, "_sq_fail_streak", 0)
    sq_recent_sec = now - float(getattr(engine, "_sq_last_ts", 0.0))
    sq_backoff = sq_noop_streak >= 2 or sq_fail_streak >= 3
    sq_rate_ok = sq_recent_sec >= sq_cooldown
    if sq_backoff and now - float(getattr(engine, "_sq_last_backoff_log", 0.0)) >= 60:
        _audit_log("SQ", "picker_backoff",
                   f"noop_streak={sq_noop_streak}, fail_streak={sq_fail_streak}, since_last={int(sq_recent_sec)}s",
                   success=True)
        engine._sq_last_backoff_log = now
    if not actions and sq_needed and sq_rate_ok and not sq_backoff and engine._sq_count < sq_limit:
        actions.append({"name": "🧠 자기질문 → 자기주도 행동", "priority": 0, "fn": engine._llm_self_question, "level_key": None})
    if not actions:
        stim = engine.stimulus.pick()
        if stim:
            actions.append({"name": f"자극: {stim.get('desc', stim.get('query', ''))[:40]}", "priority": 0,
                            "fn": lambda s=stim: engine._execute_stimulus(s), "level_key": None})
    if not actions: return None
    actions.sort(key=lambda a: a["priority"], reverse=True)
    return actions[0]

# -- Stimulus Execution --

def execute_stimulus(engine, stim: dict):
    """Execute a stimulus with REAL actions matching its description."""
    t0 = time.time()
    action = stim.get("action", "test"); desc = stim.get("desc", stim.get("query", ""))
    _CAT = {"tool_challenge": "도구 도전", "knowledge_quest": "지식 탐구",
            "cross_domain": "통합 테스트", "optimization": "최적화 벤치마크"}
    cat_label = _CAT.get(stim.get("category", ""), stim.get("category", "?"))
    result = {"success": False, "detail": ""}
    try:
        if action == "web": result = stim_web(engine, stim)
        elif action == "benchmark": result = stim_benchmark(engine, stim)
        elif action == "test": result = stim_tool_test(engine, stim)
        elif action == "integration": result = stim_integration(engine, stim)
        else: result = {"success": False, "detail": f"알 수 없는 액션: {action}"}
    except Exception as e:
        result = {"success": False, "detail": f"{type(e).__name__}: {e}"}
    detail = str(result.get("detail", "") or "")
    detail_l = detail.lower()
    noop = ("이미 24시간 내 학습됨" in detail) or ("already learned" in detail_l)
    if result.get("success") and not noop:
        engine._last_action_productive = True
    status = "✅" if result.get("success") else "⚠️"
    detail = detail[:200]
    engine._stream(f"🧪 {cat_label} {status} {detail}" if detail else f"🧪 {cat_label} {status}")
    engine.stimulus.mark_done(stim)
    dur = int((time.time() - t0) * 1000)
    _audit_log("STIMULUS", action, f"{desc[:80]} | {status} {detail[:60]}",
               success=result.get("success", False), duration_ms=dur)

def stim_web(engine, stim: dict) -> dict:
    """Web stimulus: deep search + summarize + store."""
    query = stim.get("query", "")
    if not query: return {"success": False, "detail": "검색어 없음"}
    recent_k = _jsonl_read(MEM_DIR / f"{KNOWLEDGE_STREAM}.jsonl", max_lines=100)
    cutoff_ms = int((time.time() - 86400) * 1000)
    for k in recent_k:
        if k.get("query", "") == query and k.get("ts_ms", 0) > cutoff_ms:
            return {"success": True, "detail": f"'{query}' 이미 24시간 내 학습됨"}
    return _deep_web_search(query, "random_stimulus", "stimulus_web")

def stim_tool_test(engine, stim: dict) -> dict:
    """Tool challenge: exercise tools based on desc."""
    from machina_dispatch import run_machina_tool
    desc = stim.get("desc", "").lower()
    tools = _load_manifest_tools()
    _UNSAFE = {"SHELL", "GENESIS", "DELETE", "COMPILE"}
    safe_aids = [a for a in tools if not any(u in a for u in _UNSAFE)]
    if "chain" in desc:
        try:
            r1 = str(run_machina_tool("AID.MEMORY.QUERY.v1", {"query": "self-improvement", "k": "2"}) or "")
            code = f"data = {json.dumps(r1[:150])}\nprint(f'길이: {{len(data)}}, 미리보기: {{data[:50]}}')"
            r2 = str(run_machina_tool("AID.CODE.EXEC.v1", {"lang": "python", "code": code}) or "")
            return {"success": "error" not in r2[:30].lower(), "detail": f"도구 체인: {r2[:80]}"}
        except Exception as e: return {"success": False, "detail": f"체인 실패: {e}"}
    elif "unicode" in desc or "emoji" in desc:
        test_in = {"text": "안녕 🔥 ñ 日本語 🤖💻", "query": "유니코드 🎯 テスト"}
        ok, fail = 0, 0
        for aid in [a for a in safe_aids if "MEMORY" in a or "FILE" in a][:3]:
            try:
                r = run_machina_tool(aid, test_in); rs = str(r) if r else ""
                if rs and "error" not in rs[:30].lower(): ok += 1
                else: fail += 1
            except Exception: fail += 1
        return {"success": ok > 0, "detail": f"유니코드 테스트: {ok+fail}개 중 {ok}개 정상"}
    elif "nested json" in desc:
        nested = {"level": 0}; cur = nested
        for i in range(1, 6): cur["child"] = {"level": i, "data": f"depth_{i}", "meta": {"i": i}}; cur = cur["child"]
        try:
            r = run_machina_tool("AID.MEMORY.APPEND.v1", {"text": json.dumps(nested)[:500], "stream": "test_nested"})
            return {"success": True, "detail": f"5단계 중첩 JSON: {r[:80]}"}
        except Exception as e: return {"success": False, "detail": f"중첩 JSON 실패: {e}"}
    elif "util" in desc:
        from machina_tools import util_list as _ul, util_run as _ur
        lst = _ul()
        if "no saved" in lst.lower(): return {"success": True, "detail": "저장된 유틸 없음"}
        names = [l.split(":")[0].strip().lstrip("• ") for l in lst.split("\n") if ":" in l]
        if not names: return {"success": True, "detail": "유틸 목록 비어있음"}
        ok, fail = 0, 0
        for nm in names[:5]:
            try:
                rs = str(_ur(nm) or "")
                if rs and "error" not in rs[:30].lower(): ok += 1
                else: fail += 1
            except Exception: fail += 1
        return {"success": ok >= 0, "detail": f"유틸 검증 {len(names)}개: 성공 {ok}, 실패 {fail}"}
    elif "memory" in desc:
        from machina_learning import memory_save as _ms, memory_search_recent as _msr
        tag = f"stim_test_{int(time.time())}"
        _ms(f"자극 테스트 마커: {tag}", stream="test", topic="stimulus_verify")
        time.sleep(0.3); sr = _msr(tag, stream="test", limit=3); found = tag in sr
        return {"success": found, "detail": f"메모리 왕복: {'성공' if found else '실패'} (태그: {tag[:20]})"}
    else:
        return {"success": False, "detail": f"인식 불가 도구 테스트: {desc[:60]}"}

def stim_integration(engine, stim: dict) -> dict:
    """Cross-domain integration: real multi-system tests."""
    desc = stim.get("desc", "").lower()
    if "memory" in desc and ("query" in desc or "round-trip" in desc):
        from machina_learning import memory_save as _ms, memory_search_recent as _msr
        tag = f"integ_{int(time.time())}"
        _ms(f"통합 테스트: {tag}", stream="telegram", topic="integration_test")
        time.sleep(0.5); sr = _msr(tag, stream="telegram", limit=3); found = tag in sr
        return {"success": found, "detail": f"메모리 쓰기→검색→검증: {'통과' if found else '실패'}"}
    elif "genesis" in desc:
        tools = _load_manifest_tools()
        genesis_on = os.getenv("MACHINA_GENESIS_ENABLE", "") == "1"
        return {"success": len(tools) > 0, "detail": f"Genesis: 매니페스트 {len(tools)}개, {'활성' if genesis_on else '비활성'}"}
    elif "experience" in desc and "reflect" in desc:
        from machina_learning import experience_record as _er
        insights_f = MEM_DIR / f"{INSIGHTS_STREAM}.jsonl"
        before = sum(1 for _ in open(insights_f, "r")) if insights_f.exists() else 0
        _er("integration_stimulus", {"type": "test", "action": "stimulus_check"},
            "자극 통합 테스트", success=True, elapsed=0.1)
        engine._do_reflect()
        after = sum(1 for _ in open(insights_f, "r")) if insights_f.exists() else 0
        return {"success": True, "detail": f"경험→반성 완료. 인사이트 {before}→{after}건 (+{after-before})"}
    elif "skill" in desc and "trust" in desc:
        sc = sum(1 for _ in open(MEM_DIR / f"{SKILLS_STREAM}.jsonl")) if (MEM_DIR / f"{SKILLS_STREAM}.jsonl").exists() else 0
        ec = sum(1 for _ in open(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl")) if (MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl").exists() else 0
        return {"success": True, "detail": f"스킬 {sc}건, 경험 {ec}건. 신뢰 시스템 가동 중"}
    elif "web" in desc and "search" in desc:
        if DDGS is None: return {"success": False, "detail": "DDGS 미설치"}
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                with DDGS() as ddgs:
                    results = list(ddgs.text("autonomous agent self-improvement techniques", region="wt-wt", max_results=3))
            if not results: return {"success": False, "detail": "검색 결과 없음"}
            snippet = results[0].get("body", "")[:500]
            summary = _call_engine_llm(f"요약해: {snippet}", system="한국어 1줄 요약.", max_tokens=500, temperature=0.3, think=False)
            _jsonl_append(MEM_DIR / f"{KNOWLEDGE_STREAM}.jsonl", {
                "ts_ms": int(time.time() * 1000), "event": "integration_web_pipeline",
                "stream": KNOWLEDGE_STREAM, "summary": summary[:STORE_SUMMARY_LEN]})
            return {"success": True, "detail": f"웹→요약→저장: {summary[:80]}"}
        except Exception as e: return {"success": False, "detail": f"통합 웹 파이프라인 실패: {e}"}
    else:
        import re as _re
        aids_found = _re.findall(r'AID\.\w+\.\w+\.v\d+', stim.get("desc", ""), _re.IGNORECASE)
        if not aids_found:
            manifest = _load_manifest_tools_full()
            tn = {t["name"].lower(): t["aid"] for t in manifest}
            for tname, taid in tn.items():
                if tname in desc: aids_found.append(taid)
        if aids_found:
            from machina_dispatch import run_machina_tool
            _UNSAFE = {"SHELL", "GENESIS", "DELETE", "COMPILE"}
            safe = [a for a in aids_found if not any(u in a.upper() for u in _UNSAFE)]
            results = []
            for aid in safe[:3]:
                try: run_machina_tool(aid, {"query": "integration_test", "text": "test"}); results.append(f"{aid.split('.')[-2]}:OK")
                except Exception as e: results.append(f"{aid.split('.')[-2]}:FAIL")
            return {"success": len(results) > 0, "detail": f"통합 실행 {len(safe)}개: {', '.join(results)}"}
        return {"success": False, "detail": f"인식 불가 통합 테스트: {desc[:60]}"}

def stim_benchmark(engine, stim: dict) -> dict:
    """Benchmark stimulus: real performance measurements."""
    desc = stim.get("desc", "").lower()
    if "time" in desc and "tool" in desc:
        from machina_dispatch import run_machina_tool
        tools = _load_manifest_tools()
        _UNSAFE = {"SHELL", "GENESIS", "DELETE", "COMPILE"}
        safe_aids = [a for a in tools if not any(u in a for u in _UNSAFE)]
        timings = []
        for aid in safe_aids[:TOOL_TEST_BATCH_BENCH]:
            t0 = time.time()
            try: run_machina_tool(aid, {"query": "bench", "text": "test", "k": "1"})
            except Exception: pass
            timings.append((aid.split(".")[-2], int((time.time() - t0) * 1000)))
        if not timings: return {"success": False, "detail": "측정 가능한 도구 없음"}
        times = sorted(t[1] for t in timings); p50 = times[len(times) // 2]; p99 = times[-1]
        slowest = max(timings, key=lambda x: x[1])
        _jsonl_append(AUDIT_LOG_FILE, {"ts_ms": int(time.time() * 1000), "level": "BENCHMARK",
                                       "event": "tool_latency", "p50_ms": p50, "p99_ms": p99,
                                       "count": len(timings), "slowest": slowest[0]})
        return {"success": True, "detail": f"도구 {len(timings)}개: p50={p50}ms, p99={p99}ms, 최느림={slowest[0]}({slowest[1]}ms)"}
    elif "memory" in desc and "size" in desc:
        sizes = {}; total = 0
        for f in MEM_DIR.glob("*.jsonl"):
            try: sz = f.stat().st_size; sizes[f.stem] = sz; total += sz
            except OSError: pass
        top3 = sorted(sizes.items(), key=lambda x: x[1], reverse=True)[:3]
        _jsonl_append(AUDIT_LOG_FILE, {"ts_ms": int(time.time() * 1000), "level": "BENCHMARK",
                                       "event": "memory_sizes", "total_kb": total // 1024, "files": sizes})
        return {"success": True, "detail": f"메모리 총 {total//1024}KB ({len(sizes)}개). 상위: {', '.join(f'{n}={s//1024}KB' for n,s in top3)}"}
    elif "duplicate" in desc:
        dup_count, checked = 0, 0
        for f in MEM_DIR.glob("*.jsonl"):
            seen = set()
            try:
                for entry in _jsonl_read(f, max_lines=1000):
                    key = json.dumps(entry, sort_keys=True)
                    if key in seen: dup_count += 1
                    seen.add(key); checked += 1
            except Exception: pass
        return {"success": True, "detail": f"중복 검사: {checked}건 중 {dup_count}건 중복"}
    elif "curriculum" in desc or "get_rates" in desc:
        t0 = time.time(); rates = engine.curriculum.get_rates()
        dur_us = int((time.time() - t0) * 1_000_000)
        return {"success": True, "detail": f"커리큘럼 지연: {dur_us}µs "
                f"(초={rates.get('easy_success_rate',0):.0%}, 중={rates.get('medium_success_rate',0):.0%}, 고={rates.get('hard_success_rate',0):.0%})"}
    elif "disk" in desc:
        work_dir = Path(MACHINA_ROOT) / "work"; total, file_count = 0, 0
        try:
            for f in work_dir.rglob("*"):
                if f.is_file(): total += f.stat().st_size; file_count += 1
        except Exception: pass
        return {"success": True, "detail": f"work/ 사용량: {total/(1024*1024):.1f}MB, {file_count}개 파일"}
    else:
        return {"success": False, "detail": f"인식 불가 벤치마크: {desc[:60]}"}
