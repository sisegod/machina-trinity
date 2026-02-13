#!/usr/bin/env python3
"""Tests for evolution governor and brain orchestrator."""

import unittest

from machina_evolution_governor import ChangeProposal, EvolutionGovernor
from machina_evolution_policy import check_immutable_guardrails
from machina_brain_orchestrator import BrainHealth, BrainOrchestrator


class EvolutionPolicyTests(unittest.TestCase):
    def test_immutable_guardrail_blocks_sensitive_keys(self):
        d = check_immutable_guardrails({"MACHINA_ROOT": "/tmp/x", "X": 1})
        self.assertFalse(d.allowed)
        self.assertIn("MACHINA_ROOT", d.violations)


class EvolutionGovernorTests(unittest.TestCase):
    def test_governor_commit_and_rollback(self):
        g = EvolutionGovernor()
        pid = g.submit_proposal(ChangeProposal(kind="tool", changes={"A": 1}))
        ev = g.evaluate_proposal(pid)
        self.assertTrue(ev.allowed)
        r = g.commit_or_rollback(pid, canary_ok=True)
        self.assertTrue(r["ok"])
        self.assertEqual(g.get_state(pid), "committed")

        pid2 = g.submit_proposal(ChangeProposal(kind="tool", changes={"B": 2}))
        g.evaluate_proposal(pid2)
        r2 = g.commit_or_rollback(pid2, canary_ok=False)
        self.assertTrue(r2["ok"])
        self.assertEqual(g.get_state(pid2), "rolled_back")

    def test_governor_rejects_immutable_change(self):
        g = EvolutionGovernor()
        pid = g.submit_proposal(ChangeProposal(kind="cfg", changes={"MACHINA_ROOT": "/bad"}))
        ev = g.evaluate_proposal(pid)
        self.assertFalse(ev.allowed)
        self.assertEqual(g.get_state(pid), "rejected")


class BrainOrchestratorTests(unittest.TestCase):
    def test_switch_decision_and_apply(self):
        bo = BrainOrchestrator(cooldown_sec=1, daily_max=2)
        h = BrainHealth(failure_rate=0.9, timeout_rate=0.8, parse_error_rate=0.7, latency_ms_p95=9000)
        d = bo.decide_switch(h, current_backend="oai_compat")
        self.assertTrue(d.should_switch)
        a = bo.apply_switch(d, switch_ok=True)
        self.assertTrue(a["ok"])
        self.assertTrue(a["applied"])

    def test_no_switch_when_healthy(self):
        bo = BrainOrchestrator()
        h = BrainHealth(failure_rate=0.1, timeout_rate=0.1, parse_error_rate=0.05, latency_ms_p95=800)
        d = bo.decide_switch(h, current_backend="oai_compat")
        self.assertFalse(d.should_switch)


if __name__ == "__main__":
    unittest.main()
