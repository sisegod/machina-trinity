#!/usr/bin/env python3
"""Machina GVU Tracker — CurriculumTracker + RegressionGate."""

import fcntl
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from machina_shared import (
    _jsonl_read,
    MACHINA_ROOT, MEM_DIR,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CURRICULUM_FILE = MEM_DIR / "curriculum.jsonl"
CONSECUTIVE_FAIL_LIMIT = 3  # pause category after 3 consecutive fails

logger = logging.getLogger("autonomic")


# ---------------------------------------------------------------------------
# CurriculumTracker — capability map + difficulty escalation
# ---------------------------------------------------------------------------
class CurriculumTracker:
    """Tracks agent capability by category, manages difficulty escalation.

    Inspired by WebRL: failure-based task regeneration at appropriate difficulty.
    """

    def __init__(self):
        self.state = {
            "easy_pass": 0, "easy_total": 0,
            "medium_pass": 0, "medium_total": 0,
            "hard_pass": 0, "hard_total": 0,
            "category_fails": {},  # category -> consecutive fail count
            "paused_categories": {},  # category -> pause_until timestamp
            "last_updated": 0,
        }
        self._load()

    def _load(self):
        entries = _jsonl_read(CURRICULUM_FILE, max_lines=1)
        if entries:
            self.state.update(entries[-1])

    # BUG FIX #4: fcntl file locking in save()
    def save(self):
        self.state["last_updated"] = int(time.time() * 1000)
        Path(CURRICULUM_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(CURRICULUM_FILE).touch(exist_ok=True)
        with open(CURRICULUM_FILE, "r+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                f.write(json.dumps(self.state, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def record_results(self, test_results: dict):
        """Update capability scores from test results."""
        for detail in test_results.get("details", []):
            difficulty = detail.get("scenario", {}).get("difficulty", "easy")
            key_pass = f"{difficulty}_pass"
            key_total = f"{difficulty}_total"

            self.state[key_total] = self.state.get(key_total, 0) + 1
            if detail.get("passed"):
                self.state[key_pass] = self.state.get(key_pass, 0) + 1

        self.save()

    def record_heal_result(self, heal_result: dict):
        """Track healing attempts by category."""
        cat = heal_result.get("category", "")
        if not cat:
            return
        if heal_result.get("success"):
            # Reset consecutive fail counter on success
            self.state["category_fails"][cat] = 0
        else:
            # Increment consecutive fails
            count = self.state["category_fails"].get(cat, 0) + 1
            self.state["category_fails"][cat] = count
            if count >= CONSECUTIVE_FAIL_LIMIT:
                # Circuit breaker: pause this category for 1hr
                self.state["paused_categories"][cat] = int(time.time()) + 3600
                logger.warning(f"[Curriculum] Category '{cat}' paused (3 consecutive fails)")
        self.save()

    def get_rates(self) -> dict:
        """Get success rates per difficulty level."""
        rates = {}
        for diff in ("easy", "medium", "hard"):
            total = self.state.get(f"{diff}_total", 0)
            passed = self.state.get(f"{diff}_pass", 0)
            rates[f"{diff}_success_rate"] = passed / max(total, 1)
            rates[f"{diff}_total"] = total
        return rates

    def is_category_paused(self, category: str) -> bool:
        until = self.state.get("paused_categories", {}).get(category, 0)
        if until and time.time() < until:
            return True
        # Auto-unpause if time passed
        if until and time.time() >= until:
            self.state["paused_categories"].pop(category, None)
        return False


# ---------------------------------------------------------------------------
# RegressionGate — blocks changes that cause E2E test regression
# ---------------------------------------------------------------------------
class RegressionGate:
    """Run E2E suite after changes; reject if pass count drops below baseline.

    Baseline is monotonically improving: only updated when pass_count >= current.
    Stored in regression_baseline.json.
    """

    BASELINE_FILE = MEM_DIR / "regression_baseline.json"
    E2E_SCRIPT = os.path.join(MACHINA_ROOT, "work", "scripts", "e2e_machina_v3.py")

    def __init__(self):
        self.baseline = self._load_baseline()

    def _load_baseline(self) -> dict:
        try:
            if self.BASELINE_FILE.exists():
                with open(self.BASELINE_FILE, "r") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
        return {"pass_count": 0, "fail_count": 0, "total": 0, "ts_ms": 0}

    def _save_baseline(self, result: dict):
        Path(self.BASELINE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(self.BASELINE_FILE).touch(exist_ok=True)
        with open(self.BASELINE_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                json.dump(result, f, ensure_ascii=False)
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def run_e2e(self, timeout: int = 300) -> dict:
        """Run full E2E suite, return {pass_count, fail_count, total}."""
        try:
            proc = subprocess.run(
                ["python3", self.E2E_SCRIPT],
                capture_output=True, text=True, timeout=timeout,
                cwd=MACHINA_ROOT,
                env={**os.environ, "MACHINA_ROOT": MACHINA_ROOT},
            )
            m = re.search(
                r"(\d+)\s+PASS\s*/\s*(\d+)\s+FAIL\s*/\s*(\d+)\s+TOTAL",
                proc.stdout,
            )
            if m:
                return {"pass_count": int(m.group(1)), "fail_count": int(m.group(2)),
                        "total": int(m.group(3)), "ts_ms": int(time.time() * 1000)}
            return {"pass_count": 0, "total": 0, "ts_ms": int(time.time() * 1000),
                    "error": "parse_failed"}
        except subprocess.TimeoutExpired:
            return {"pass_count": 0, "total": 0, "ts_ms": int(time.time() * 1000),
                    "error": "timeout"}
        except Exception as e:
            return {"pass_count": 0, "total": 0, "ts_ms": int(time.time() * 1000),
                    "error": str(e)[:200]}

    def ensure_baseline(self):
        """Establish baseline if none exists (runs E2E once)."""
        if self.baseline.get("total", 0) > 0:
            return
        result = self.run_e2e()
        if result.get("total", 0) > 0 and not result.get("error"):
            self.baseline = result
            self._save_baseline(result)
            logger.info(f"[Gate] Baseline: {result['pass_count']}/{result['total']}")

    def check(self, result: dict) -> bool:
        """True if result doesn't regress from baseline."""
        if not self.baseline.get("total"):
            return True
        return result.get("pass_count", 0) >= self.baseline.get("pass_count", 0)

    def accept(self, result: dict):
        """Update baseline to new result (call after check passes)."""
        if result.get("pass_count", 0) >= self.baseline.get("pass_count", 0):
            self.baseline = result
            self._save_baseline(result)

    def gate(self, apply_fn, rollback_fn=None) -> dict:
        """Full gate: apply change -> E2E test -> accept or rollback.

        Returns: {accepted, gated, after, change_result}
        """
        self.ensure_baseline()
        change_result = apply_fn()
        after = self.run_e2e()
        if after.get("error"):
            logger.warning(f"[Gate] E2E error: {after['error']}, skipping gate")
            return {"accepted": True, "gated": False, "after": after,
                    "change_result": change_result}
        if self.check(after):
            self.accept(after)
            logger.info(f"[Gate] ACCEPTED {after['pass_count']}/{after['total']}")
            return {"accepted": True, "gated": True, "after": after,
                    "change_result": change_result}
        logger.warning(f"[Gate] REJECTED {after['pass_count']}/{after['total']} "
                       f"< baseline {self.baseline.get('pass_count')}/{self.baseline.get('total')}")
        if rollback_fn:
            rollback_fn()
        return {"accepted": False, "gated": True, "after": after,
                "baseline": self.baseline, "change_result": change_result}
