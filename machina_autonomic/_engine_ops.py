"""Autonomic Engine — Operations (hygiene, rollback, log mgmt, metrics, self-evolve).
Functions take engine (AutonomicEngine) as first arg; static helpers standalone."""

import hashlib
import json
import os
import time
from pathlib import Path

from machina_shared import (
    _jsonl_append, _jsonl_read,
    _engine_llm_daily_calls,
    MACHINA_ROOT, MEM_DIR,
    EXPERIENCE_STREAM, INSIGHTS_STREAM, SKILLS_STREAM,
    get_brain_label,
)

from machina_autonomic._constants import (
    KNOWLEDGE_STREAM, AUDIT_LOG_FILE,
    _audit_log, _send_alert, logger,
)
from machina_autonomic._web import DDGS, _ACTION_MARKERS


# --- State hash ---

def state_hash(engine) -> str:
    """State fingerprint: skills + experiences + insights + curriculum + time bucket.

    Includes a 10-minute time bucket so stasis auto-expires even without new data.
    This prevents permanent stasis when L1/L2 don't write new records.
    """
    counts = {}
    for stream_name in (SKILLS_STREAM, EXPERIENCE_STREAM, INSIGHTS_STREAM):
        f = MEM_DIR / f"{stream_name}.jsonl"
        counts[stream_name] = 0
        if f.exists():
            with open(f, "r") as _fh:
                counts[stream_name] = sum(1 for _ in _fh)
    rates = engine.curriculum.get_rates()
    # 10-minute time bucket: hash changes every 10 minutes even with no data change
    time_bucket = int(time.time()) // 600
    key = (f"{counts[SKILLS_STREAM]}|{counts[EXPERIENCE_STREAM]}|"
           f"{counts[INSIGHTS_STREAM]}|"
           f"{rates.get('easy_success_rate',0):.2f}|"
           f"{rates.get('medium_success_rate',0):.2f}|"
           f"{time_bucket}")
    return hashlib.md5(key.encode()).hexdigest()[:8]


# --- Log Size Management ---

def check_log_sizes(engine):
    """Enforce log size limits. Max 2GB per file, 10GB total."""
    MAX_FILE = 2 * 1024 * 1024 * 1024
    MAX_TOTAL = 10 * 1024 * 1024 * 1024

    try:
        jsonl_files = sorted(MEM_DIR.glob("*.jsonl"),
                             key=lambda f: f.stat().st_size, reverse=True)
    except OSError:
        return
    total = sum(f.stat().st_size for f in jsonl_files)
    if total < MAX_TOTAL:
        return

    for fpath in jsonl_files:
        fsize = fpath.stat().st_size
        if fsize > MAX_FILE or total > MAX_TOTAL:
            try:
                import fcntl
                with open(fpath, "r+", encoding="utf-8") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        lines = f.readlines()
                        keep = max(len(lines) // 2, 10)
                        removed = len(lines) - keep
                        f.seek(0)
                        f.truncate()
                        f.writelines(lines[-keep:])
                        f.flush()
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                new_size = fpath.stat().st_size
                freed = fsize - new_size
                total -= freed
                logger.info(f"[LogRotation] {fpath.name}: {removed}건 정리, "
                            f"{freed // 1024}KB 확보")
                engine._stream(f"🧹 {fpath.name}: 오래된 {removed}건 정리")
            except Exception as e:
                logger.warning(f"[LogRotation] {fpath}: {e}")
        if total <= MAX_TOTAL * 0.8:
            break


# ---------------------------------------------------------------------------
# Level 4: Memory Hygiene
# ---------------------------------------------------------------------------

def do_hygiene(engine):
    """Rotate JSONL files, deduplicate, prune low-utility entries."""
    logger.info("[L4] Memory Hygiene")
    t0 = time.time()

    reward = engine.reward_tracker.detect_regression()
    engine.reward_tracker.snapshot()
    if reward.get("regressed"):
        suspects = engine.reward_tracker.find_suspects()
        logger.warning(f"[L4] Reward regression: delta={reward.get('delta')}, "
                       f"suspects={suspects}")
        _audit_log("L4", "reward_regression", f"delta={reward.get('delta')}, suspects={suspects}", success=False)
        _send_alert(f"⚠️ 보상 회귀 감지: 변화={reward.get('delta')}")
        auto_rollback_recent(engine)

    # Trust-scored experience rotation
    exp_file = MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl"
    if exp_file.exists():
        import fcntl
        with open(exp_file, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                entries = []
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                before = len(entries)
                entries = [e for e in entries if engine._trust_score(e) >= 0.1]
                pruned = before - len(entries)
                if pruned > 0:
                    f.seek(0)
                    f.truncate()
                    for e in entries:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
                    f.flush()
                    logger.info(f"[L4] Trust pruned {pruned} low-trust experiences")
                    _audit_log("L4", "trust_prune_exp", f"removed {pruned}")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    rotate(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", 2000)
    rotate(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", 1000)
    rotate(MEM_DIR / f"{KNOWLEDGE_STREAM}.jsonl", 1000)

    check_log_sizes(engine)

    # Deduplicate + trust-prune skills
    skills_file = MEM_DIR / f"{SKILLS_STREAM}.jsonl"
    if skills_file.exists():
        import fcntl
        with open(skills_file, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                entries = []
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                seen = set()
                unique = []
                trust_pruned = 0
                for e in entries:
                    h = e.get("code_hash", hashlib.sha256(e.get("code", "").encode()).hexdigest())
                    if h in seen:
                        continue
                    seen.add(h)
                    if engine._trust_score(e) < 0.1 and not e.get("used_count"):
                        trust_pruned += 1
                        continue
                    unique.append(e)
                f.seek(0)
                f.truncate()
                for e in unique[-100:]:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
                f.flush()
                if trust_pruned:
                    logger.info(f"[L4] Trust pruned {trust_pruned} low-trust skills")
                    _audit_log("L4", "trust_prune_skill", f"removed {trust_pruned}")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # Clean old executed suggestions
    suggest_file = MEM_DIR / "genesis_suggestions.jsonl"
    if suggest_file.exists():
        import fcntl
        now_ms = int(time.time() * 1000)
        with open(suggest_file, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                kept = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not s.get("executed") or now_ms - s.get("ts_ms", now_ms) < 86400000:
                        kept.append(s)
                f.seek(0)
                f.truncate()
                for s in kept:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # work/scripts retention: delete run_*.py older than 7 days
    scripts_dir = Path(MACHINA_ROOT) / "work" / "scripts"
    if scripts_dir.is_dir():
        cutoff = time.time() - 7 * 86400
        cleaned = 0
        for f in scripts_dir.glob("run_*.py"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    cleaned += 1
            except OSError as e:
                logger.debug(f"OSError: {e}")
        if cleaned:
            logger.info(f"[L4] Cleaned {cleaned} old run_*.py scripts")
            _audit_log("L4", "scripts_cleanup", f"removed {cleaned} old run_*.py")

    dur = int((time.time() - t0) * 1000)
    _audit_log("L4", "hygiene", "complete", duration_ms=dur)
    logger.info("[L4] Hygiene complete")
    engine._save_state()


def rotate(filepath: Path, max_lines: int):
    """Rotate a JSONL file, archiving evicted lines."""
    if not filepath.exists():
        return
    import fcntl
    with open(filepath, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            lines = f.readlines()
            if len(lines) <= max_lines:
                return
            evicted = lines[:-max_lines]
            if evicted:
                archive_path = filepath.parent / f"{filepath.stem}.archive{filepath.suffix}"
                try:
                    with open(archive_path, "a", encoding="utf-8") as af:
                        af.writelines(evicted)
                        af.flush()
                except OSError as ae:
                    logger.warning(f"[L4] Archive write failed for {filepath.name}: {ae}")
            f.seek(0)
            f.truncate()
            for line in lines[-max_lines:]:
                f.write(line)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    _audit_log("L4", "rotate", f"{filepath.name}: {len(lines)} → {max_lines}, archived {len(evicted)}")
    logger.info(f"[L4] Rotated {filepath.name}: {len(lines)} → {max_lines} (archived {len(evicted)})")


# ---------------------------------------------------------------------------
# Rollback Helpers
# ---------------------------------------------------------------------------

def rollback_artifact(engine, info: dict):
    """Remove util script + matching skill entry from skills.jsonl."""
    script_path = info.get("script_path", "")
    if script_path and os.path.exists(script_path):
        os.remove(script_path)
        logger.info(f"[Rollback] Deleted {os.path.basename(script_path)}")

    code_hash = info.get("code_hash", "")
    if not code_hash:
        return
    skills_file = MEM_DIR / f"{SKILLS_STREAM}.jsonl"
    if not skills_file.exists():
        return
    import fcntl
    with open(skills_file, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            entries = []
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    if e.get("code_hash") == code_hash:
                        continue
                    entries.append(e)
                except json.JSONDecodeError:
                    continue
            f.seek(0)
            f.truncate()
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    logger.info(f"[Rollback] Removed skill {code_hash[:8]}")


def auto_rollback_recent(engine):
    """Remove most recent skill when reward drops."""
    skills_file = MEM_DIR / f"{SKILLS_STREAM}.jsonl"
    if not skills_file.exists():
        return
    recent = _jsonl_read(skills_file, max_lines=5)
    if not recent:
        return
    last = recent[-1]
    code_hash = last.get("code_hash", "")
    req = last.get("request", "")[:60]
    if code_hash:
        rollback_artifact(engine, {"code_hash": code_hash})
        _audit_log("L4", "auto_rollback", f"'{req}' ({code_hash[:8]})", success=False)
        _send_alert(f"⚠️ 자동 롤백: '{req}'")
        logger.info(f"[L4] Auto-rollback: removed '{req}' ({code_hash[:8]})")


# ---------------------------------------------------------------------------
# Quality Metrics
# ---------------------------------------------------------------------------

def compute_quality_metrics(engine) -> dict:
    """Compute self-improvement quality metrics beyond pass-count.

    Returns:
      knowledge_action_ratio: % of knowledge entries with actionable content
      insight_novelty: % of recent insights that are unique (non-duplicate)
      curiosity_success_rate: % of curiosity goals that succeeded
      experience_success_rate: overall success rate of recent experiences
      memory_efficiency: total memory size in KB
    """
    metrics = {}

    # 1. Knowledge -> Action ratio
    knowledge = _jsonl_read(MEM_DIR / f"{KNOWLEDGE_STREAM}.jsonl", max_lines=50)
    if knowledge:
        actionable = sum(1 for k in knowledge
                         if any(m in k.get("summary", "").lower() for m in _ACTION_MARKERS))
        metrics["knowledge_action_ratio"] = round(actionable / len(knowledge), 2)
        metrics["knowledge_total"] = len(knowledge)
    else:
        metrics["knowledge_action_ratio"] = 0
        metrics["knowledge_total"] = 0

    # 2. Insight novelty (unique content ratio in last 50)
    insights = _jsonl_read(MEM_DIR / f"{INSIGHTS_STREAM}.jsonl", max_lines=50)
    if insights:
        seen_content = set()
        unique = 0
        for ins in insights:
            key = ins.get("reflection", ins.get("rules", ""))
            if isinstance(key, list):
                key = str(sorted(key))
            key_hash = hashlib.md5(str(key).encode()).hexdigest()[:12]
            if key_hash not in seen_content:
                unique += 1
                seen_content.add(key_hash)
        metrics["insight_novelty"] = round(unique / len(insights), 2)
        metrics["insight_total"] = len(insights)
    else:
        metrics["insight_novelty"] = 0
        metrics["insight_total"] = 0

    # 3. Curiosity success rate (from gap file)
    gap_file = MEM_DIR / "curiosity_gaps.jsonl"
    if gap_file.exists():
        gaps = _jsonl_read(gap_file, max_lines=50)
        attempted = [g for g in gaps if g.get("goal_name")]
        if attempted:
            succeeded = sum(1 for g in attempted if g.get("success"))
            metrics["curiosity_success_rate"] = round(succeeded / len(attempted), 2)
            metrics["curiosity_attempted"] = len(attempted)
        else:
            metrics["curiosity_success_rate"] = 0
            metrics["curiosity_attempted"] = 0
    else:
        metrics["curiosity_success_rate"] = 0
        metrics["curiosity_attempted"] = 0

    # 4. Recent experience success rate
    exps = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=100)
    if exps:
        ok = sum(1 for e in exps if e.get("success"))
        metrics["experience_success_rate"] = round(ok / len(exps), 2)
        metrics["experience_total"] = len(exps)
    else:
        metrics["experience_success_rate"] = 0
        metrics["experience_total"] = 0

    # 5. Memory efficiency (total JSONL size)
    total_kb = 0
    for f in MEM_DIR.glob("*.jsonl"):
        try:
            total_kb += f.stat().st_size // 1024
        except OSError as e:
            logger.debug(f"OSError: {e}")
    metrics["memory_kb"] = total_kb

    return metrics


# ---------------------------------------------------------------------------
# get_status + set_mode + run_once + run_forever
# ---------------------------------------------------------------------------

def get_status(engine) -> dict:
    """Return current autonomic state for /auto_status display."""
    idle = engine.idle_seconds()
    rates = engine.curriculum.get_rates()
    now = time.time()
    t = engine._t
    if idle >= t["l5_idle"]:
        current_level = "L5 (Curiosity)"
    elif idle >= t["l3_idle"]:
        current_level = "L3 (Heal)"
    elif idle >= t["l2_idle"]:
        current_level = "L2 (Test)"
    elif idle >= t["l1_idle"]:
        current_level = "L1 (Reflect)"
    else:
        current_level = "Idle (user active)"

    tp = engine._build_tool_profile()

    return {
        "idle_sec": int(idle),
        "current_level": current_level,
        "paused": engine.paused,
        "stasis": engine._stasis,
        "dev_explore": engine._dev,
        "level_done": {k: int(now - v) if v > 0 else -1
                       for k, v in engine.level_done.items()},
        "curriculum_rates": rates,
        "curiosity_daily": engine.curiosity.daily_count,
        "curiosity_max": engine.curiosity._max_per_day,
        "engine_backend": os.getenv("MACHINA_CHAT_BACKEND", "oai_compat"),
        "in_burst": engine._in_burst,
        "web_search": DDGS is not None,
        "engine_daily_calls": _engine_llm_daily_calls.get("count", 0),
        "engine_daily_tokens": _engine_llm_daily_calls.get("tokens", 0),
        "tool_profile": {
            "total": tp.get("total", 0),
            "tested": tp.get("tested", 0),
            "high_fail": len(tp.get("high_fail", [])),
            "hypotheses": len(tp.get("hypotheses", [])),
        },
        "quality_metrics": engine._compute_quality_metrics(),
    }


def set_mode(engine, dev: bool):
    """Switch between DEV EXPLORE and PRODUCTION mode at runtime."""
    from machina_autonomic._constants import set_dev_explore, _TIMINGS_DEV, _TIMINGS_NORMAL
    set_dev_explore(dev)
    engine._dev = dev
    engine._t = _TIMINGS_DEV if dev else _TIMINGS_NORMAL
    # Update curiosity driver limits
    engine.curiosity._max_per_day = engine._t["curiosity_max_per_day"]
    engine.curiosity._cooldown_sec = engine._t["curiosity_cooldown"]
    mode_name = "DEV EXPLORE" if dev else "PRODUCTION"
    logger.info(f"[Mode] Switched to {mode_name}")
    _audit_log("MODE", "mode_switch", f"→ {mode_name}")
    _send_alert(f"모드 전환: {mode_name}")


def run_once(engine):
    """Execute one full improvement cycle (for testing/cron)."""
    logger.info("=== Autonomic Engine: single cycle ===")
    engine.last_activity = 0
    engine.level_done = {k: 0 for k in engine.level_done}

    engine._do_reflect()
    engine._do_test_and_learn()
    engine._do_heal()
    engine._drain_inbox()
    engine._do_hygiene()
    engine._do_curiosity()

    rates = engine.curriculum.get_rates()
    logger.info(f"=== Cycle complete | Easy:{rates.get('easy_success_rate',0):.0%} "
                 f"Med:{rates.get('medium_success_rate',0):.0%} "
                 f"Hard:{rates.get('hard_success_rate',0):.0%} ===")


def run_forever(engine):
    """Main loop for standalone execution."""
    import signal
    import sys

    hb = engine._t["heartbeat"]
    mode_label = "DEV EXPLORE" if engine._dev else "PRODUCTION"
    logger.info(f"=== Autonomic Engine v5: {mode_label} mode ===")
    logger.info(f"  Brain: {get_brain_label()}")
    logger.info(f"  Memory: {MEM_DIR}")
    logger.info(f"  Heartbeat: {hb}s")
    if engine._dev:
        logger.info(f"  Timings: L1={engine._t['l1_idle']}s L2={engine._t['l2_idle']}s "
                     f"L3={engine._t['l3_idle']}s L5={engine._t['l5_idle']}s")
        logger.info(f"  Curiosity: {engine._t['curiosity_max_per_day']}/day, "
                     f"cooldown={engine._t['curiosity_cooldown']}s")

    pid_file = Path(MACHINA_ROOT) / "autonomic.pid"
    pid_file.write_text(str(os.getpid()))
    logger.info(f"  PID: {os.getpid()} → {pid_file}")

    def _graceful(signum, frame):
        logger.info(f"Signal {signum} received, shutting down")
        engine._save_state()
        pid_file.unlink(missing_ok=True)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    engine._load_state()

    if engine._dev:
        rates = engine.curriculum.get_rates()
        _send_alert(
            f"🟢 엔진 시작 | {mode_label}\n"
            f"🧠 {get_brain_label()}\n"
            f"⏱ 반성={engine._t['l1_idle']}초 테스트={engine._t['l2_idle']}초 "
            f"치유={engine._t['l3_idle']}초 탐구={engine._t['l5_idle']}초\n"
            f"🔧 탐구 {engine._t['curiosity_max_per_day']}회/일 | 버스트 {engine._t['burst_max_sec']//60}분\n"
            f"📈 초급={rates.get('easy_success_rate',0):.0%} "
            f"중급={rates.get('medium_success_rate',0):.0%} "
            f"고급={rates.get('hard_success_rate',0):.0%}")

    engine.run_once()
    engine._dev_status_ts = time.time()

    while True:
        try:
            time.sleep(hb)
            engine.tick()
            engine._save_state()

            if engine._dev and time.time() - engine._dev_status_ts >= 600:
                engine._dev_status_ts = time.time()
                s = engine.get_status()
                _LVL_KR = {"L1 (Reflect)": "반성", "L2 (Test)": "테스트",
                           "L3 (Heal)": "치유", "L5 (Curiosity)": "탐구",
                           "Idle (user active)": "대기"}
                _lvl = _LVL_KR.get(s['current_level'], s['current_level'])
                _send_alert(
                    f"📊 상태 | {_lvl} | 유휴 {s['idle_sec']}초\n"
                    f"{'⏸ 정체' if s['stasis'] else '▶ 활동'} | "
                    f"탐구 {s['curiosity_daily']}/{s['curiosity_max']}회\n"
                    f"📈 초급={s['curriculum_rates'].get('easy_success_rate',0):.0%} "
                    f"중급={s['curriculum_rates'].get('medium_success_rate',0):.0%} "
                    f"고급={s['curriculum_rates'].get('hard_success_rate',0):.0%}")
        except KeyboardInterrupt:
            logger.info("Shutting down gracefully")
            engine._save_state()
            if engine._dev:
                _send_alert("🔴 엔진 정지 (SIGINT)")
            pid_file.unlink(missing_ok=True)
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            _audit_log("ENGINE", "loop_error", str(e), success=False)
            engine._save_state()
            time.sleep(60)

    pid_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Self-Evolution: safe modification of bot's own source files
# ---------------------------------------------------------------------------
_SELF_EVOLVE = os.getenv("MACHINA_SELF_EVOLVE", "") == "1"


def self_evolve_patch(engine, file_path: str, old_text: str, new_text: str) -> dict:
    """Patch own source (.py) with safety: path check, backup, py_compile, auto-rollback.
    Requires MACHINA_SELF_EVOLVE=1."""
    _fail = lambda r: {"success": False, "reason": r}
    if not _SELF_EVOLVE:
        return _fail("MACHINA_SELF_EVOLVE not enabled")
    fp = Path(file_path).resolve()
    root = Path(MACHINA_ROOT).resolve()
    if not str(fp).startswith(str(root)):
        return _fail("path outside MACHINA_ROOT")
    if fp.suffix != ".py":
        return _fail("only .py files allowed")
    if not fp.exists():
        return _fail(f"file not found: {fp.name}")
    original = fp.read_text(encoding="utf-8")
    if old_text not in original:
        return _fail("old_text not found in file")
    if old_text == new_text:
        return _fail("no change")
    bak = fp.with_suffix(".py.evolve_bak")
    bak.write_text(original, encoding="utf-8")
    patched = original.replace(old_text, new_text, 1)
    fp.write_text(patched, encoding="utf-8")
    import py_compile
    try:
        py_compile.compile(str(fp), doraise=True)
    except py_compile.PyCompileError as e:
        fp.write_text(original, encoding="utf-8")
        _audit_log("EVOLVE", "patch_rollback", f"compile fail: {e}", success=False)
        engine._milestone(f"자가진화 실패(컴파일): {fp.name}")
        return _fail(f"compile error, rolled back: {e}")
    desc = f"{fp.name}: {len(old_text)}→{len(new_text)} chars"
    _audit_log("EVOLVE", "patch_applied", desc, success=True)
    engine._milestone(f"자가진화 성공: {fp.name} 패치 적용")
    logger.info(f"[EVOLVE] Patched {desc}")
    return {"success": True, "file": fp.name, "backup": bak.name}
