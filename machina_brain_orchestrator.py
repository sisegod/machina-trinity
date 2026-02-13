#!/usr/bin/env python3
"""Automatic backend/model switch orchestration with cooldown and daily cap."""

from dataclasses import dataclass
import time


@dataclass
class BrainHealth:
    failure_rate: float
    timeout_rate: float
    parse_error_rate: float
    latency_ms_p95: int


@dataclass
class SwitchDecision:
    should_switch: bool
    reason: str
    target_backend: str = ""


class BrainOrchestrator:
    def __init__(self, cooldown_sec: int = 1800, daily_max: int = 6):
        self.cooldown_sec = cooldown_sec
        self.daily_max = daily_max
        self._last_switch_ts = 0
        self._switches_today = 0
        self._day_key = time.strftime("%Y-%m-%d")

    def score_brain_health(self, h: BrainHealth) -> float:
        # Lower is better; values > 0.55 indicate degraded quality.
        return (
            0.35 * h.failure_rate
            + 0.25 * h.timeout_rate
            + 0.2 * h.parse_error_rate
            + 0.2 * min(h.latency_ms_p95 / 12000.0, 1.0)
        )

    def _refresh_day(self):
        now_key = time.strftime("%Y-%m-%d")
        if now_key != self._day_key:
            self._day_key = now_key
            self._switches_today = 0

    def decide_switch(self, health: BrainHealth, current_backend: str) -> SwitchDecision:
        self._refresh_day()
        score = self.score_brain_health(health)
        if score < 0.55:
            return SwitchDecision(False, "healthy")
        if self._switches_today >= self.daily_max:
            return SwitchDecision(False, "daily_cap_reached")
        now = int(time.time())
        if now - self._last_switch_ts < self.cooldown_sec:
            return SwitchDecision(False, "cooldown_active")
        target = "anthropic" if current_backend != "anthropic" else "oai_compat"
        return SwitchDecision(True, "degraded_health", target_backend=target)

    def apply_switch(self, decision: SwitchDecision, switch_ok: bool) -> dict:
        if not decision.should_switch:
            return {"ok": False, "applied": False, "reason": decision.reason}
        if switch_ok:
            self._last_switch_ts = int(time.time())
            self._switches_today += 1
            return {"ok": True, "applied": True, "backend": decision.target_backend}
        return {"ok": False, "applied": False, "reason": "switch_failed_rollback"}
