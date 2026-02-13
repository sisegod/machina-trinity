"""Machina Learning System — experience recording, reflection, insight extraction, skill management, wisdom retrieval."""

import fcntl
import json
import hashlib
import logging
import time
import re
from pathlib import Path

from machina_shared import (
    _jsonl_append,
    _jsonl_read,
    _normalize_tool_name,
    BM25Okapi,
    MEM_DIR,
    EXPERIENCE_STREAM,
    INSIGHTS_STREAM,
    SKILLS_STREAM,
)

# Re-export memory operations so existing `from machina_learning import X` still works
from machina_learning_memory import (          # noqa: F401
    _infer_topic_tag,
    _infer_importance,
    memory_save,
    _cpp_hybrid_memory_search,
    _python_bm25_memory_search,
    memory_search_recent,
    _genesis_suggest,
)

logger = logging.getLogger(__name__)


def experience_record(user_text: str, intent: dict, result: str, success: bool,
                      elapsed: float = 0.0):
    """Record an experience after every interaction (ExpeL-style).

    Quality gate: rejects dummy/auto-test entries with identical expected/got,
    trivial result previews, stress_test spam, and 24h tool+result dedup.
    """
    try:
        mem_dir = MEM_DIR
        mem_dir.mkdir(parents=True, exist_ok=True)

        intent_type = intent.get("type", "")
        tool = ""
        if intent_type == "action":
            actions = intent.get("actions", [])
            if actions:
                raw_tool = actions[0].get("aid", actions[0].get("kind", ""))
                tool = _normalize_tool_name(raw_tool)

        # Quality gate: reject dummy/low-value experiences
        result_str = result[:200] if result else ""
        if result_str:
            rl = result_str.lower()
            # Reject "expected=X, got=X" identical pairs (auto-test dummy)
            if "expected=" in rl and "got=" in rl:
                parts = re.findall(r'expected=([^,]+),?\s*got=(.+)', rl)
                if parts and parts[0][0].strip() == parts[0][1].strip():
                    logger.debug(f"Experience gate: rejected identical expected/got: {result_str[:60]}")
                    return
            # Reject stress_test spam
            if rl.count("stress_test") >= 3:
                logger.debug("Experience gate: rejected stress_test spam")
                return

        # 24h dedup: skip if same tool+success combination recorded within 24h
        tool_key = tool or intent_type
        if tool_key:
            exp_file = mem_dir / f"{EXPERIENCE_STREAM}.jsonl"
            if exp_file.exists():
                now_ms = int(time.time() * 1000)
                day_ms = 24 * 3600 * 1000
                recent_exps = _jsonl_read(exp_file, max_lines=30)
                for prev in recent_exps:
                    prev_ts = prev.get("ts_ms", 0)
                    if now_ms - prev_ts > day_ms:
                        continue
                    if (prev.get("tool_used") == tool_key
                            and prev.get("success") == success
                            and prev.get("result_preview", "")[:80] == result_str[:80]):
                        logger.debug(f"Experience dedup: {tool_key} same result within 24h — skipped")
                        return

        entry = {
            "ts_ms": int(time.time() * 1000),
            "event": "experience",
            "stream": EXPERIENCE_STREAM,
            "user_request": user_text[:1000],
            "intent_type": intent_type,
            "tool_used": tool or intent_type,
            "success": success,
            "elapsed_sec": round(elapsed, 1),
            "result_preview": result_str,
        }

        exp_file = mem_dir / f"{EXPERIENCE_STREAM}.jsonl"
        _jsonl_append(exp_file, entry)

        # Graph Memory: ingest user request for entity extraction
        if user_text and len(user_text) >= 10:
            try:
                from machina_graph import graph_ingest
                graph_ingest(user_text, metadata={"source": "experience", "tool": tool_key})
            except Exception as e:
                logger.debug(f"{type(e).__name__}: {e}")
                pass

        # On failure, auto-reflect
        if not success:
            reflect_on_failure(user_text, intent, result)

        # Periodically extract insights (every 10 experiences)
        try:
            with open(exp_file, "r", encoding="utf-8") as _f:
                fcntl.flock(_f, fcntl.LOCK_SH)
                line_count = sum(1 for _ in _f)
                fcntl.flock(_f, fcntl.LOCK_UN)
            if line_count > 0 and line_count % 10 == 0:
                _extract_insights(exp_file)
        except Exception as e_inner:
            logger.debug(f"Insight extraction trigger error: {e_inner}")

    except Exception as e:
        logger.error(f"Experience record error: {e}")


def reflect_on_failure(user_text: str, intent: dict, result: str):
    """Reflexion-style: classify failure and record alternative action."""
    try:
        mem_dir = MEM_DIR
        mem_dir.mkdir(parents=True, exist_ok=True)
        # Defensive: coerce result to string (may receive dict/list)
        if not isinstance(result, str):
            if isinstance(result, dict):
                result = result.get("content", "") or json.dumps(result, ensure_ascii=False)
            elif isinstance(result, list):
                result = "\n".join(str(r) for r in result)
            else:
                result = str(result) if result else ""
        result_lower = (result or "").lower()

        # Classify failure type
        if "파싱 실패" in result_lower or "json parse" in result_lower or "jsondecode" in result_lower:
            fail_type = "parse_error"
            alternative = "retry with simpler prompt or fallback to direct LLM"
        elif "timeout" in result_lower or "timed out" in result_lower:
            fail_type = "tool_error"
            alternative = "use shorter timeout or simpler command"
        elif "error" in result_lower[:80] or "traceback" in result_lower[:80]:
            fail_type = "tool_error"
            alternative = "check command syntax or tool availability"
        elif not result or not result.strip():
            fail_type = "empty"
            alternative = "tool may need different input format"
        else:
            fail_type = "wrong_tool"
            alternative = "try different tool or rephrase as chat"

        # Build actionable reflection
        intent_type = intent.get("type", "")
        tool_used = ""
        if intent_type == "action":
            actions = intent.get("actions", [])
            if actions:
                tool_used = _normalize_tool_name(actions[0].get("aid", actions[0].get("kind", "")))

        reflection = {
            "ts_ms": int(time.time() * 1000),
            "event": "reflection",
            "stream": INSIGHTS_STREAM,
            "type": "failure",
            "fail_type": fail_type,
            "user_request": user_text[:1000],
            "tool_used": tool_used,
            "intent_tried": json.dumps(intent, ensure_ascii=False)[:800],
            "error_preview": result[:1000] if result else "no output",
            "alternative": alternative,
            "importance": 1,
        }

        insights_file = mem_dir / f"{INSIGHTS_STREAM}.jsonl"
        _jsonl_append(insights_file, reflection)

        logger.info(f"Reflection recorded: {user_text[:50]} -> {fail_type} | alt: {alternative}")
    except Exception as e:
        logger.error(f"Reflection record error: {e}")


def _extract_insights(exp_file: Path):
    """ExpeL-style insight extraction: compare success vs failure, extract rules."""
    try:
        lines = []
        with open(exp_file, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            for line in f:
                lines.append(line.strip())
            fcntl.flock(f, fcntl.LOCK_UN)
        recent = lines[-30:]
        successes, failures, tool_stats = [], [], {}
        for line in recent:
            try:
                e = json.loads(line)
                tool = e.get("tool_used", "") or e.get("intent_type", "chat")
                tool_stats.setdefault(tool, {"ok": 0, "fail": 0})
                if e.get("success"):
                    successes.append(e)
                    tool_stats[tool]["ok"] += 1
                else:
                    failures.append(e)
                    tool_stats[tool]["fail"] += 1
            except json.JSONDecodeError:
                continue

        insights_file = MEM_DIR / f"{INSIGHTS_STREAM}.jsonl"
        rules = []
        for tool, stats in tool_stats.items():
            total = stats["ok"] + stats["fail"]
            if total >= 3 and stats["fail"] / total > 0.5:
                rules.append(f"AVOID: '{tool}' fails often ({stats['fail']}/{total}). Try alternative tools.")
            elif total >= 3 and stats["ok"] / total > 0.8:
                rules.append(f"PREFER: '{tool}' is reliable ({stats['ok']}/{total} success).")
        fail_types = {}
        for f in failures:
            preview = f.get("result_preview", "")[:100].lower()
            if "\ud30c\uc2f1 \uc2e4\ud328" in preview or "json" in preview:
                fail_types["parse"] = fail_types.get("parse", 0) + 1
            elif "timeout" in preview:
                fail_types["timeout"] = fail_types.get("timeout", 0) + 1
            elif "error" in preview[:30]:
                fail_types["runtime"] = fail_types.get("runtime", 0) + 1
        for ftype, count in fail_types.items():
            if count >= 2:
                rules.append(f"PATTERN: '{ftype}' errors repeat ({count}x). Check input format before execution.")
        success_patterns = {}
        for s in successes[-10:]:
            req = s.get("user_request", "")[:80]
            tool = s.get("tool_used", "") or s.get("intent_type", "")
            if tool and req:
                key = tool
                success_patterns.setdefault(key, []).append(req)
        for tool, reqs in success_patterns.items():
            if len(reqs) >= 2:
                rules.append(f"WORKS: '{tool}' succeeds for requests like: {reqs[0][:50]}")

        # Write compound insight — with dedup against recent insights
        # Quality gate: reject empty or trivially short rules
        rules = [r for r in rules if r and len(r) > 10]
        if rules:
            # Quality score: based on data volume + rule specificity
            data_score = min(len(recent) / 30.0, 1.0)  # more data = higher quality
            specificity = sum(1 for r in rules if any(k in r for k in ["PREFER", "AVOID", "PATTERN"])) / max(len(rules), 1)
            quality_score = round(0.4 * data_score + 0.6 * specificity, 3)

            # Skip low-quality insights (score < 0.3)
            if quality_score < 0.3:
                logger.info(f"ExpeL quality gate: score={quality_score} < 0.3 — skipped")
            else:
                # Dedup: check if identical rules already exist in last 20 insights
                _skip = False
                existing_insights = _jsonl_read(insights_file, max_lines=20)
                rules_set = set(rules[:10])
                for ei in existing_insights:
                    if ei.get("type") == "rules":
                        existing_rules = set(ei.get("rules", []))
                        # Skip if 60%+ overlap with any recent insight
                        if rules_set and existing_rules:
                            overlap = len(rules_set & existing_rules) / max(len(rules_set), 1)
                            if overlap >= 0.6:
                                _skip = True
                                break
                if _skip:
                    logger.info(f"ExpeL insight dedup: {len(rules)} rules match recent insight — skipped")
                else:
                    insight = {
                        "ts_ms": int(time.time() * 1000),
                        "event": "insight",
                        "stream": INSIGHTS_STREAM,
                        "type": "rules",
                        "rules": rules[:10],
                        "tool_stats": tool_stats,
                        "total_experiences": len(recent),
                        "importance": len(rules),
                        "quality_score": quality_score,
                    }
                    _jsonl_append(insights_file, insight)
                    logger.info(f"ExpeL insight: {len(rules)} rules (q={quality_score}) from {len(recent)} experiences")

                    # Trigger Genesis suggestion check only after quality gate pass
                    _genesis_suggest(tool_stats, fail_types, failures)
    except Exception as e:
        logger.error(f"Insight extraction error: {e}")


def skill_record(user_request: str, lang: str, code: str, result: str):
    """Voyager-style: record successful code as a reusable skill.

    Only records code that executed successfully (non-empty, no error).
    These skills can be retrieved for similar future requests.
    """
    try:
        if not code or not result:
            return
        if "error" in result.lower()[:50] or "traceback" in result.lower()[:50]:
            return

        mem_dir = MEM_DIR
        mem_dir.mkdir(parents=True, exist_ok=True)

        # Auto-generate name: first meaningful phrase from request
        name = user_request[:60].strip()
        # Extract tags from code: imports, builtins, patterns
        tags = set()
        tags.add(lang)
        for imp_match in re.findall(r'(?:import|from)\s+(\w+)', code):
            tags.add(imp_match)
        for kw in ["sort", "search", "math", "file", "web", "parse", "calc", "print", "loop", "api"]:
            if kw in code.lower():
                tags.add(kw)

        skill = {
            "ts_ms": int(time.time() * 1000),
            "event": "skill",
            "stream": SKILLS_STREAM,
            "name": name,
            "tags": sorted(tags),
            "request": user_request[:500],
            "lang": lang,
            "code": code[:3000],
            "result_preview": result[:1000],
        }

        skills_file = mem_dir / f"{SKILLS_STREAM}.jsonl"
        _jsonl_append(skills_file, skill)

        logger.info(f"Skill recorded: {user_request[:50]} ({lang})")
    except Exception as e:
        logger.error(f"Skill record error: {e}")


def skill_search(query: str, limit: int = 3) -> str:
    """BM25-ranked skill retrieval (Voyager-style). Returns best-match code."""
    try:
        mem_dir = MEM_DIR
        skills_file = mem_dir / f"{SKILLS_STREAM}.jsonl"
        if not skills_file.exists():
            return ""

        entries = []
        with open(skills_file, "r", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            fcntl.flock(f, fcntl.LOCK_UN)

        if not entries:
            return ""

        # Deduplicate by code hash (same code = same skill)
        seen = set()
        unique = []
        for e in entries:
            code_hash = hashlib.sha256(e.get("code", "").encode()).hexdigest()
            if code_hash not in seen:
                seen.add(code_hash)
                unique.append(e)
        entries = unique[-50:]  # keep latest 50 unique skills

        # BM25 search over request + description text
        search_texts = [e.get("request", "") + " " + e.get("description", "") for e in entries]
        bm25 = BM25Okapi()
        bm25.index(search_texts)
        hits = bm25.query(query, top_k=limit)

        matches = []
        for idx, score in hits:
            if score < 0.1:
                continue
            e = entries[idx]
            code = e.get("code", "")[:1000]
            matches.append(f"[{e.get('lang','?')}] {e.get('request','')} -> {code[:500]}")

        return "\n".join(matches) if matches else ""
    except Exception as e:
        logger.error(f"Skill search error: {e}")
        return ""


def wisdom_retrieve(user_text: str, limit: int = 5) -> str:
    """Retrieve accumulated wisdom for intent enrichment (ExpeL + Reflexion + Skills).

    Reads from insights.jsonl + skills.jsonl:
      1. rules (ExpeL) -- natural language rules extracted from experience patterns
      2. failure (Reflexion) -- specific failures with alternatives
      3. pattern (legacy) -- tool success/fail stats
      4. skills (Voyager) -- relevant code snippets for similar requests

    Returns compact string injected before LLM intent classification.
    Cap: 2000 chars for rich context with insights + skill hints.
    """
    try:
        mem_dir = MEM_DIR
        insights_file = mem_dir / f"{INSIGHTS_STREAM}.jsonl"

        rules = []         # ExpeL extracted rules
        reflections = []    # Reflexion failure alternatives
        patterns = []       # Legacy tool stats

        if insights_file.exists():
            lines = []
            with open(insights_file, "r", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                for line in f:
                    line = line.strip()
                    if line:
                        lines.append(line)
                fcntl.flock(f, fcntl.LOCK_UN)

            for line in reversed(lines[-30:]):
                try:
                    entry = json.loads(line)
                    etype = entry.get("type", "")

                    # ExpeL rules (highest priority) — take top 5
                    if etype == "rules" and not rules:
                        rules = entry.get("rules", [])[:5]

                    # Reflexion failures with alternatives
                    elif etype == "failure" and len(reflections) < 3:
                        alt = entry.get("alternative", "")
                        ftype = entry.get("fail_type", "")
                        req = entry.get("user_request", "")[:100]
                        if alt:
                            reflections.append(f"{ftype}: {req}\u2192{alt}")

                    # Legacy pattern stats
                    elif etype == "pattern" and not patterns:
                        st = entry.get("success_tools", {})
                        if st:
                            top = sorted(st.items(), key=lambda x: -x[1])[:3]
                            patterns.append("good:" + ",".join(f"{k}({v})" for k, v in top))

                except json.JSONDecodeError:
                    continue

        parts = []
        if rules:
            parts.append(" | ".join(rules[:5]))
        if reflections:
            parts.append("avoid: " + "; ".join(reflections[:3]))
        if patterns and not rules:
            parts.append(" ".join(patterns))

        # Skill hints: BM25 search for relevant code snippets
        if user_text.strip():
            skill_hint = skill_search(user_text, limit=3)
            if skill_hint:
                parts.append(f"[skills] {skill_hint[:500]}")

        # Graph Memory: entity/relation context
        if user_text.strip():
            try:
                from machina_graph import graph_query
                graph_ctx = graph_query(user_text, limit=5)
                if graph_ctx:
                    parts.append(graph_ctx)
            except Exception as ge:
                logger.debug(f"Graph query error in wisdom: {ge}")

        return " ".join(parts)[:2000] if parts else ""
    except Exception as e:
        logger.error(f"Wisdom retrieve error: {e}")
        return ""


# ---------------------------------------------------------------------------
# RewardTracker — rolling-window reward signal from experience stream
# ---------------------------------------------------------------------------
class RewardTracker:
    """Compares success_rate across rolling windows to detect regression.

    Window: last N experiences vs previous N. Regression threshold: >5% drop.
    """

    SNAPSHOT_FILE = MEM_DIR / "reward_snapshots.jsonl"
    WINDOW = 100
    THRESHOLD = 0.05

    def compute(self, window: int = 0) -> dict:
        """Metrics over last N experiences: success_rate, avg_latency."""
        ws = window or self.WINDOW
        exps = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=ws)
        if len(exps) < 5:
            return {"success_rate": 0.0, "avg_latency": 0.0, "count": 0}
        ok = sum(1 for e in exps if e.get("success"))
        lats = [e.get("elapsed_sec", 0) for e in exps
                if e.get("elapsed_sec", 0) > 0]
        return {
            "success_rate": round(ok / len(exps), 4),
            "avg_latency": round(sum(lats) / max(len(lats), 1), 2),
            "count": len(exps),
        }

    def detect_regression(self) -> dict:
        """Compare current vs previous window. {regressed, delta, ...}"""
        ws = self.WINDOW
        exps = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl",
                           max_lines=ws * 2)
        if len(exps) < ws:
            return {"regressed": False, "reason": "insufficient_data"}
        current = exps[-ws:]
        prev_end = len(exps) - ws
        previous = exps[max(prev_end - ws, 0):prev_end]
        if not previous:
            return {"regressed": False, "reason": "no_previous_window"}
        cur_rate = sum(1 for e in current if e.get("success")) / len(current)
        prev_rate = sum(1 for e in previous if e.get("success")) / len(previous)
        delta = cur_rate - prev_rate
        return {
            "regressed": delta < -self.THRESHOLD,
            "current_rate": round(cur_rate, 4),
            "previous_rate": round(prev_rate, 4),
            "delta": round(delta, 4),
        }

    def snapshot(self) -> dict:
        """Save current metrics to snapshot file."""
        m = self.compute()
        m["ts_ms"] = int(time.time() * 1000)
        _jsonl_append(self.SNAPSHOT_FILE, m)
        return m

    def find_suspects(self) -> list:
        """Tools with >50% failure rate in recent window."""
        exps = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl",
                           max_lines=self.WINDOW)
        stats = {}
        for e in exps:
            tool = e.get("tool_used", "") or e.get("intent_type", "")
            if tool:
                stats.setdefault(tool, {"ok": 0, "fail": 0})
                stats[tool]["ok" if e.get("success") else "fail"] += 1
        suspects = []
        for tool, s in stats.items():
            total = s["ok"] + s["fail"]
            if total >= 3 and s["fail"] / total > 0.5:
                suspects.append({"tool": tool,
                                 "fail_rate": round(s["fail"] / total, 2)})
        suspects.sort(key=lambda x: -x["fail_rate"])
        return suspects[:5]

# Policy Distillation — (keyword→tool, success_rate) from experiences
_DISTILL_CACHE: dict = {}
_DISTILL_TS: float = 0.0

def distill_rules(force: bool = False) -> dict:
    """Build distilled rule cache.  {keyword: (tool, success_rate, count)}."""
    global _DISTILL_CACHE, _DISTILL_TS
    if not force and _DISTILL_CACHE and (time.time() - _DISTILL_TS) < 600:
        return _DISTILL_CACHE
    exps = _jsonl_read(MEM_DIR / f"{EXPERIENCE_STREAM}.jsonl", max_lines=500)
    agg: dict = {}
    for e in exps:
        tool = e.get("tool_used", "") or e.get("intent_type", "")
        kw = e.get("keyword", "") or e.get("query", "")
        if not tool or not kw: continue
        kw_lower = kw.lower().strip()[:40]
        agg.setdefault(kw_lower, {}).setdefault(tool, {"ok": 0, "fail": 0})
        agg[kw_lower][tool]["ok" if e.get("success") else "fail"] += 1
    rules: dict = {}
    for kw, tools in agg.items():
        best_tool, best_rate, best_cnt = "", 0.0, 0
        for t, s in tools.items():
            total = s["ok"] + s["fail"]
            if total < 2: continue
            rate = s["ok"] / total
            if rate > best_rate or (rate == best_rate and total > best_cnt):
                best_tool, best_rate, best_cnt = t, rate, total
        if best_tool and best_rate >= 0.7:
            rules[kw] = (best_tool, round(best_rate, 2), best_cnt)
    _DISTILL_CACHE, _DISTILL_TS = rules, time.time()
    return rules

def _norm_tokens(s: str) -> set:
    """Normalize text to token set for Jaccard matching."""
    return {t for t in re.sub(r'[^\w\s]', '', s.lower()).split() if len(t) > 1}

def lookup_distilled(text: str, intent_key: str = "") -> tuple:
    """Check distilled rules via Jaccard token overlap. Returns (tool, confidence) or (None, 0)."""
    if not text or len(text) < 2: return (None, 0.0)
    rules = distill_rules()
    if not rules: return (None, 0.0)
    if intent_key and intent_key in rules:
        tool, rate, cnt = rules[intent_key]
        if rate >= 0.8: return (tool, rate)
    txt_tok = _norm_tokens(text)
    if not txt_tok: return (None, 0.0)
    best_match, best_score, best_rate, best_cnt = None, 0.0, 0.0, 0
    for kw, (tool, rate, cnt) in rules.items():
        kw_tok = _norm_tokens(kw)
        if not kw_tok: continue
        inter = len(txt_tok & kw_tok)
        if inter == 0: continue
        jaccard = inter / len(txt_tok | kw_tok)
        if jaccard < 0.3: continue
        score = jaccard * rate
        if score > best_score or (score == best_score and cnt > best_cnt):
            best_match, best_score, best_rate, best_cnt = tool, score, rate, cnt
    return (best_match, best_rate) if best_match and best_rate >= 0.8 else (None, 0.0)
