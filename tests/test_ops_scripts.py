#!/usr/bin/env python3
"""Smoke tests for ops orchestration shell scripts."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class OpsScriptsTests(unittest.TestCase):
    def test_ops_detect_writes_json(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "pids.json"
            subprocess.run(
                [str(ROOT / "scripts" / "ops_detect.sh"), "--json-out", str(out), "--dry-run"],
                check=True,
                text=True,
                capture_output=True,
            )
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("processes", data)
            self.assertIn("count", data)

    def test_ops_kill_dry_run_from_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td) / "in.json"
            out = ROOT / "ops" / "pids.killed.json"
            fixture.write_text(
                json.dumps(
                    {
                        "processes": [
                            {"pid": 999999, "role": "machina_serve", "protected": False},
                            {"pid": 999998, "role": "ollama", "protected": True},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [str(ROOT / "scripts" / "ops_kill.sh"), "--from", str(fixture), "--dry-run"],
                check=True,
                text=True,
                capture_output=True,
            )
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["targets"], [999999])

    def test_ops_restart_dry_run(self):
        subprocess.run(
            [str(ROOT / "scripts" / "ops_restart.sh"), "--dry-run"],
            check=True,
            text=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
