"""Machina Autonomic Engine — CuriosityDriver class.

RandomStimulus has been extracted to machina_autonomic._random_stimulus.
It is re-exported here for backward compatibility.
"""

import hashlib
import json
import logging
import os
import random
import re
import time

from machina_shared import (
    _jsonl_append, _jsonl_read,
    _call_engine_llm, _load_manifest_tools, _load_manifest_tools_full,
    sandboxed_run,
    MACHINA_ROOT, MEM_DIR,
    EXPERIENCE_STREAM, INSIGHTS_STREAM, SKILLS_STREAM,
)
from machina_gvu import RegressionGate
from machina_autonomic._constants import (
    KNOWLEDGE_STREAM,
    STORE_SUMMARY_LEN, STORE_RESULT_LEN,
    _audit_log, _send_alert, logger,
)
from machina_autonomic._random_stimulus import RandomStimulus  # noqa: F401

# ---------------------------------------------------------------------------
# CuriosityDriver — self-stimulus engine (Voyager-inspired)
# ---------------------------------------------------------------------------
class CuriosityDriver:
    """Autonomous goal generation: capability gap analysis -> Genesis trigger.

    Pipeline: gap_scan -> score -> goal_synthesize -> genesis_execute -> gate
    Safety: max_per_day limit, RegressionGate mandatory, rate limited.
    """

    GAP_FILE = MEM_DIR / "curiosity_gaps.jsonl"
    MAX_PER_DAY = 3
    COOLDOWN_SEC = 7200  # 2hr between curiosity cycles

    def __init__(self, regression_gate: RegressionGate = None,
                 max_per_day: int = None, cooldown_sec: int = None,
                 stream_fn=None):
        self.gate = regression_gate
        self.last_run = 0
        self.daily_count = 0
        self.daily_reset_ts = 0
        self._max_per_day = max_per_day if max_per_day is not None else self.MAX_PER_DAY
        self._cooldown_sec = cooldown_sec if cooldown_sec is not None else self.COOLDOWN_SEC
        self._stream = stream_fn or (lambda msg: None)

    def _reset_daily_if_needed(self):
        now = time.time()
        if now - self.daily_reset_ts > 86400:
            self.daily_count = 0
            self.daily_reset_ts = now

    def can_run(self) -> bool:
        self._reset_daily_if_needed()
        if self.daily_count >= self._max_per_day:
            return False
        if time.time() - self.last_run < self._cooldown_sec:
            return False
        return True

    def _relevance_gate(self, goal: dict) -> tuple:
        """Deterministic relevance check. Returns (pass, reason)."""
        code = goal.get("code", "")
        name = goal.get("name", "")

        # 1. Feasibility: code length bounds
        if len(code) < 30:
            return (False, "code too short")
        if len(code) > 10000:
            return (False, "code too long")

        # 2. Novelty: check against existing skills
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        skills_file = MEM_DIR / f"{SKILLS_STREAM}.jsonl"
        if skills_file.exists():
            for s in _jsonl_read(skills_file):
                if s.get("code_hash") == code_hash:
                    return (False, "duplicate skill")

        # 3. Alignment: name must relate to known tools/project domains
        known = {"error", "scan", "shell", "file", "http", "memory", "queue",
                 "report", "gpu", "embed", "vector", "genesis", "search",
                 "code", "web", "util", "metric", "proc", "log", "format",
                 "parse", "validate", "convert", "analyze", "monitor",
                 "fix", "tool", "test", "replay", "check", "run", "build",
                 "stat", "list", "read", "write", "hash", "sort", "filter",
                 "count", "detect", "transform", "extract", "generate",
                 "retry", "handler", "config", "data", "clean", "update",
                 "diff", "merge", "debug", "batch", "cache", "schedule",
                 "process", "render", "compile", "link", "load", "save",
                 "fetch", "send", "recv", "input", "output", "path",
                 "string", "number", "json", "csv", "xml", "yaml",
                 # Project-specific: agent, learning, autonomic domains
                 "agent", "llm", "telegram", "bot", "dispatch", "intent",
                 "skill", "experience", "insight", "knowledge", "audit",
                 "curiosity", "stimulus", "burst", "heal", "reflect",
                 "sandbox", "permission", "mcp", "plugin", "wal"}
        name_lower = name.lower().replace("_", " ")
        if not any(k in name_lower for k in known):
            return (False, f"unaligned: '{name}' not related to known tools")

        return (True, "passed")

    def scan_gaps(self) -> list:
        """Analyze registered tools vs failed requests -> capability gaps."""
        # 1. Get registered tools (canonical AID names)
        manifest_tools = set()
        for aid in _load_manifest_tools():
            manifest_tools.add(aid)
        # Add Python-side tools (proper AID identifiers)
        py_tools = {
            "AID.SHELL.EXEC.v1", "AID.NET.WEB_SEARCH.v1",
            "AID.CODE.EXEC.v1", "AID.NET.HTTP_GET.v1",
            "AID.MEMORY.APPEND.v1", "AID.MEMORY.QUERY.v1",
            "AID.FILE.READ.v1", "AID.FILE.WRITE.v1",
            "AID.GENESIS.WRITE_FILE.v1", "AID.GENESIS.COMPILE_SHARED.v1",
            "AID.GENESIS.LOAD_PLUGIN.v1",
            "AID.UTIL.SAVE.v1", "AID.UTIL.RUN.v1", "AID.UTIL.LIST.v1",
            "AID.UTIL.DELETE.v1", "AID.UTIL.UPDATE.v1",
            "AID.FILE.LIST.v1", "AID.FILE.SEARCH.v1", "AID.FILE.DIFF.v1",
            "AID.FILE.EDIT.v1", "AID.FILE.APPEND.v1", "AID.FILE.DELETE.v1",
            "AID.PROJECT.CREATE.v1", "AID.PROJECT.BUILD.v1",
            "AID.SYSTEM.PIP_INSTALL.v1",
            "AID.SYSTEM.PIP_UNINSTALL.v1",
            "AID.SYSTEM.PIP_LIST.v1",
        }
        all_tools = manifest_tools | py_tools

        # 2. Analyze failed requests
        # Exclude internal SQ action categories — these are NOT real tools
        _SQ_CATEGORIES = {"audit", "test_tool", "search", "reflect", "code", "reply", "chat", "action"}
        exp_file = MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl"
        if not exp_file.exists():
            return []
        exps = _jsonl_read(exp_file, max_lines=200)
        failed_requests = []
        tool_fail_counts = {}
        for e in exps:
            if not e.get("success"):
                req = e.get("user_request", "")
                tool = e.get("tool_used", "")
                if req:
                    failed_requests.append(req)
                # Only count real tools (AID.* or known names), not SQ categories
                if tool and tool not in _SQ_CATEGORIES:
                    tool_fail_counts[tool] = tool_fail_counts.get(tool, 0) + 1

        # 3. Detect gaps: tools with high failure rate
        gaps = []
        for tool, count in tool_fail_counts.items():
            if count >= 3:
                # Count total uses
                total = sum(1 for e in exps if (e.get("tool_used", "") == tool))
                fail_rate = count / max(total, 1)
                if fail_rate > 0.4:
                    gaps.append({
                        "type": "high_failure_tool",
                        "tool": tool,
                        "fail_count": count,
                        "fail_rate": round(fail_rate, 2),
                        "sample_requests": [r[:80] for r in failed_requests
                                            if tool in str(r).lower()][:3],
                    })

        # 4. Detect capability gaps: requests that produced wrong_tool/empty
        unhandled = [e for e in exps if not e.get("success")
                     and not e.get("tool_used")]
        if len(unhandled) >= 3:
            sample = [e.get("user_request", "")[:80] for e in unhandled[-5:]]
            gaps.append({
                "type": "unhandled_capability",
                "count": len(unhandled),
                "sample_requests": sample,
            })

        # 5. Proactive: detect untested/rarely-tested tools
        tested_tools = {e.get("tool_used", "") for e in exps if e.get("tool_used")}
        untested = sorted(all_tools - tested_tools - _SQ_CATEGORIES - {""})
        if untested:
            gaps.append({
                "type": "untested_tool",
                "tools": untested[:5],
                "count": len(untested),
            })

        return gaps

    @staticmethod
    def _safe_token(text: str, limit: int = 24) -> str:
        """Convert free text into a safe snake_case token."""
        token = re.sub(r"[^a-z0-9_]+", "_", (text or "").lower())
        token = re.sub(r"_+", "_", token).strip("_")
        return (token or "gap")[:limit]

    def _fallback_goal(self, gap: dict, reason: str = "") -> dict:
        """Deterministic fallback when LLM goal synthesis fails.

        Produces a safe, executable Python utility that generates a diagnostic
        report tied to the detected capability gap.
        """
        gtype = gap.get("type", "gap")
        stamp = int(time.time())
        exp_path = str(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl")

        if gtype == "high_failure_tool":
            tool = str(gap.get("tool", "unknown_tool"))
            tool_tok = self._safe_token(tool.replace(".", "_"))
            name = f"gap_repair_{tool_tok}"
            desc = f"High-failure tool diagnostics for {tool}"
            code = f'''#!/usr/bin/env python3
import json
from collections import Counter
from pathlib import Path

TARGET_TOOL = {json.dumps(tool)}
EXP_FILE = Path({json.dumps(exp_path)})

def load_entries():
    if not EXP_FILE.exists():
        return []
    rows = []
    for line in EXP_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows

rows = load_entries()
fails = [r for r in rows if r.get("tool_used") == TARGET_TOOL and not r.get("success")]
reasons = Counter((r.get("result_preview", "") or "unknown")[:80] for r in fails)
report = {{
    "kind": "high_failure_tool_diagnostic",
    "tool": TARGET_TOOL,
    "total_failures": len(fails),
    "top_failure_signatures": reasons.most_common(5),
    "repair_hypothesis": (
        "input schema mismatch or missing precondition. "
        "recommend auto-generated schema probe + minimal valid input fixture."
    ),
}}
print(json.dumps(report, ensure_ascii=False))
'''
        elif gtype == "untested_tool":
            tools = list(gap.get("tools", []))[:12]
            name = f"gap_coverage_{stamp}"
            desc = "Untested-tool coverage planner"
            code = f'''#!/usr/bin/env python3
import json

UNTESTED = {json.dumps(tools, ensure_ascii=False)}
plan = []
for t in UNTESTED:
    plan.append({{
        "tool": t,
        "priority": "high" if "MCP" in str(t).upper() else "medium",
        "first_probe": "schema_only",
        "second_probe": "minimal_valid_input",
    }})

report = {{
    "kind": "untested_tool_coverage_plan",
    "untested_count": len(UNTESTED),
    "plan_top": plan[:10],
    "next_action": "run automated probes and store pass/fail signatures",
}}
print(json.dumps(report, ensure_ascii=False))
'''
        else:
            name = f"gap_unhandled_{stamp}"
            desc = "Unhandled capability request analyzer"
            code = f'''#!/usr/bin/env python3
import json
from collections import Counter
from pathlib import Path

EXP_FILE = Path({json.dumps(exp_path)})
STOP = {{"the","and","for","with","that","this","from","have","what","when","where","how","is","are","to","in","on","a","an"}}

def tok(text):
    text = "".join(ch.lower() if (ch.isalnum() or ch == " ") else " " for ch in str(text))
    return [w for w in text.split() if len(w) >= 3 and w not in STOP]

rows = []
if EXP_FILE.exists():
    for line in EXP_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass

unhandled = [r for r in rows if (not r.get("success")) and (not r.get("tool_used"))]
wc = Counter()
for r in unhandled:
    wc.update(tok(r.get("user_request", "")))

report = {{
    "kind": "unhandled_capability_analyzer",
    "count": len(unhandled),
    "top_request_tokens": wc.most_common(15),
    "recommendation": "convert top clusters into explicit goals with runnable tool chains",
}}
print(json.dumps(report, ensure_ascii=False))
'''

        return {
            "name": name,
            "description": (desc + (f" | fallback: {reason}" if reason else ""))[:300],
            "code": code,
            "gap": gap,
            "fallback": True,
        }

    def synthesize_goal(self, gaps: list) -> dict:
        """LLM generates a concrete improvement goal from gaps."""
        if not gaps:
            return {}

        # Pick highest priority gap
        gap = max(gaps, key=lambda g: g.get("fail_count", g.get("count", 0)))

        if gap["type"] == "high_failure_tool":
            desc = (f"Tool '{gap['tool']}' fails {gap['fail_rate']:.0%}. "
                    f"Samples: {'; '.join(gap.get('sample_requests', [])[:2])}")
        elif gap["type"] == "untested_tool":
            desc = (f"{gap['count']} tools never tested. "
                    f"Examples: {', '.join(gap.get('tools', [])[:3])}. "
                    "Create a test utility that exercises these tools with sample inputs.")
        else:
            desc = (f"{gap['count']} requests unhandled. "
                    f"Samples: {'; '.join(gap.get('sample_requests', [])[:2])}")

        prompt = (
            f"다음 능력 갭을 분석하고 개선 도구를 설계해:\n{desc}\n\n"
            "Python 유틸리티 생성 (200줄 이내). json, os.path, re, math, datetime, collections 등 stdlib 자유 사용.\n"
            'JSON 출력: {"name":"도구이름_영문","description":"설명","code":"파이썬코드"}\n'
            "JSON만 출력."
        )
        raw = _call_engine_llm(prompt, system="유효한 JSON만 출력하라. 코드 블록 금지.",
                           max_tokens=2000, temperature=0.3, format_json=True,
                           think=False)
        fail_reason = ""
        try:
            goal = json.loads(raw)
            if goal.get("name") and goal.get("code"):
                goal["gap"] = gap
                return goal
            fail_reason = "missing required fields"
        except (json.JSONDecodeError, TypeError):
            fail_reason = "invalid JSON response"
        fb = self._fallback_goal(gap, fail_reason)
        _audit_log("L5", "goal_fallback", f"{gap.get('type', '?')}: {fail_reason}", success=True)
        logger.info(f"[L5] Goal synthesis fallback used: {fail_reason}")
        return fb

    def execute_goal(self, goal: dict) -> dict:
        """Execute a synthesized goal: save util -> test -> gate."""
        name = goal.get("name", "curiosity_util")[:30]
        code = goal.get("code", "")
        if not code or len(code) < 20:
            return {"success": False, "reason": "code too short"}

        # Safety check — comprehensive blocklist for LLM-generated code
        # Normalize whitespace to prevent bypass via "os . system(" or "eval ("
        _normalized = re.sub(r'\s+', '', code)
        dangerous = [
            "os.system(", "subprocess.", "eval(", "exec(",
            "compile(", "__import__(", "__builtins__",
            "os.popen", "os.execve", "os.execvp", "os.execl",
            "shutil.rmtree", "shutil.move",
            "open(", "ctypes.", "importlib.",
            "getattr(", "setattr(", "delattr(",
            "base64.", "codecs.", "__class__", "__subclasses__",
            "globals()[", "locals()[", "vars(", "dir(", "type(",
            # Additional dangerous APIs (Fix #2)
            "socket.", "urllib.", "pickle.", "os.environ",
            "os.remove", "pathlib.Path.unlink", "shutil.rmtree",
        ]
        # Compare against whitespace-normalized code
        if any(d.replace(' ', '') in _normalized for d in dangerous):
            return {"success": False, "reason": "dangerous code blocked"}

        # Strip markdown fences
        if code.startswith("```"):
            lines = code.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            code = "\n".join(lines).strip()

        # Sanitize name: strip path separators and traversal sequences
        name = name.replace("/", "_").replace("\\", "_").replace("..", "_")

        # Save and test
        utils_dir = os.path.join(MACHINA_ROOT, "work", "scripts", "utils")
        os.makedirs(utils_dir, exist_ok=True)
        script_path = os.path.join(utils_dir, f"curiosity_{name}.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        try:
            proc = sandboxed_run(
                ["python3", script_path],
                timeout=10, cwd=MACHINA_ROOT,
                writable_dirs=[os.path.join(MACHINA_ROOT, "work")],
            )
            success = proc.returncode == 0 and "traceback" not in proc.stderr.lower()
            output = proc.stdout[:1000]
        except Exception as e:
            logger.warning(f"Curiosity goal execution failed: {e}")
            success = False
            output = "execution failed"

        if not success:
            try:
                os.remove(script_path)
            except OSError as oe:
                logger.warning(f"[L5] Cleanup failed for {script_path}: {oe}")
            return {"success": False, "reason": "test failed", "output": output}

        code_hash = hashlib.sha256(code.encode()).hexdigest()

        # Regression gate
        if self.gate:
            e2e = self.gate.run_e2e()
            if not e2e.get("error") and not self.gate.check(e2e):
                try:
                    os.remove(script_path)
                except OSError as oe:
                    logger.warning(f"[L5] Cleanup failed for {script_path}: {oe}")
                return {"success": False, "reason": "E2E regression", "rolled_back": True}
            elif not e2e.get("error"):
                self.gate.accept(e2e)

        # Record as skill
        _jsonl_append(MEM_DIR / f"{SKILLS_STREAM}.jsonl", {
            "ts_ms": int(time.time() * 1000),
            "event": "skill", "stream": SKILLS_STREAM,
            "request": f"curiosity: {goal.get('description', '')[:300]}",
            "lang": "python", "code": code,
            "result": output, "code_hash": code_hash,
            "source": "curiosity_driver",
        })

        return {"success": True, "name": name, "code_hash": code_hash,
                "script_path": script_path, "output": output,
                "description": goal.get("description", "")}

    def run_cycle(self) -> dict:
        """Full curiosity cycle: scan -> synthesize -> execute -> record."""
        if not self.can_run():
            return {"skipped": True, "reason": "rate limited"}

        self.last_run = time.time()
        self.daily_count += 1

        self._stream("능력 갭 스캔 시작... 실패한 요청 패턴 분석중")
        logger.info("[L5] CuriosityDriver: scanning capability gaps")
        gaps = self.scan_gaps()
        if not gaps:
            logger.info("[L5] No gaps detected")
            self._stream("갭 없음. 현재 도구 세트로 충분.")
            _jsonl_append(self.GAP_FILE, {
                "ts_ms": int(time.time() * 1000),
                "gaps_found": 0, "action": "none",
            })
            return {"gaps": 0}

        # Describe gaps for self-dialogue
        gap_descs = []
        for g in gaps[:3]:
            if g["type"] == "high_failure_tool":
                gap_descs.append(f"  - {g['tool']}: 실패율 {g['fail_rate']:.0%}")
            else:
                gap_descs.append(f"  - 미처리 요청 {g['count']}건")
        self._stream(f"{len(gaps)}개 갭 발견:\n" + "\n".join(gap_descs)
                     + "\nLLM에게 개선 도구 설계 요청중...")

        logger.info(f"[L5] Found {len(gaps)} gaps, synthesizing goal")
        goal = self.synthesize_goal(gaps)
        if not goal:
            logger.info("[L5] Goal synthesis failed")
            self._stream("LLM이 도구 설계 실패. 다음에 재시도.")
            return {"gaps": len(gaps), "goal": False}

        # Relevance gate — deterministic pre-filter
        gate_ok, gate_reason = self._relevance_gate(goal)
        if not gate_ok:
            logger.info(f"[L5] Relevance gate rejected: {gate_reason}")
            self._stream(f"관련성 검증 실패: {gate_reason}\n'{goal.get('name', '?')}' 폐기.")
            _audit_log("L5", "relevance_gate_reject", f"{goal.get('name', '?')}: {gate_reason}", success=False)
            _jsonl_append(self.GAP_FILE, {
                "ts_ms": int(time.time() * 1000),
                "gaps_found": len(gaps),
                "goal_name": goal.get("name", ""),
                "rejected": True, "reject_reason": gate_reason,
            })
            return {"gaps": len(gaps), "goal": goal.get("name"), "rejected": gate_reason}

        self._stream(f"'{goal.get('name', '?')}' 도구 생성중...\n"
                     f"설명: {goal.get('description', '')[:300]}\n"
                     f"코드 {len(goal.get('code', ''))}자, 샌드박스 테스트 실행...")

        logger.info(f"[L5] Executing goal: {goal.get('name', '?')}")
        result = self.execute_goal(goal)

        _jsonl_append(self.GAP_FILE, {
            "ts_ms": int(time.time() * 1000),
            "gaps_found": len(gaps),
            "goal_name": goal.get("name", ""),
            "goal_desc": goal.get("description", "")[:300],
            "success": result.get("success", False),
            "reason": result.get("reason", ""),
        })

        if result.get("success"):
            logger.info(f"[L5] SUCCESS: {goal.get('name')}")
            self._stream(f"'{goal.get('name')}' 생성 성공!\n"
                         f"출력: {result.get('output', '')[:150]}\n"
                         f"스킬 라이브러리에 저장 완료. 검증 큐에 등록.")
        else:
            logger.info(f"[L5] FAILED: {result.get('reason')}")
            self._stream(f"'{goal.get('name')}' 실패: {result.get('reason', '?')}")

        return {"gaps": len(gaps), "goal": goal.get("name"), "result": result}
