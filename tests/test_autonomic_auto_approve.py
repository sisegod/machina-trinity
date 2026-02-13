#!/usr/bin/env python3
"""Tests for autonomic low-risk ASK auto-approval."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from machina_autonomic._autoapprove import (  # noqa: E402
    is_autonomic_auto_approved_aid,
    sq_auto_approved_tool,
)


class AutonomicAutoApproveTests(unittest.TestCase):
    def test_default_safe_allow(self):
        with patch.dict(os.environ, {"MACHINA_AUTONOMIC_AUTO_APPROVE": "1"}, clear=False):
            self.assertTrue(is_autonomic_auto_approved_aid("AID.NET.HTTP_GET.v1"))
            self.assertTrue(is_autonomic_auto_approved_aid("AID.ERROR_SCAN.v1"))
            self.assertFalse(is_autonomic_auto_approved_aid("AID.SHELL.EXEC.v1"))

    def test_env_extension_allow(self):
        with patch.dict(
            os.environ,
            {
                "MACHINA_AUTONOMIC_AUTO_APPROVE": "1",
                "MACHINA_AUTONOMIC_AUTO_APPROVE_AIDS": "AID.MCP.WEB_SEARCH.SEARCH.v1",
            },
            clear=False,
        ):
            self.assertTrue(is_autonomic_auto_approved_aid("AID.MCP.WEB_SEARCH.SEARCH.v1"))

    def test_sq_block_override(self):
        with patch.dict(os.environ, {"MACHINA_AUTONOMIC_AUTO_APPROVE": "1"}, clear=False):
            self.assertTrue(sq_auto_approved_tool("http_get"))
            self.assertFalse(sq_auto_approved_tool("shell_exec"))


if __name__ == "__main__":
    unittest.main()
