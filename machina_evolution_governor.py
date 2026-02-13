#!/usr/bin/env python3
"""Automatic change governance with strict rollback-oriented decisions."""

from dataclasses import dataclass, field
from typing import Any
import itertools
import time

from machina_evolution_policy import check_immutable_guardrails


_ID_GEN = itertools.count(1)


@dataclass
class ChangeProposal:
    kind: str
    changes: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    proposal_id: str = ""
    ts_ms: int = 0


@dataclass
class ChangeEvaluation:
    proposal_id: str
    allowed: bool
    stage: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


class EvolutionGovernor:
    """In-memory governor for automated change proposals."""

    def __init__(self):
        self._proposals: dict[str, ChangeProposal] = {}
        self._evaluations: dict[str, ChangeEvaluation] = {}
        self._state: dict[str, str] = {}  # proposal_id -> proposed|canary|committed|rolled_back|rejected

    def submit_proposal(self, proposal: ChangeProposal) -> str:
        pid = f"cp{next(_ID_GEN):06d}"
        proposal.proposal_id = pid
        proposal.ts_ms = int(time.time() * 1000)
        self._proposals[pid] = proposal
        self._state[pid] = "proposed"
        return pid

    def evaluate_proposal(self, proposal_id: str) -> ChangeEvaluation:
        p = self._proposals[proposal_id]
        pol = check_immutable_guardrails(p.changes)
        if not pol.allowed:
            ev = ChangeEvaluation(
                proposal_id=proposal_id,
                allowed=False,
                stage="policy",
                reason=pol.reason,
                details={"violations": pol.violations},
            )
            self._evaluations[proposal_id] = ev
            self._state[proposal_id] = "rejected"
            return ev
        ev = ChangeEvaluation(
            proposal_id=proposal_id,
            allowed=True,
            stage="policy",
            reason="ok",
            details={},
        )
        self._evaluations[proposal_id] = ev
        self._state[proposal_id] = "canary"
        return ev

    def commit_or_rollback(self, proposal_id: str, canary_ok: bool) -> dict[str, Any]:
        if self._state.get(proposal_id) in ("rejected", "rolled_back"):
            return {"ok": False, "state": self._state.get(proposal_id), "action": "none"}
        if canary_ok:
            self._state[proposal_id] = "committed"
            return {"ok": True, "state": "committed", "action": "commit"}
        self._state[proposal_id] = "rolled_back"
        return {"ok": True, "state": "rolled_back", "action": "rollback"}

    def get_state(self, proposal_id: str) -> str:
        return self._state.get(proposal_id, "unknown")
