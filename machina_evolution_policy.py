#!/usr/bin/env python3
"""Evolution policy and immutable guardrail checks."""

from dataclasses import dataclass, field


IMMUTABLE_KEYS = {
    "MACHINA_PERMISSION_MODE",
    "MACHINA_PERMISSION_OVERRIDES",
    "MACHINA_ROOT",
    "MACHINA_BWRAP_REQUIRED",
}


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""
    violations: list[str] = field(default_factory=list)


def check_immutable_guardrails(changes: dict) -> PolicyDecision:
    """Reject proposal if it attempts to mutate immutable safety keys."""
    violations = [k for k in changes.keys() if k in IMMUTABLE_KEYS]
    if violations:
        return PolicyDecision(
            allowed=False,
            reason="immutable_guardrail_violation",
            violations=sorted(violations),
        )
    return PolicyDecision(allowed=True, reason="ok", violations=[])
