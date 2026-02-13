#!/usr/bin/env python3
"""Tests for CuriosityDriver fallback goal synthesis."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from machina_autonomic._stimulus import CuriosityDriver  # noqa: E402


class CuriosityFallbackTests(unittest.TestCase):
    def setUp(self):
        self.driver = CuriosityDriver(max_per_day=99, cooldown_sec=0)

    def test_synthesize_goal_fallback_on_invalid_json(self):
        gaps = [{
            "type": "high_failure_tool",
            "tool": "AID.FILE.READ.v1",
            "fail_count": 5,
            "fail_rate": 0.83,
            "sample_requests": ["read missing file"],
        }]
        with patch("machina_autonomic._stimulus._call_engine_llm", return_value="not-json"):
            goal = self.driver.synthesize_goal(gaps)
        self.assertTrue(goal.get("fallback"))
        self.assertIn("gap_repair", goal.get("name", ""))
        self.assertIn("TARGET_TOOL", goal.get("code", ""))
        self.assertEqual(goal.get("gap", {}).get("type"), "high_failure_tool")

    def test_synthesize_goal_fallback_on_missing_fields(self):
        gaps = [{
            "type": "untested_tool",
            "tools": ["AID.MCP.WEB_SEARCH.SEARCH.v1"],
            "count": 1,
        }]
        with patch("machina_autonomic._stimulus._call_engine_llm", return_value='{"name":"x"}'):
            goal = self.driver.synthesize_goal(gaps)
        self.assertTrue(goal.get("fallback"))
        self.assertIn("coverage", goal.get("name", ""))
        self.assertIn("untested_tool_coverage_plan", goal.get("code", ""))

    def test_synthesize_goal_passes_through_valid_llm_output(self):
        gaps = [{
            "type": "unhandled_capability",
            "count": 4,
            "sample_requests": ["need parser"],
        }]
        llm_json = '{"name":"smart_parser","description":"d","code":"print(1)"}'
        with patch("machina_autonomic._stimulus._call_engine_llm", return_value=llm_json):
            goal = self.driver.synthesize_goal(gaps)
        self.assertEqual(goal.get("name"), "smart_parser")
        self.assertEqual(goal.get("gap", {}).get("type"), "unhandled_capability")
        self.assertFalse(goal.get("fallback", False))


if __name__ == "__main__":
    unittest.main()
