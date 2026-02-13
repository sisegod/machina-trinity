#!/usr/bin/env python3
"""Regression tests for guardrail helper scripts."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.security_guardrails import collect_security_issues
from scripts.validate_docs_refs import collect_unknown_doc_aids
from scripts.work_memory_maintenance import run as run_memory_maintenance


class GuardrailScriptTests(unittest.TestCase):
    def test_collect_security_issues_detects_plaintext_secret(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "mcp_servers.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "servers": {
                            "bad": {
                                "headers": {"Authorization": "Bearer plaintext"},
                                "env": {"Z_AI_API_KEY": "plaintext"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            issues = collect_security_issues(cfg_path)
            self.assertTrue(issues)

    def test_collect_unknown_doc_aids_flags_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            doc = Path(td) / "x.md"
            doc.write_text("Use `AID.UNKNOWN.TEST.v1` here", encoding="utf-8")
            unknown = collect_unknown_doc_aids([doc], known={"AID.FILE.READ.v1"}, allowed_unknown=set())
            self.assertEqual(len(unknown), 1)
            self.assertEqual(unknown[0][1], "AID.UNKNOWN.TEST.v1")

    def test_work_memory_maintenance_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            mem_dir = Path(td) / "work" / "memory"
            mem_dir.mkdir(parents=True, exist_ok=True)
            f = mem_dir / "experiences.jsonl"
            f.write_text("a\nb\nc\n", encoding="utf-8")
            rc = run_memory_maintenance(
                apply_changes=False,
                keep_lines=1,
                min_size_mb=0,
                streams=["experiences"],
                mem_dir=mem_dir,
            )
            self.assertEqual(rc, 0)
            self.assertTrue(f.exists())
            self.assertEqual(f.read_text(encoding="utf-8"), "a\nb\nc\n")


if __name__ == "__main__":
    unittest.main()
