#!/usr/bin/env python3
"""Tests for SQ meaningful-result gate."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from machina_autonomic._sq import _is_meaningful_sq_result  # noqa: E402


class SqMeaningfulnessTests(unittest.TestCase):
    def test_meaningful_success(self):
        r = {"success": True, "detail": "schema probe found missing required field: path"}
        self.assertTrue(_is_meaningful_sq_result("audit", r))

    def test_noop_already_learned(self):
        r = {"success": True, "detail": "'foo' 이미 24시간 내 학습됨"}
        self.assertFalse(_is_meaningful_sq_result("search", r))

    def test_noop_blocked(self):
        r = {"success": True, "detail": "자율 모드에서 'shell_exec' 차단 (ASK 권한 필요)"}
        self.assertFalse(_is_meaningful_sq_result("test_tool", r))

    def test_failed_not_meaningful(self):
        r = {"success": False, "detail": "timeout"}
        self.assertFalse(_is_meaningful_sq_result("search", r))


if __name__ == "__main__":
    unittest.main()
