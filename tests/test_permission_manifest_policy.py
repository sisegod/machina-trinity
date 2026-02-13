#!/usr/bin/env python3
"""Tests for manifest-driven permission fallback policy."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import machina_permissions as mp  # noqa: E402


class PermissionManifestPolicyTests(unittest.TestCase):
    def test_permission_from_side_effects_safe_read(self):
        self.assertEqual(mp._permission_from_side_effects({"filesystem_read"}), mp.ALLOW)
        self.assertEqual(mp._permission_from_side_effects({"none"}), mp.ALLOW)

    def test_permission_from_side_effects_risky(self):
        self.assertEqual(mp._permission_from_side_effects({"network_io"}), mp.ASK)
        self.assertEqual(mp._permission_from_side_effects({"filesystem_write"}), mp.ASK)
        self.assertEqual(mp._permission_from_side_effects({"proc_exec"}), mp.ASK)

    def test_check_permission_uses_manifest_fallback_for_unknown_aid(self):
        with patch("machina_permissions.get_mode", return_value="standard"), \
             patch("machina_permissions._load_overrides", return_value={}), \
             patch("machina_permissions._load_manifest_permission_map", return_value={"AID.TEST.READONLY.v1": mp.ALLOW}):
            self.assertEqual(mp.check_permission("AID.TEST.READONLY.v1"), mp.ALLOW)


if __name__ == "__main__":
    unittest.main()

