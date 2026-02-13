#!/usr/bin/env python3
"""Machina GVU Building Blocks — SelfQuestioner, SelfTester, SelfHealer.

CurriculumTracker and RegressionGate live in machina_gvu_tracker.py
and are re-exported here for backward compatibility.
"""

import hashlib
import json
import logging
import os
import subprocess
import time

from machina_shared import (
    _jsonl_read, _call_engine_llm,
    _load_manifest_tools,
    MACHINA_ROOT, MEM_DIR, UTILS_DIR,
    EXPERIENCE_STREAM, INSIGHTS_STREAM, SKILLS_STREAM,
)

# Re-export so existing `from machina_gvu import CurriculumTracker, RegressionGate` still works
from machina_gvu_tracker import CurriculumTracker, RegressionGate  # noqa: F401

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GENESIS_MAX_PER_HOUR = 2

logger = logging.getLogger("autonomic")


# ---------------------------------------------------------------------------
# SelfQuestioner — generates test scenarios & improvement tasks
# ---------------------------------------------------------------------------
class SelfQuestioner:
    """Generator: creates test scenarios from manifest + failure patterns + curriculum.

    Three question sources:
    1. Tool coverage: ensure every tool in manifest can be triggered
    2. Failure replay: past failures -> generate similar scenarios to re-verify
    3. Curriculum escalation: easy -> medium -> hard capability testing
    """

    # Static test bank organized by difficulty (deterministic -- no LLM needed)
    STATIC_TESTS = {
        "easy": [
            {"input": "\uc548\ub155\ud558\uc138\uc694", "expected_type": "reply", "desc": "Korean greeting"},
            {"input": "Hello", "expected_type": "reply", "desc": "English greeting"},
            {"input": "\uace0\ub9c8\uc6cc", "expected_type": "reply", "desc": "Thanks"},
            {"input": "\uc624\ub298 \uae30\ubd84 \uc5b4\ub54c?", "expected_type": "reply", "desc": "Casual chat"},
        ],
        "medium": [
            {"input": "GPU \uc0c1\ud0dc \ubcf4\uc5ec\uc918", "expected_type": "action", "desc": "Shell tool trigger"},
            {"input": "\ube44\ud2b8\ucf54\uc778 \uac00\uaca9 \uac80\uc0c9\ud574\uc918", "expected_type": "action", "desc": "Search trigger"},
            {"input": "\ud30c\uc77c \ubaa9\ub85d \ubcf4\uc5ec\uc918", "expected_type": "action", "desc": "File tool trigger"},
            {"input": "\ub514\uc2a4\ud06c \uc6a9\ub7c9 \ud655\uc778", "expected_type": "action", "desc": "Disk check"},
            {"input": "\uba54\ubaa8\ub9ac \uc0ac\uc6a9\ub7c9 \uc54c\ub824\uc918", "expected_type": "action", "desc": "Memory check"},
        ],
        "hard": [
            {"input": "\ud53c\ubcf4\ub098\uce58 \uc218\uc5f4 \ucc98\uc74c 10\uac1c \uad6c\ud574\uc918", "expected_type": "action", "desc": "Code generation"},
            {"input": "\ub0b4 \uc0dd\uc77c 3\uc6d4 15\uc77c\uc774\uc57c, \uae30\uc5b5\ud574\uc918", "expected_type": "action", "desc": "Memory save"},
            {"input": "example.com \ub0b4\uc6a9 \uc77d\uc5b4\uc918", "expected_type": "action", "desc": "URL fetch"},
            {"input": "\uc815\ub82c \uc54c\uace0\ub9ac\uc998 \ucf54\ub4dc \uc9dc\uc11c \uc2e4\ud589\ud574\ubd10", "expected_type": "action", "desc": "Code+exec"},
        ],
    }

    def __init__(self):
        self.last_difficulty = "easy"
        self._novelty_stats = {"high": 0, "low": 0, "skipped": 0}

    # --- Novelty Scoring ---
    def reset_novelty_stats(self):
        """Reset novelty stats at burst start."""
        self._novelty_stats = {"high": 0, "low": 0, "skipped": 0}

    def get_novelty_stats(self) -> dict:
        """Return current novelty stats."""
        return dict(self._novelty_stats)

    @staticmethod
    def _tokenize(text: str) -> set:
        """Simple whitespace + punctuation tokenizer for Jaccard similarity."""
        import re as _re
        return set(_re.findall(r'[a-zA-Z0-9\uac00-\ud7a3]+', text.lower()))

    def _compute_novelty(self, question: str) -> float:
        """Compute novelty score for a proposed question/action.

        Compares against recent experiences and insights using Jaccard similarity.
        Returns: 0.0 (identical to existing) to 1.0 (completely new).
        """
        q_tokens = self._tokenize(question)
        if not q_tokens:
            return 1.0  # empty question is "novel" (will fail elsewhere)

        # Load recent experiences and insights
        exp_path = MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl"
        ins_path = MEM_DIR / f"{INSIGHTS_STREAM}.jsonl"
        experiences = _jsonl_read(exp_path, max_lines=20)
        insights = _jsonl_read(ins_path, max_lines=10)

        max_sim = 0.0

        # Compare against experience descriptions
        for exp in experiences:
            text = exp.get("user_request", "") + " " + exp.get("result_preview", "")
            e_tokens = self._tokenize(text)
            if not e_tokens:
                continue
            intersection = len(q_tokens & e_tokens)
            union = len(q_tokens | e_tokens)
            sim = intersection / union if union > 0 else 0.0
            if sim > max_sim:
                max_sim = sim

        # Compare against insight descriptions
        for ins in insights:
            parts = []
            for field in ("topic", "reflection", "rules"):
                val = ins.get(field, "")
                if isinstance(val, list):
                    parts.append(" ".join(str(v) for v in val))
                elif isinstance(val, str):
                    parts.append(val)
            text = " ".join(parts)
            i_tokens = self._tokenize(text)
            if not i_tokens:
                continue
            intersection = len(q_tokens & i_tokens)
            union = len(q_tokens | i_tokens)
            sim = intersection / union if union > 0 else 0.0
            if sim > max_sim:
                max_sim = sim

        return round(1.0 - max_sim, 4)

    def generate_scenarios(self, curriculum: dict, insights: list) -> list:
        """Generate test scenarios based on current capability + past failures."""
        scenarios = []

        # 1. Static tests at appropriate difficulty
        difficulty = self._select_difficulty(curriculum)
        static = self.STATIC_TESTS.get(difficulty, self.STATIC_TESTS["easy"])
        for test in static:
            scenarios.append({
                "source": "static",
                "difficulty": difficulty,
                **test,
            })

        # 2. Failure replay -- regenerate scenarios from past failure patterns
        fail_patterns = [i for i in insights if i.get("type") == "failure"]
        for fp in fail_patterns[-3:]:  # last 3 failure insights
            user_req = fp.get("user_request", "")
            if user_req and not user_req.startswith("[self-test]"):
                scenarios.append({
                    "source": "failure_replay",
                    "difficulty": "medium",
                    "input": user_req[:200],
                    "expected_type": "action",  # failures are typically action misclassification
                    "desc": f"Replay of past failure: {fp.get('fail_type', 'unknown')}",
                })

        # 3. Tool coverage -- check if any tool hasn't been tested recently
        tested_tools = set()
        recent_exp = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=50)
        for exp in recent_exp:
            tool = exp.get("tool_used", "")
            if tool:
                tested_tools.add(tool)

        tool_prompts = {
            "shell": "\uc11c\ubc84 \ud504\ub85c\uc138\uc2a4 \ubaa9\ub85d \ubcf4\uc5ec\uc918",
            "web": "\ud30c\uc774\uc36c 3.12 \uc0c8\ub85c\uc6b4 \uae30\ub2a5 \uac80\uc0c9\ud574\uc918",
            "file": "machina_env.sh \ud30c\uc77c \uc77d\uc5b4\uc918",
            "memory": "\ub0b4\uac00 \uc5b4\uc81c \ubb50 \uc800\uc7a5\ud588\ub294\uc9c0 \ucc3e\uc544\ubd10",
            "code": "1\ubd80\ud130 100\uae4c\uc9c0 \ud569 \uacc4\uc0b0\ud558\ub294 \ucf54\ub4dc \uc9dc\uc918",
        }
        for tool_name, prompt in tool_prompts.items():
            if tool_name not in tested_tools:
                scenarios.append({
                    "source": "tool_coverage",
                    "difficulty": "medium",
                    "input": prompt,
                    "expected_type": "action",
                    "desc": f"Coverage: {tool_name} tool untested",
                })

        self.last_difficulty = difficulty
        return scenarios[:10]  # cap at 10 per cycle

    def _select_difficulty(self, curriculum: dict) -> str:
        """WebRL-style difficulty selection based on success rates."""
        easy_rate = curriculum.get("easy_success_rate", 1.0)
        medium_rate = curriculum.get("medium_success_rate", 0.0)

        if easy_rate < 0.8:
            return "easy"  # still struggling with basics
        elif medium_rate < 0.7:
            return "medium"
        else:
            return "hard"

    def generate_self_questions(self, curriculum: dict) -> list:
        """AgentEvolver-style: LLM generates its own improvement questions.

        Only triggered when static tests pass well (medium_success_rate > 0.7).
        Uses Ollama to generate novel test scenarios.
        """
        if curriculum.get("medium_success_rate", 0.0) < 0.7:
            return []  # not ready for self-questioning yet

        tools_desc = ", ".join(_load_manifest_tools()[:10])
        prompt = (
            "\ub2f9\uc2e0\uc740 AI \uc5d0\uc774\uc804\ud2b8 \ud14c\uc2a4\ud130\uc785\ub2c8\ub2e4. \ub2e4\uc74c \ub3c4\uad6c\ub4e4\uc744 \uac00\uc9c4 \uc5d0\uc774\uc804\ud2b8\uc758 \ub2a5\ub825\uc744 \uac80\uc99d\ud558\ub294 "
            "\ud14c\uc2a4\ud2b8 \uc2dc\ub098\ub9ac\uc624 3\uac1c\ub97c JSON \ubc30\uc5f4\ub85c \uc0dd\uc131\ud558\uc138\uc694.\n"
            f"\ub3c4\uad6c: {tools_desc}\n"
            '\ud615\uc2dd: [{{"input":"\uc0ac\uc6a9\uc790 \uba54\uc2dc\uc9c0","expected_type":"action \ub610\ub294 reply","desc":"\uc124\uba85"}}]\n'
            "JSON\ub9cc \ucd9c\ub825."
        )
        raw = _call_engine_llm(prompt, max_tokens=300, temperature=0.8, format_json=True)
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                return [{"source": "self_question", "difficulty": "hard", **item}
                        for item in items[:3] if "input" in item]
        except (json.JSONDecodeError, TypeError):
            pass
        return []


# ---------------------------------------------------------------------------
# SelfTester — binary verification (NO LLM self-judgment)
# ---------------------------------------------------------------------------
class SelfTester:
    """Verifier: executes scenarios through chat_driver, checks binary pass/fail.

    Verification methods (ranked by reliability):
    1. Exit code (subprocess return code)
    2. Type match (expected_type vs actual_type)
    3. Non-empty output check
    Never uses LLM to judge its own output (reward hacking prevention).
    """

    def __init__(self):
        self.chat_driver_path = os.path.join(MACHINA_ROOT, "policies", "chat_driver.py")

    def run_scenario(self, scenario: dict) -> dict:
        """Execute a single test scenario, return binary result."""
        user_text = scenario.get("input", "")
        expected_type = scenario.get("expected_type", "")

        result = {
            "scenario": scenario,
            "passed": False,
            "actual_type": "",
            "error": "",
            "duration_ms": 0,
        }

        t0 = time.time()
        try:
            proc = subprocess.run(
                ["python3", self.chat_driver_path],
                input=json.dumps({
                    "mode": "intent",
                    "conversation": [{"role": "user", "content": user_text}],
                    "session": {},
                }, ensure_ascii=False),
                capture_output=True, text=True, timeout=60,
                cwd=MACHINA_ROOT,
                env={**os.environ, "MACHINA_ROOT": MACHINA_ROOT},
            )
            result["duration_ms"] = int((time.time() - t0) * 1000)

            if proc.returncode != 0:
                result["error"] = f"exit_code={proc.returncode}"
                return result

            if proc.stdout:
                resp = json.loads(proc.stdout)
                actual_type = resp.get("type", "")
                result["actual_type"] = actual_type
                result["passed"] = (actual_type == expected_type)
            else:
                result["error"] = "empty stdout"

        except subprocess.TimeoutExpired:
            result["error"] = "timeout"
            result["duration_ms"] = int((time.time() - t0) * 1000)
        except json.JSONDecodeError as e:
            result["error"] = f"json_parse: {e}"
        except Exception as e:
            result["error"] = str(e)[:200]

        return result

    def run_batch(self, scenarios: list, abort_check=None) -> dict:
        """Run all scenarios, return aggregate results."""
        results = {"passed": 0, "failed": 0, "errors": 0, "details": []}

        for scenario in scenarios:
            # Allow abort if user becomes active
            if abort_check and abort_check():
                logger.info("[SelfTest] Aborted: external activity detected")
                break

            r = self.run_scenario(scenario)
            results["details"].append(r)

            if r["error"]:
                results["errors"] += 1
            elif r["passed"]:
                results["passed"] += 1
            else:
                results["failed"] += 1

        return results


# ---------------------------------------------------------------------------
# SelfHealer — generates fixes, tests, deploys
# ---------------------------------------------------------------------------
class SelfHealer:
    """Updater: takes failure patterns -> generates code fixes -> tests -> deploys.

    Pipeline: failure_analysis -> codegen -> sandbox_test -> deploy_or_discard
    Safety: rate-limited, sandbox-only, no self-modifying core files.
    """

    def __init__(self):
        self.genesis_recent = []  # timestamps

    def _rate_ok(self) -> bool:
        now = time.time()
        self.genesis_recent = [t for t in self.genesis_recent if now - t < 3600]
        return len(self.genesis_recent) < GENESIS_MAX_PER_HOUR

    def analyze_failures(self, test_results: dict) -> list:
        """Classify failures into actionable categories."""
        actions = []
        for detail in test_results.get("details", []):
            if detail["passed"] or detail["error"]:
                continue
            scenario = detail["scenario"]
            expected = scenario.get("expected_type", "")
            actual = detail.get("actual_type", "")

            # Deterministic failure classification (no LLM)
            if actual == "":
                actions.append({
                    "type": "empty_output",
                    "desc": f"'{scenario.get('input', '')[:60]}' -> expected {expected}, got empty output",
                    "category": "pipeline",
                    "fix_hint": "chat_driver returned no type — possible LLM timeout or parse failure",
                })
            elif expected == "action" and actual == "reply":
                actions.append({
                    "type": "intent_misclass",
                    "desc": f"'{scenario.get('input', '')[:60]}' -> expected action, got reply",
                    "category": "intent",
                    "fix_hint": "Intent prompt may need stronger action-trigger keywords",
                })
            elif expected == "reply" and actual == "action":
                actions.append({
                    "type": "intent_misclass",
                    "desc": f"'{scenario.get('input', '')[:60]}' -> expected reply, got action",
                    "category": "intent",
                    "fix_hint": "Intent prompt may be too aggressive on action classification",
                })
        return actions

    def attempt_heal(self, failure_actions: list) -> dict:
        """Generate and test a fix for the most common failure pattern."""
        if not failure_actions or not self._rate_ok():
            return {"attempted": False, "reason": "no actions or rate limited"}

        # Group by category, fix the most frequent
        categories = {}
        for a in failure_actions:
            cat = a.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1

        top_category = max(categories, key=categories.get)
        top_actions = [a for a in failure_actions if a.get("category") == top_category]

        # Generate a utility that could help
        examples = "\n".join(f"- {a['desc']}" for a in top_actions[:3])
        codegen_prompt = (
            "Write a short Python diagnostic script (under 40 lines) that analyzes "
            f"this AI agent issue:\n{examples}\n\n"
            "The script should:\n"
            "1. Read the intent classification prompt from policies/chat_driver.py\n"
            "2. Identify patterns that might cause misclassification\n"
            "3. Print specific suggestions for improvement\n\n"
            "Rules: pure Python stdlib only, no input(), no f-strings, "
            "use print() for output. Only output Python code."
        )

        generated = _call_engine_llm(
            codegen_prompt,
            system="You are a Python code generator. Output ONLY valid Python code.",
            max_tokens=600, temperature=0.3,
        ).strip()

        # Strip markdown fences
        if generated.startswith("```"):
            lines = generated.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            generated = "\n".join(lines).strip()

        if not generated or len(generated) < 30:
            return {"attempted": True, "success": False, "reason": "codegen empty"}

        # Save and test
        util_name = f"heal_{top_category}_{int(time.time()) % 10000}"
        script_path = os.path.join(UTILS_DIR, f"{util_name}.py")
        os.makedirs(UTILS_DIR, exist_ok=True)

        # BUG FIX #2: Strengthened dangerous pattern check
        dangerous = [
            "os.remove", "os.unlink", "os.rmdir", "os.system(",
            "shutil.rmtree", "shutil.move",
            "subprocess.call", "subprocess.Popen", "subprocess.run",
            "eval(", "exec(", "compile(", "__import__(",
            "__builtins__", "globals(", "locals(",
            "import socket", "import http.server", "import http.client",
            'open(', "import ctypes", "import signal",
            "io.open(", "pathlib.Path.read_text(", "pathlib.Path.write_text(",
            "pathlib.Path.open(", "pathlib.Path.write_bytes(",
            # Additional dangerous APIs (security hardening)
            "os.popen", "importlib.", "pickle.", "socket.",
        ]
        # Allow open() only in explicit read mode -- block write/append modes
        # and indirect variable references like open(x, y) that bypass string checks
        if 'open(' in generated:
            import re as _re
            # Block explicit write modes: "w", "wb", "a", "ab", "x", "xb", "r+", "rb+", "w+", etc.
            open_write_calls = _re.findall(
                r'open\s*\([^)]*["\']([wxa][+b]*|r[b]?\+)["\']', generated
            )
            if open_write_calls:
                return {"attempted": True, "success": False, "reason": "file write in generated code blocked"}
            # Block indirect variable references: open(path, mode) where mode is a variable
            # (bypasses string-literal checks above)
            open_var_mode = _re.findall(
                r'open\s*\([^)]*,\s*([a-zA-Z_]\w*)\s*\)', generated
            )
            if open_var_mode:
                return {"attempted": True, "success": False, "reason": "open() with variable mode blocked (indirect bypass risk)"}
            # Remove open( from dangerous check since read-only with literal "r" is OK
            dangerous = [d for d in dangerous if d != 'open(']

        if any(d in generated for d in dangerous):
            return {"attempted": True, "success": False, "reason": "dangerous code blocked"}

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(generated)

        # Sandbox test
        try:
            proc = subprocess.run(
                ["python3", script_path],
                capture_output=True, text=True, timeout=30,
                cwd=MACHINA_ROOT,
                env={**os.environ, "MACHINA_ROOT": MACHINA_ROOT},
            )
            success = proc.returncode == 0 and "traceback" not in proc.stderr.lower()
            output = proc.stdout[:500]
        except subprocess.TimeoutExpired:
            success = False
            output = "timeout"
        except Exception as e:
            success = False
            output = str(e)[:200]

        self.genesis_recent.append(time.time())

        # Record to skills if successful (use canonical skill_record function)
        if success:
            from machina_learning import skill_record
            skill_record(
                user_request=f"heal_{top_category}",
                lang="python",
                code=generated,
                result=output[:300],
            )
            logger.info(f"[SelfHeal] SUCCESS: {util_name} -> skill recorded")

        return {
            "attempted": True,
            "success": success,
            "util_name": util_name,
            "script_path": script_path,
            "code_hash": hashlib.sha256(generated.encode()).hexdigest(),
            "category": top_category,
            "output": output[:300],
            "failures_addressed": len(top_actions),
        }
