"""Machina Autonomic Engine — AutonomicEngine class (core orchestrator).

This file contains the class shell with __init__, tick(), touch(), idle_seconds()
and thin wrappers that delegate to extracted modules:
  - _engine_levels.py  — Level handlers (L1-L6), tool profile, helpers
  - _engine_burst.py   — Burst mode, stimulus handlers, manifest, inbox
  - _engine_ops.py     — Hygiene, rollback, log management, metrics, run loops
  - _sq.py             — Self-Questioning (SQ) module
"""

import json
import sys
import time
from pathlib import Path

from machina_shared import MACHINA_ROOT, MEM_DIR
from machina_gvu import (
    SelfQuestioner, SelfTester, SelfHealer, CurriculumTracker,
    RegressionGate,
)
from machina_learning import RewardTracker

from machina_autonomic._constants import (
    _DEV_EXPLORE, _CLOUD_AUTO, _TIMINGS_DEV, _TIMINGS_NORMAL,
    _audit_log, _send_alert, logger,
)
from machina_autonomic._stimulus import CuriosityDriver, RandomStimulus
from machina_autonomic._sq import (
    llm_self_question as _sq_llm_self_question,
    sq_do_search, sq_do_tool_test, sq_do_code, sq_do_reflect,
    sq_do_audit, sq_mem_save, sq_mem_query, sq_file_read, sq_file_list,
)

# Extracted module imports
from machina_autonomic import _engine_levels as _levels
from machina_autonomic import _engine_burst as _burst
from machina_autonomic import _engine_ops as _ops


class AutonomicEngine:
    """Standalone self-improving engine. Can run without Telegram.

    Lifecycle:
      tick() → self_question → self_test → analyze → self_heal → record
      ↑                                                              ↓
      └──────────────── curriculum update ◄──────────────────────────┘
    """

    def __init__(self):
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        self._dev = _DEV_EXPLORE
        self._cloud = _CLOUD_AUTO
        self._t = _TIMINGS_DEV if self._dev else _TIMINGS_NORMAL
        self.questioner = SelfQuestioner()
        self.tester = SelfTester()
        self.healer = SelfHealer()
        self.curriculum = CurriculumTracker()
        self.regression_gate = RegressionGate()
        self.reward_tracker = RewardTracker()
        self.curiosity = CuriosityDriver(
            regression_gate=self.regression_gate,
            max_per_day=self._t["curiosity_max_per_day"],
            cooldown_sec=self._t["curiosity_cooldown"],
            stream_fn=self._stream,
        )
        self.stimulus = RandomStimulus()
        self._sq_recent: list[str] = []   # last N self-question actions (dedup)
        self._sq_count = 0                # self-questions this burst session
        self._sq_last_ts = 0.0            # last SQ execution timestamp
        self._sq_noop_streak = 0          # consecutive SQ no-op count
        self._sq_fail_streak = 0          # consecutive SQ failure count
        self._sq_last_backoff_log = 0.0   # rate-limit SQ backoff logs
        self._tool_profile_cache = None   # cached tool profile (refreshed per burst)
        self._tool_profile_ts = 0
        self._last_reflect_hash = ""     # detect stale L1 reflect
        self._curiosity_fail_count = {}  # gap → consecutive failures
        self.last_activity = time.time()
        self.paused = False
        self.level_done = {
            "reflect": 0, "test": 0, "heal": 0, "hygiene": 0, "curiosity": 0,
            "web_explore": 0, "_burst": 0,
        }
        self._prev_hashes = []  # Phase 2: stasis detection
        self._stasis = False
        self._stasis_entered = 0.0  # timestamp when stasis began
        self._in_burst = False
        self._dev_last_report = {}  # category → timestamp for rate limiting

        # Restore state from previous run (works in both bot and standalone mode)
        self._load_state()

    def touch(self):
        """Call on every user message to reset idle timer."""
        self.last_activity = time.time()
        self.paused = False
        self._stasis = False  # user activity breaks stasis

    def idle_seconds(self) -> float:
        return time.time() - self.last_activity

    # --- Delegated: Tool Introspection (_engine_levels) ---
    def _build_tool_profile(self) -> dict:
        return _levels.build_tool_profile(self)

    def _build_dynamic_queries(self) -> list:
        return _levels.build_dynamic_queries(self)

    def _cloud_rate_factor(self) -> float:
        return _levels.cloud_rate_factor(self)

    def _trust_score(self, entry: dict) -> float:
        return _levels.trust_score(self, entry)

    def _dev_report(self, category: str, message: str):
        return _levels.dev_report(self, category, message)

    def _stream(self, message: str):
        return _levels.stream(self, message)

    def _milestone(self, message: str):
        return _levels.milestone(self, message)

    # --- Delegated: Level handlers (_engine_levels) ---
    def _do_reflect(self):
        return _levels.do_reflect(self)

    def _do_test_and_learn(self, abort_check=None):
        return _levels.do_test_and_learn(self, abort_check)

    def _do_heal(self):
        return _levels.do_heal(self)

    def _do_curiosity(self):
        return _levels.do_curiosity(self)

    def _do_web_explore(self):
        return _levels.do_web_explore(self)

    def _try_apply_knowledge(self, query: str, goal: str, summary: str):
        return _levels.try_apply_knowledge(self, query, goal, summary)

    # --- Delegated: Burst mode + stimulus (_engine_burst) ---
    _MANIFEST_PATH = Path(MACHINA_ROOT) / "work" / "scripts" / "utils" / "manifest.json"

    def _register_in_manifest(self, name: str, lang: str, path: str, description: str = ""):
        return _burst.register_in_manifest(self, name, lang, path, description)

    def _unregister_from_manifest(self, name: str):
        return _burst.unregister_from_manifest(self, name)

    _QUEUE_DIR = Path(MACHINA_ROOT) / "work" / "queue"

    def _self_enqueue_validation(self, skill_name: str, code_hash: str):
        return _burst.self_enqueue_validation(self, skill_name, code_hash)

    def _drain_inbox(self, max_jobs: int = 3):
        return _burst.drain_inbox(self, max_jobs)

    def _autonomous_burst(self, abort_check=None):
        return _burst.autonomous_burst(self, abort_check)

    def _pick_next_action(self, abort_check=None):
        return _burst.pick_next_action(self, abort_check)

    def _execute_stimulus(self, stim: dict):
        return _burst.execute_stimulus(self, stim)

    def _stim_web(self, stim: dict) -> dict:
        return _burst.stim_web(self, stim)

    def _stim_tool_test(self, stim: dict) -> dict:
        return _burst.stim_tool_test(self, stim)

    def _stim_integration(self, stim: dict) -> dict:
        return _burst.stim_integration(self, stim)

    def _stim_benchmark(self, stim: dict) -> dict:
        return _burst.stim_benchmark(self, stim)

    # --- Delegated: SQ (_sq.py) ---
    _SQ_CONSECUTIVE_DEDUP = 0

    def _llm_self_question(self):
        return _sq_llm_self_question(self)

    def _sq_do_search(self, query, reason):
        return sq_do_search(self, query, reason)

    def _sq_do_tool_test(self, tool, tool_input):
        return sq_do_tool_test(self, tool, tool_input)

    def _sq_do_code(self, code):
        return sq_do_code(self, code)

    def _sq_do_reflect(self, topic, reason):
        return sq_do_reflect(self, topic, reason)

    def _sq_do_audit(self, tool, test_type, reason):
        return sq_do_audit(self, tool, test_type, reason)

    def _sq_mem_save(self, text):
        return sq_mem_save(self, text)

    def _sq_mem_query(self, query):
        return sq_mem_query(self, query)

    def _sq_file_read(self, path):
        return sq_file_read(self, path)

    def _sq_file_list(self):
        return sq_file_list(self)

    # --- Delegated: Operations (_engine_ops) ---
    def _state_hash(self) -> str:
        return _ops.state_hash(self)

    def _check_log_sizes(self):
        return _ops.check_log_sizes(self)

    def _do_hygiene(self):
        return _ops.do_hygiene(self)

    @staticmethod
    def _rotate(filepath: Path, max_lines: int):
        return _ops.rotate(filepath, max_lines)

    def _rollback_artifact(self, info: dict):
        return _ops.rollback_artifact(self, info)

    def _auto_rollback_recent(self):
        return _ops.auto_rollback_recent(self)

    def _compute_quality_metrics(self) -> dict:
        return _ops.compute_quality_metrics(self)

    def get_status(self) -> dict:
        return _ops.get_status(self)

    def self_evolve_patch(self, file_path: str, old_text: str, new_text: str) -> dict:
        return _ops.self_evolve_patch(self, file_path, old_text, new_text)

    def set_mode(self, dev: bool):
        return _ops.set_mode(self, dev)

    def run_once(self):
        return _ops.run_once(self)

    def run_forever(self):
        return _ops.run_forever(self)

    # --- tick: core orchestrator (kept in this file) ---
    def tick(self, abort_check=None):
        """Single heartbeat — triggers appropriate level based on idle duration."""
        if self.paused:
            return

        # Set trace context for this tick — all audit logs within inherit it
        from machina_autonomic._constants import new_trace_id, new_span_id, set_trace_context
        tick_trace = new_trace_id()
        tick_span = new_span_id()
        set_trace_context(tick_trace, tick_span)

        # Stasis detection with auto-expiry (prevents L2/L3 deadlock)
        stasis_max_sec = 600  # 10min (DEV and PROD)
        if self._stasis and self._stasis_entered and (time.time() - self._stasis_entered) >= stasis_max_sec:
            self._stasis = False
            self._stasis_entered = 0.0
            self._prev_hashes.clear()
            logger.info(f"[Engine] Stasis auto-expired after {stasis_max_sec}s — L2/L3 unlocked")
            _audit_log("ENGINE", "stasis_expire", f"auto-expired after {stasis_max_sec}s")
            self._dev_report("STASIS", f"정체 자동 해제 ({stasis_max_sec//60}분). L2/L3 재개.")

        sth = self._t["stasis_threshold"]
        current_hash = self._state_hash()
        self._prev_hashes.append(current_hash)
        if len(self._prev_hashes) > sth:
            self._prev_hashes = self._prev_hashes[-sth:]
        if len(self._prev_hashes) == sth and len(set(self._prev_hashes)) == 1:
            if not self._stasis:
                self._stasis = True
                self._stasis_entered = time.time()
                logger.info(f"[Engine] Stasis detected (hash={current_hash}, threshold={sth})")
                _audit_log("ENGINE", "stasis_enter", f"hash={current_hash}")
                self._dev_report("STASIS", f"정체 감지 (hash={current_hash}). L2/L3 일시정지 (최대 {stasis_max_sec//60}분).")

        idle = self.idle_seconds()
        now = time.time()
        t = self._t
        cf = self._cloud_rate_factor()

        try:
            # Level 1: Self-Reflect
            if idle >= t["l1_idle"] * cf and now - self.level_done.get("reflect", 0) >= t["l1_rate"] * cf:
                self._do_reflect()
                # Only set to now if _do_reflect didn't already push a future cooldown
                if self.level_done.get("reflect", 0) <= now:
                    self.level_done["reflect"] = now

            # Level 2: Self-Test + Feedback Loop
            if not self._stasis and idle >= t["l2_idle"] * cf and now - self.level_done.get("test", 0) >= t["l2_rate"] * cf:
                self._do_test_and_learn(abort_check)
                self.level_done["test"] = now

            # Level 3: Self-Heal
            if not self._stasis and idle >= t["l3_idle"] * cf and now - self.level_done.get("heal", 0) >= t["l3_rate"] * cf:
                self._do_heal()
                self.level_done["heal"] = now

            # Inbox drain
            self._drain_inbox(max_jobs=2)

            # Level 4: Memory Hygiene
            if now - self.level_done.get("hygiene", 0) >= t["l4_rate"] * cf:
                self._do_hygiene()
                self.level_done["hygiene"] = now

            # Level 5: Curiosity
            curiosity_due = now - self.level_done.get("curiosity", 0)
            if self._stasis:
                can_curiosity = curiosity_due >= t["stasis_curiosity_rate"] * cf
                if can_curiosity:
                    self._stream("정체 상태지만 자가진화 시도... 새로운 도구 탐색")
            else:
                can_curiosity = idle >= t["l5_idle"] * cf and curiosity_due >= t["l5_rate"] * cf
            if can_curiosity:
                self._do_curiosity()
                self.level_done["curiosity"] = now

            # Level 6: Web Exploration (independent of burst)
            web_rate = t.get("web_explore_rate", 1800) * cf
            if idle >= t["l5_idle"] * cf and now - self.level_done.get("web_explore", 0) >= web_rate:
                self._do_web_explore()

            # Multi-turn burst mode
            burst_idle = t.get("burst_idle", 7200) * cf
            burst_rate = t.get("burst_rate", 7200) * cf
            if (idle >= burst_idle and not self._in_burst
                    and now - self.level_done.get("_burst", 0) >= burst_rate):
                self._autonomous_burst(abort_check)

        except Exception as e:
            logger.error(f"[Engine] tick error: {e}")
            _audit_log("ENGINE", "tick_error", str(e), success=False)
            _send_alert(f"⚠️ 엔진 오류: {e}")

    # --- State Persistence ---
    _STATE_FILE = MEM_DIR / "autonomic_state.json"

    def _save_state(self):
        """Persist level_done timestamps and stasis state to disk."""
        try:
            state = {
                "level_done": self.level_done,
                "stasis": self._stasis,
                "prev_hashes": self._prev_hashes,
                "saved_ts": int(time.time()),
            }
            with open(self._STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.warning(f"[Engine] State save failed: {e}")

    def _load_state(self):
        """Restore level_done timestamps from disk if available."""
        try:
            if self._STATE_FILE.exists():
                with open(self._STATE_FILE) as f:
                    state = json.load(f)
                saved_level = state.get("level_done", {})
                for k in self.level_done:
                    if k in saved_level:
                        self.level_done[k] = saved_level[k]
                # Don't restore stasis from disk — start fresh to avoid deadlock.
                # Stasis will be re-detected naturally if state hash stays unchanged.
                self._stasis = False
                self._stasis_entered = 0.0
                self._prev_hashes = []
                logger.info(f"[Engine] Restored state from {self._STATE_FILE.name}")
        except Exception as e:
            logger.warning(f"[Engine] State load failed: {e}")


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    engine = AutonomicEngine()
    if "--once" in sys.argv:
        engine.run_once()
    else:
        engine.run_forever()
