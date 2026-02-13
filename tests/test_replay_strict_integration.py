import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


class ReplayStrictIntegrationTest(unittest.TestCase):
    @staticmethod
    def _make_request() -> dict:
        return {
            "goal_id": "goal.GENESIS.REPLAY_TEST.v1",
            "candidate_tags": ["tag.genesis"],
            "control_mode": "FALLBACK_ONLY",
            "inputs": {
                "relative_path": "replay_test_tool.cpp",
                "content": (
                    "#include \"machina/plugin_api.h\"\n"
                    "extern \"C\" bool machina_plugin_init(machina::Registrar*) { return true; }\n"
                ),
            },
        }

    @staticmethod
    def _run_and_get_log(root: Path, cli: Path, req_path: Path, env: dict) -> Path:
        run = subprocess.run(
            [str(cli), "run", str(req_path)],
            cwd=str(root),
            env=env,
            text=True,
            capture_output=True,
            timeout=45,
        )
        run_out = (run.stdout or "") + (run.stderr or "")
        m = re.search(r"^log:\s*(\S+)\s*$", run_out, flags=re.MULTILINE)
        if m is None:
            raise AssertionError(f"log path not found in run output:\n{run_out}")
        log_path = Path(m.group(1))
        if not log_path.exists():
            raise AssertionError(f"run log missing: {log_path}")
        return log_path

    def test_run_then_replay_strict_heuristic(self) -> None:
        root = Path(__file__).resolve().parent.parent
        cli = root / "build" / "machina_cli"
        if not cli.exists():
            self.skipTest("machina_cli binary not found; build C++ targets first")

        req = self._make_request()

        env = os.environ.copy()
        env["MACHINA_SELECTOR"] = "HEURISTIC"
        env.setdefault("MACHINA_GENESIS_ENABLE", "1")
        env.setdefault("MACHINA_GENESIS_COMPILE_RETRIES", "0")

        with tempfile.TemporaryDirectory(prefix="machina_replay_") as td:
            req_path = Path(td) / "req.json"
            req_path.write_text(json.dumps(req), encoding="utf-8")

            log_path = self._run_and_get_log(root, cli, req_path, env)

            rep = subprocess.run(
                [str(cli), "replay_strict", str(req_path), str(log_path)],
                cwd=str(root),
                env=env,
                text=True,
                capture_output=True,
                timeout=45,
            )
            rep_out = (rep.stdout or "") + (rep.stderr or "")
            self.assertEqual(rep.returncode, 0, rep_out)
            self.assertIn("REPLAY_STRICT OK", rep_out)

    def test_replay_strict_fails_on_invalid_tx_patch(self) -> None:
        root = Path(__file__).resolve().parent.parent
        cli = root / "build" / "machina_cli"
        if not cli.exists():
            self.skipTest("machina_cli binary not found; build C++ targets first")

        req = self._make_request()

        env = os.environ.copy()
        env["MACHINA_SELECTOR"] = "HEURISTIC"
        env.setdefault("MACHINA_GENESIS_ENABLE", "1")
        env.setdefault("MACHINA_GENESIS_COMPILE_RETRIES", "0")

        with tempfile.TemporaryDirectory(prefix="machina_replay_badpatch_") as td:
            req_path = Path(td) / "req.json"
            req_path.write_text(json.dumps(req), encoding="utf-8")
            log_path = self._run_and_get_log(root, cli, req_path, env)

            broken = Path(td) / "broken.jsonl"
            changed = False
            with log_path.open("r", encoding="utf-8") as src, broken.open("w", encoding="utf-8") as dst:
                for line in src:
                    rec = json.loads(line)
                    if (
                        not changed
                        and rec.get("event") == "tool_ok"
                        and isinstance(rec.get("payload"), dict)
                        and rec["payload"].get("deterministic") is False
                    ):
                        rec["payload"]["tx_patch"] = [{"op": "move", "path": "/slots/1"}]
                        changed = True
                    dst.write(json.dumps(rec, ensure_ascii=True) + "\n")
            self.assertTrue(changed, "expected at least one non-deterministic tool_ok event in run log")

            rep = subprocess.run(
                [str(cli), "replay_strict", str(req_path), str(broken)],
                cwd=str(root),
                env=env,
                text=True,
                capture_output=True,
                timeout=45,
            )
            rep_out = (rep.stdout or "") + (rep.stderr or "")
            self.assertNotEqual(rep.returncode, 0, rep_out)
            self.assertIn("cannot apply logged tx_patch", rep_out)


if __name__ == "__main__":
    unittest.main()
