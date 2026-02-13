"""Machina Autonomic Engine — Level handlers (L1-L6) + helpers.

Extracted from _engine.py. All functions take `engine` (AutonomicEngine) as first arg.
"""
import hashlib, json, os, time
from pathlib import Path

from machina_shared import (
    _jsonl_append, _jsonl_read, _call_engine_llm,
    _load_manifest_tools_full, sandboxed_run,
    MACHINA_ROOT, MEM_DIR,
    EXPERIENCE_STREAM, INSIGHTS_STREAM, SKILLS_STREAM,
)
from machina_autonomic._constants import (
    KNOWLEDGE_STREAM,
    STORE_SUMMARY_LEN, STORE_RESULT_LEN,
    REFLECT_EXPERIENCE_WINDOW, REFLECT_RECENT_SLICE,
    _audit_log, _send_alert, logger,
)
from machina_autonomic._web import DDGS, _deep_web_search, _ACTION_MARKERS

# -- Tool Introspection --

def build_tool_profile(engine) -> dict:
    """Introspect all tools: tested/untested/fail rates/hypotheses. Cached 5min."""
    now = time.time()
    if engine._tool_profile_cache and now - engine._tool_profile_ts < 300:
        return engine._tool_profile_cache
    manifest = _load_manifest_tools_full()
    if not manifest:
        return {"tools": [], "total": 0, "tested": 0, "untested": [], "high_fail": [], "hypotheses": []}
    exps = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=200)
    tool_stats = {}
    for e in exps:
        t = e.get("tool_used", "")
        if not t: continue
        if t not in tool_stats: tool_stats[t] = {"ok": 0, "fail": 0}
        tool_stats[t]["ok" if e.get("success") else "fail"] += 1
    tested, untested, high_fail = [], [], []
    for t in manifest:
        aid, name = t["aid"], t["name"]
        stats = tool_stats.get(aid, tool_stats.get(name, None))
        if stats:
            tested.append(t)
            total_uses = stats["ok"] + stats["fail"]
            if total_uses >= 2 and stats["fail"] / total_uses > 0.4:
                high_fail.append({"name": name, "aid": aid, "fail_rate": stats["fail"] / total_uses,
                                  "uses": total_uses})
        else:
            untested.append({"name": name, "aid": aid, "inputs": t.get("inputs", [])})
    # Generate hypotheses from data
    hypotheses = []
    for hf in high_fail[:3]:
        hypotheses.append(f"'{hf['name']}' 실패율 {hf['fail_rate']:.0%} — 입력 형식이나 전제조건 점검 필요")
    if untested:
        sample = untested[:2]
        for u in sample:
            hypotheses.append(f"미사용 도구 '{u['name']}' 테스트 (입력: {', '.join(u['inputs'][:3])})")
    if len(tested) > 3:
        top = sorted(tool_stats.items(), key=lambda x: x[1]["ok"], reverse=True)[:2]
        for tname, ts in top:
            if ts["ok"] > 5:
                hypotheses.append(f"'{tname}' 고빈도 ({ts['ok']}회 성공) — 체인/확장 활용 검토")
    profile = {"tools": manifest, "total": len(manifest), "tested": len(tested),
               "untested": untested, "high_fail": high_fail, "hypotheses": hypotheses}
    engine._tool_profile_cache = profile
    engine._tool_profile_ts = now
    return profile

def build_dynamic_queries(engine) -> list:
    """Generate context-aware search queries from tool profile + gaps."""
    profile = build_tool_profile(engine)
    queries = []
    for hf in profile.get("high_fail", [])[:2]:
        queries.append(f"{hf['name']} common errors troubleshooting")
    for ut in profile.get("untested", [])[:2]:
        queries.append(f"{ut['name']} usage examples best practices")
    insights = _jsonl_read(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", max_lines=5)
    for ins in insights[-2:]:
        topic = ins.get("topic", ins.get("type", ""))
        if topic and len(topic) > 3:
            queries.append(f"AI agent {topic} optimization")
    return queries[:6]

# -- Helpers --

def cloud_rate_factor(engine) -> float:
    """Always 1.0 — _call_engine_llm handles routing internally."""
    return 1.0

def trust_score(engine, entry: dict) -> float:
    """Composite trust: recency * result_quality. Range 0.0-1.0."""
    now_ms = int(time.time() * 1000)
    age_days = (now_ms - entry.get("ts_ms", now_ms)) / 86400000
    recency = 2 ** (-age_days / 7.0)
    if "success" in entry:
        quality = 1.0 if entry["success"] else 0.3
    else:
        quality = 0.5
    return round(recency * quality, 3)

def dev_report(engine, category: str, message: str):
    """Send progress report to Telegram in dev exploration mode."""
    if not engine._dev:
        return
    now = time.time()
    min_interval = engine._t.get("report_min_interval", 300)
    key = f"report_{category}"
    if now - engine._dev_last_report.get(key, 0) < min_interval:
        return
    engine._dev_last_report[key] = now
    _CAT_KR = {"L1": "반성", "L2": "테스트", "L3": "치유",
               "L4": "정리", "L5": "탐구", "STASIS": "정체"}
    _send_alert(f"📊 {_CAT_KR.get(category, category)} | {message}")

def stream(engine, message: str):
    """Send verbose self-dialogue to Telegram (DEV mode only)."""
    if not engine._dev:
        return
    if message == engine._dev_last_report.get("_stream_last_msg"):
        return
    engine._dev_last_report["_stream_last_msg"] = message
    _send_alert(message)

def milestone(engine, message: str):
    """Send important milestone to Telegram (always, regardless of DEV mode).

    Use for: L2 test results, L3 heal outcomes, burst completion,
    web explore findings, errors. Rate-limited to 1 per 60s per message hash.
    """
    msg_key = hash(message) % 100000
    now = time.time()
    last = engine._dev_last_report.get(f"_ms_{msg_key}", 0)
    if now - last < 60:
        return
    engine._dev_last_report[f"_ms_{msg_key}"] = now
    _send_alert(f"🤖 {message}")

# -- L1: Self-Reflect --

def do_reflect(engine):
    """Statistical analysis on experiences -> extract insights."""
    engine._stream("경험 데이터 통계 분석 시작...")
    logger.info("[L1] Self-Reflect: analyzing experiences")
    t0 = time.time()
    exp_file = MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl"
    if not exp_file.exists():
        return
    total_exp = 0
    try:
        with open(exp_file, "r", encoding="utf-8", errors="replace") as f:
            for _ in f:
                total_exp += 1
    except Exception:
        total_exp = 0
    experiences = _jsonl_read(exp_file, max_lines=REFLECT_EXPERIENCE_WINDOW)
    if len(experiences) < 5:
        return
    tool_stats, fail_types, failures = {}, {}, []
    for exp in experiences[-REFLECT_RECENT_SLICE:]:
        tool = exp.get("tool_used", "")
        ok = exp.get("success", False)
        if tool:
            if tool not in tool_stats:
                tool_stats[tool] = {"ok": 0, "fail": 0}
            tool_stats[tool]["ok" if ok else "fail"] += 1
        if not ok:
            ftype = exp.get("fail_type", "unknown")
            fail_types[ftype] = fail_types.get(ftype, 0) + 1
            failures.append(exp)
    rules = []
    for tool, stats in tool_stats.items():
        total = stats["ok"] + stats["fail"]
        if total >= 3:
            rules.append(f"{tool}: {stats['ok']/total:.0%} success ({total} uses)")
    # Stale detection
    reflect_hash = hashlib.sha256(json.dumps(sorted(rules), ensure_ascii=False).encode()).hexdigest()[:16]
    if reflect_hash == engine._last_reflect_hash:
        dur = int((time.time() - t0) * 1000)
        _audit_log("L1", "reflect_stale", "identical rules — skipped write", duration_ms=dur)
        engine._dev_report("L1", "경험 변화 없음 — 동일 분석 스킵 (5분 쿨다운)")
        engine.level_done["reflect"] = time.time() + 300
        return
    engine._last_reflect_hash = reflect_hash
    if rules:
        skip = False
        now_ms = int(time.time() * 1000)
        existing = _jsonl_read(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", max_lines=20)
        for prev in reversed(existing):
            if prev.get("type") == "tool_stats" and prev.get("source") == "autonomic_reflect":
                if sorted(prev.get("rules", [])) == sorted(rules):
                    skip = True
                elif now_ms - prev.get("ts_ms", 0) < 1_800_000:
                    skip = True
                break
        if not skip:
            _jsonl_append(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", {
                "ts_ms": int(time.time() * 1000), "event": "insight",
                "stream": INSIGHTS_STREAM, "type": "tool_stats",
                "rules": rules, "total_experiences": len(experiences),
                "source": "autonomic_reflect",
            })
    dur = int((time.time() - t0) * 1000)
    _audit_log(
        "L1",
        "reflect",
        f"window={len(experiences)}/{total_exp} exp, {len(tool_stats)} tools, {len(failures)} failures",
        duration_ms=dur,
    )
    logger.info(
        f"[L1] Reflect complete: window={len(experiences)}/{total_exp} exp, "
        f"{len(tool_stats)} tools, {len(failures)} failures"
    )
    engine._dev_report("L1",
        f"경험 윈도우 {len(experiences)}/{total_exp}건, 도구 {len(tool_stats)}종, 실패 {len(failures)}건" +
        (f"\n상위: {'; '.join(rules[:3])}" if rules else ""))

# -- L2: Self-Test + Closed Feedback Loop --

def do_test_and_learn(engine, abort_check=None):
    """THE CRITICAL CLOSED LOOP: question->test->verify->record->analyze->heal->re-test"""
    engine._stream("테스트 시나리오 생성중... 커리큘럼 분석")
    logger.info("[L2] Self-Test: generating scenarios")
    t0 = time.time()
    curriculum_rates = engine.curriculum.get_rates()
    insights = _jsonl_read(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", max_lines=20)
    scenarios = engine.questioner.generate_scenarios(curriculum_rates, insights)
    if not scenarios:
        logger.info("[L2] No scenarios to test"); engine._stream("생성할 시나리오 없음. 건너뜀."); return
    engine._stream(f"{len(scenarios)}개 시나리오 실행중...")
    total_scen = len(scenarios)
    action_scen = sum(1 for s in scenarios if s.get("expected_type") == "action")
    hard_scen = sum(1 for s in scenarios if s.get("difficulty") == "hard")
    replay_scen = sum(1 for s in scenarios if s.get("source") in ("failure_replay", "tool_coverage", "self_question"))
    results = engine.tester.run_batch(scenarios, abort_check=abort_check)
    engine.curriculum.record_results(results)
    # Record experiences
    for detail in results["details"]:
        scenario = detail["scenario"]
        _desc = scenario.get("desc", "")[:100]
        _et, _at = scenario.get("expected_type", "?"), detail.get("actual_type", "error")
        _ps = "PASS" if detail["passed"] else "FAIL"
        _dur = f"{detail.get('duration_ms', 0):.0f}ms"
        _diff = scenario.get("difficulty", "?")
        _snip = detail.get("output_snippet", "")[:200] if detail.get("output_snippet") else ""
        _ti = f" (expected={_et}, got={_at})" if _et != _at else ""
        _preview = f"[{_ps}|{_diff}|{_dur}] {_desc}{_ti}"
        if _snip: _preview += f" | {_snip}"
        _jsonl_append(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", {
            "ts_ms": int(time.time() * 1000), "event": "self_test",
            "stream": EXPERIENCE_STREAM, "user_request": f"[self-test] {_desc}",
            "intent_type": _at, "tool_used": scenario.get("tool_used", ""),
            "result_preview": _preview[:STORE_RESULT_LEN], "success": detail["passed"],
            "elapsed_sec": detail.get("duration_ms", 0) / 1000,
            "difficulty": _diff, "source": "autonomic",
        })
    # FEEDBACK LOOP: failures -> analyze + heal
    if results["failed"] > 0:
        engine._stream(f"{results['failed']}개 실패 발견! 원인 분석 + 자동 수정 시도...")
        failure_actions = engine.healer.analyze_failures(results)
        if failure_actions:
            _jsonl_append(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", {
                "ts_ms": int(time.time() * 1000), "event": "insight",
                "stream": INSIGHTS_STREAM, "type": "test_failure_analysis",
                "failures": [a["desc"] for a in failure_actions[:5]],
                "categories": list(set(a["category"] for a in failure_actions)),
                "source": "autonomic_test",
            })
            heal_result = engine.healer.attempt_heal(failure_actions)
            if heal_result.get("success"):
                e2e = engine.regression_gate.run_e2e()
                if not e2e.get("error") and not engine.regression_gate.check(e2e):
                    engine._rollback_artifact(heal_result)
                    heal_result["success"] = False; heal_result["rolled_back"] = True
                elif not e2e.get("error"):
                    engine.regression_gate.accept(e2e)
            engine.curriculum.record_heal_result(heal_result)
            if heal_result.get("rolled_back"):
                _audit_log("L2", "heal_rollback", heal_result.get("util_name", ""), success=False)
                _send_alert(f"⚠️ 자동 수정 롤백: {heal_result.get('util_name', '?')}")
                engine._stream(f"수정 시도했으나 회귀 발생 → 롤백: {heal_result.get('util_name', '?')}")
            elif heal_result.get("success"):
                _audit_log("L2", "heal_success", heal_result.get("util_name", ""))
                engine._stream(f"자동 수정 성공: {heal_result.get('util_name', '')}")
            elif heal_result.get("attempted"):
                _ho = heal_result.get("output", "") or heal_result.get("error", "") or "(no detail)"
                _audit_log("L2", "heal_fail", _ho[:200], success=False)
                engine._stream(f"수정 시도 실패. 다음에 다시 시도.")
    # Self-Questioning
    if results["passed"] > 3 and curriculum_rates.get("medium_success_rate", 0) > 0.7:
        sq = engine.questioner.generate_self_questions(curriculum_rates)
        if sq:
            sq_results = engine.tester.run_batch(sq[:3], abort_check=abort_check)
            engine.curriculum.record_results(sq_results)
    rates = engine.curriculum.get_rates()
    dur = int((time.time() - t0) * 1000)
    robust_signal = (total_scen >= 5 and action_scen >= 3 and (hard_scen >= 1 or replay_scen >= 1))
    if results['failed'] == 0 and robust_signal:
        engine._burst_l2_streak = getattr(engine, '_burst_l2_streak', 0) + 1
    else:
        engine._burst_l2_streak = 0
    if not robust_signal:
        _audit_log(
            "L2",
            "test_low_signal",
            f"scenarios={total_scen}, action={action_scen}, hard={hard_scen}, replay={replay_scen}",
            success=True,
        )
    _audit_log("L2", "test_and_learn",
               f"{results['passed']}/{results['passed']+results['failed']} passed",
               success=(results['failed'] == 0), duration_ms=dur)
    engine._milestone(
        f"자가 테스트 완료: {results['passed']}/{results['passed']+results['failed']} 통과"
        + (f" ({results['failed']}건 실패→자동수정)" if results['failed'] > 0 else " (전원 통과)"))
    engine._dev_report("L2",
        f"통과 {results['passed']}/{results['passed']+results['failed']}\n"
        f"초급:{rates.get('easy_success_rate',0):.0%} "
        f"중급:{rates.get('medium_success_rate',0):.0%} "
        f"고급:{rates.get('hard_success_rate',0):.0%}\n"
        f"시나리오: total={total_scen}, action={action_scen}, hard={hard_scen}, replay={replay_scen}"
        + (f"\n실패 {results['failed']}건 → 자동 수정 시도" if results['failed'] > 0 else ""))

# -- L3: Self-Heal --

def do_heal(engine):
    """Process genesis suggestions from ExpeL insights."""
    engine._stream("Genesis 제안 검토중...")
    logger.info("[L3] Self-Heal: checking genesis suggestions")
    t0 = time.time()
    suggest_file = MEM_DIR / "genesis_suggestions.jsonl"
    if not suggest_file.exists():
        return
    suggestions = [s for s in _jsonl_read(suggest_file)
                   if s.get("priority", 0) >= 3 and not s.get("executed")]
    if not suggestions:
        engine._stream("처리할 제안 없음."); return
    suggestions.sort(key=lambda x: x.get("priority", 0), reverse=True)
    suggestion = suggestions[0]
    skey = suggestion.get("suggestion_key", "")
    engine._stream(f"제안 '{skey}' 처리 시작...\n내용: {suggestion.get('proposal', '')[:100]}")
    proposal = suggestion.get("proposal", "")
    generated = _call_engine_llm(
        f"다음 기능의 Python 유틸리티를 만들어 (200줄 이내):\n{proposal}\n"
        "규칙: stdlib 자유 사용, input() 금지, 결과는 stdout 출력.\n코드만 출력.",
        system="파이썬 코드 생성기. 유효한 파이썬 코드만 출력하라.",
        max_tokens=2000, temperature=0.3, think=False).strip()
    if generated.startswith("```"):
        generated = "\n".join(l for l in generated.split("\n") if not l.strip().startswith("```")).strip()
    success, output = False, ""
    utils_dir = os.path.join(MACHINA_ROOT, "work", "scripts", "utils")
    _DANGEROUS = [
        "os.system(", "subprocess.run(", "subprocess.call(", "subprocess.Popen(",
        "eval(", "exec(", "compile(", "__import__(", "__builtins__", "globals(", "locals(",
        "import socket", "import http.server", "import http.client",
        "import ctypes", "import signal", "shutil.rmtree(",
        "os.popen(", "os.exec", "io.open(", "pathlib.Path(",
    ]
    if generated and any(d in generated for d in _DANGEROUS):
        logger.warning("[L3] _do_heal: dangerous code blocked"); return
    if generated and len(generated) > 30:
        import subprocess
        script_path = os.path.join(utils_dir, f"genesis_{skey[:20]}.py")
        os.makedirs(utils_dir, exist_ok=True)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(generated)
        try:
            proc = sandboxed_run(["python3", script_path], timeout=10, cwd=MACHINA_ROOT,
                                 writable_dirs=[os.path.join(MACHINA_ROOT, "work")])
            success = proc.returncode == 0 and "traceback" not in proc.stderr.lower()
            output = proc.stdout[:1000]
        except Exception as e:
            logger.warning(f"Genesis heal script test failed: {e}")
        if success:
            code_hash = hashlib.sha256(generated.encode()).hexdigest()
            _jsonl_append(MEM_DIR / f"{SKILLS_STREAM}.jsonl", {
                "ts_ms": int(time.time() * 1000), "event": "skill", "stream": SKILLS_STREAM,
                "request": proposal[:500], "lang": "python",
                "code": generated, "result": output, "code_hash": code_hash,
            })
            e2e = engine.regression_gate.run_e2e()
            if not e2e.get("error") and not engine.regression_gate.check(e2e):
                engine._rollback_artifact({"script_path": script_path, "code_hash": code_hash})
                success = False
                _audit_log("L3", "genesis_rollback", skey, success=False)
                _send_alert(f"⚠️ Genesis 롤백: '{skey}' (회귀 테스트 실패)")
            elif not e2e.get("error"):
                engine.regression_gate.accept(e2e)
                engine._self_enqueue_validation(skey, code_hash)
                engine._register_in_manifest(f"genesis_{skey[:20]}", "python", script_path, proposal[:100])
    suggestion["executed"] = True
    suggestion["executed_ts"] = int(time.time() * 1000)
    suggestion["success"] = success
    import fcntl
    Path(suggest_file).touch(exist_ok=True)
    with open(suggest_file, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            all_sugg = [json.loads(line) for line in f if line.strip()]
            f.seek(0); f.truncate()
            for s in all_sugg:
                f.write(json.dumps(suggestion if s.get("suggestion_key") == skey else s, ensure_ascii=False) + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    dur = int((time.time() - t0) * 1000)
    _audit_log("L3", "heal", skey, success=success, duration_ms=dur)
    if success:
        engine._stream(f"'{skey}' 생성 성공!\n출력: {output[:150]}")
        engine._milestone(f"자가 치유 성공: '{skey}'")
    else:
        engine._stream(f"'{skey}' 실패.")
        engine._milestone(f"자가 치유 시도 실패: '{skey}'")
    engine._dev_report("L3", f"{'성공' if success else '실패'}: '{skey}'\n" + (f"결과: {output[:100]}" if output else ""))

# -- L5: Curiosity --

def do_curiosity(engine):
    """Autonomous capability improvement: gap scan -> goal -> execute."""
    logger.info("[L5] Curiosity: self-stimulus cycle")
    t0 = time.time()
    result = engine.curiosity.run_cycle()
    dur = int((time.time() - t0) * 1000)
    goal_key = result.get("goal", "unknown")
    # Fallback: no gaps OR synthesis failure -> web explore
    if not result.get("skipped") and not result.get("success"):
        has_creation = isinstance(result.get("result"), dict) and result["result"].get("success")
        web_rate = engine._t.get("web_explore_rate", 1800)
        if not has_creation and time.time() - engine.level_done.get("web_explore", 0) >= web_rate:
            logger.info("[L5] No productive output — falling through to web explore")
            engine._do_web_explore(); return
    if result.get("skipped"):
        logger.info(f"[L5] Skipped: {result.get('reason')}")
    elif result.get("success") or (isinstance(result.get("result"), dict) and result["result"].get("success")):
        _audit_log("L5", "curiosity_success", goal_key, duration_ms=dur)
        engine._curiosity_fail_count.pop(goal_key, None)
        r = result.get("result", result)
        if r.get("code_hash"):
            engine._self_enqueue_validation(goal_key, r["code_hash"])
            if r.get("script_path"):
                engine._register_in_manifest(r.get("name", goal_key), "python", r["script_path"], r.get("description", ""))
            engine._stasis = False; engine._prev_hashes.clear()
    else:
        engine._curiosity_fail_count[goal_key] = engine._curiosity_fail_count.get(goal_key, 0) + 1
        fail_n = engine._curiosity_fail_count[goal_key]
        if fail_n >= 3:
            _audit_log("L5", "curiosity_suppressed", f"{goal_key} failed {fail_n}x", success=False, duration_ms=dur)
        else:
            _audit_log("L5", "curiosity_cycle", f"{result.get('gaps', 0)} gaps, fail #{fail_n}", success=False, duration_ms=dur)
    if not result.get("skipped"):
        r = result.get("result", result)
        gaps_n = result.get("gaps", 0)
        parts = [f"갭 {gaps_n}건"]
        if r.get("success"): parts.append(f"'{result.get('goal', '?')}' 생성 성공")
        elif result.get("rejected"): parts.append(f"거부: {result['rejected']}")
        elif gaps_n > 0: parts.append(f"생성 실패: {r.get('reason', '원인 불명')}")
        else: parts.append("현재 도구 세트 충분")
        engine._dev_report("L5", ", ".join(parts))

# -- L6: Web Exploration --

def do_web_explore(engine):
    """Autonomous deep-dive: topic selection -> multi-round search -> act."""
    if DDGS is None:
        engine._stream("ddgs 미설치. 웹 탐색 건너뜀."); return
    engine._stream("🌐 관심 주제 선정중...")
    gaps = engine.curiosity.scan_gaps()
    skills = _jsonl_read(MEM_DIR / f"{SKILLS_STREAM}.jsonl", max_lines=10)
    knowledge = _jsonl_read(MEM_DIR / f"{KNOWLEDGE_STREAM}.jsonl", max_lines=5)
    exps = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=50)
    recent_fails = [e for e in exps if not e.get("success")][-5:]
    fail_summary = "; ".join(f"{e.get('tool_used','?')}: {e.get('result_preview','')[:40]}" for e in recent_fails) if recent_fails else "최근 실패 없음"
    ctx = []
    if gaps: ctx.append("갭: " + ", ".join(g.get("tool", g.get("type", "?")) for g in gaps[:3]))
    if recent_fails: ctx.append(f"최근 실패: {fail_summary}")
    if skills: ctx.append("보유 스킬: " + ", ".join(s.get("name", s.get("request", ""))[:30] for s in skills[-3:]))
    if knowledge: ctx.append("이미 검색한 것: " + ", ".join(k.get("query", "") for k in knowledge[-3:]))
    context = "\n".join(ctx) if ctx else "시스템 초기 상태"
    query_prompt = (
        f"현재 시스템:\n{context}\n\n"
        "시스템의 실제 문제 해결이나 성능 개선에 도움될 웹 검색 쿼리를 영어로 생성해.\n"
        "이미 검색한 것과 겹치지 않게.\n"
        'JSON: {"query":"search terms","goal":"한국어","reason":"한국어"}\nJSON만.'
    )
    raw = _call_engine_llm(query_prompt, system="유효한 JSON만 출력.",
                       max_tokens=300, temperature=0.3, format_json=True, think=False)
    try:
        q = json.loads(raw)
        query, goal, reason = q.get("query", ""), q.get("goal", ""), q.get("reason", q.get("goal", ""))
    except (json.JSONDecodeError, TypeError):
        engine._stream("쿼리 생성 실패."); return
    if not query or len(query) < 3: return
    engine._stream(f"🌐 딥다이브: '{query}'\n목표: {goal}")
    result = _deep_web_search(query, reason, "web_explore", max_rounds=3, goal=goal)
    if not result["success"]:
        engine._stream(f"검색 실패: {result.get('detail', '?')}"); return
    engine.level_done["web_explore"] = time.time()
    rounds, pages = result.get("rounds", 1), result.get("pages_read", 0)
    queries = result.get("queries_tried", [query])
    _audit_log("WEB", "deep_dive", f"rounds={rounds}, queries={queries}, pages={pages}")
    summary = result.get("summary", "")
    engine._stream(f"🌐 딥다이브 완료! {rounds}라운드, {pages}페이지\n요약: {summary[:300]}")
    engine._milestone(f"웹 탐색 완료: '{query[:40]}' ({rounds}라운드, {pages}페이지)")
    if summary and any(m in summary.lower() for m in _ACTION_MARKERS):
        try_apply_knowledge(engine, query, goal, summary)

def try_apply_knowledge(engine, query: str, goal: str, summary: str):
    """Act on web findings -- try to use them."""
    engine._stream("💡 학습 내용 적용 시도...")
    apply_prompt = (
        f"웹에서 '{query}'에 대해 학습한 내용:\n{summary[:STORE_SUMMARY_LEN]}\n\n"
        f"목표: {goal}\n\n"
        "어떤 행동이 가장 유용한가?\n"
        '{"action":"test_tool","tool":"도구명","input":"입력"}\n'
        '{"action":"read_code","file":"파일명.py","what":"확인 내용"}\n'
        '{"action":"remember","insight":"핵심 교훈"}\n'
        '{"action":"skip","reason":"이유"}\nJSON만.'
    )
    raw = _call_engine_llm(apply_prompt, system="자율 에이전트 개선 전문가. JSON만.",
                       max_tokens=300, temperature=0.3, format_json=True, think=False)
    try: action = json.loads(raw)
    except (json.JSONDecodeError, TypeError): return
    act = action.get("action", "skip")
    if act == "test_tool":
        tool, tool_input = action.get("tool", ""), action.get("input", "")
        if tool:
            r = engine._sq_do_tool_test(tool, tool_input)
            engine._stream(f"🔧 도구 테스트 '{tool}': {r.get('detail', '')[:100]}")
            _audit_log("WEB", "apply_test", f"{tool}: {r.get('detail', '')[:100]}", success=r.get("success", False))
    elif act == "read_code":
        fname, what = action.get("file", ""), action.get("what", "")
        if fname:
            src_path = Path(MACHINA_ROOT) / fname
            if src_path.exists() and src_path.suffix == ".py":
                try:
                    code = src_path.read_text(encoding="utf-8")
                    lines = code.split("\n")
                    relevant = []
                    for i, line in enumerate(lines):
                        if what.lower() in line.lower():
                            s, e = max(0, i-3), min(len(lines), i+10)
                            relevant.append(f"L{s+1}-{e}: " + "\n".join(lines[s:e]))
                            if len(relevant) >= 2: break
                    if relevant:
                        engine._stream(f"📖 {fname}에서 '{what}' 확인:\n{chr(10).join(relevant)[:1000]}")
                        _audit_log("WEB", "apply_read_code", f"{fname}: found '{what}'")
                    else:
                        engine._stream(f"📖 {fname}에서 '{what}' 관련 코드 없음")
                except Exception as e:
                    logger.debug(f"[Web] Code read failed: {e}")
    elif act == "remember":
        insight_text = action.get("insight", "")
        if insight_text and len(insight_text) > 10:
            _jsonl_append(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", {
                "ts_ms": int(time.time() * 1000), "event": "insight", "stream": INSIGHTS_STREAM,
                "type": "web_lesson", "reflection": insight_text[:STORE_SUMMARY_LEN],
                "source_query": query[:60], "source": "web_apply",
            })
            engine._stream(f"💾 교훈 기록: {insight_text[:100]}")
            _audit_log("WEB", "apply_remember", insight_text[:200])
