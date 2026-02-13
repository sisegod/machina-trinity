"""Machina Learning — Memory Operations (search, save, hybrid retrieval, genesis suggestions)."""

import fcntl
import json
import logging
import os
import subprocess
import time

from machina_shared import (
    _jsonl_append,
    BM25Okapi,
    MACHINA_ROOT,
    MEM_DIR,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Topic / Importance Inference
# ---------------------------------------------------------------------------

def _infer_topic_tag(text: str) -> str:
    """Lightweight topic tagging from text keywords (no LLM call)."""
    lower = text.lower()
    tags = {
        "birthday": ["생일", "birthday", "born"],
        "preference": ["좋아", "싫어", "prefer", "favorite"],
        "identity": ["이름", "나이", "직업", "name", "age", "job"],
        "system": ["서버", "gpu", "메모리", "디스크", "server", "memory"],
        "learning": ["배우", "공부", "learn", "study"],
        "project": ["프로젝트", "코드", "project", "code"],
        "schedule": ["일정", "약속", "schedule", "meeting"],
        "fact": [],  # default
    }
    for tag, keywords in tags.items():
        if any(kw in lower for kw in keywords):
            return tag
    return "fact"


def _infer_importance(text: str) -> int:
    """Heuristic importance score 1-5 based on content type."""
    lower = text.lower()
    # Personal info = high importance
    if any(kw in lower for kw in ["생일", "이름", "birthday", "name", "비밀번호"]):
        return 5
    # Preferences = medium-high
    if any(kw in lower for kw in ["좋아", "싫어", "prefer", "favorite"]):
        return 4
    # Facts = medium
    if any(kw in lower for kw in ["기억", "remember", "중요", "important"]):
        return 4
    # Technical info
    if any(kw in lower for kw in ["서버", "gpu", "코드", "project"]):
        return 3
    return 2


# ---------------------------------------------------------------------------
# Memory Save
# ---------------------------------------------------------------------------

def memory_save(text: str, stream: str = "telegram",
                session_id: str = "", topic: str = "") -> str:
    """Save to Machina memory via JSONL append with structured metadata.

    Also feeds text into Graph Memory for entity/relation extraction.
    """
    try:
        mem_dir = MEM_DIR
        mem_dir.mkdir(parents=True, exist_ok=True)
        ts_ms = int(time.time() * 1000)
        # (4c) Structured metadata: topic_tag, importance, session_id
        inferred_topic = topic if topic else _infer_topic_tag(text)
        importance = _infer_importance(text)
        entry = {
            "ts_ms": ts_ms,
            "stream": stream,
            "event": "user_note",
            "text": text,
            "topic_tag": inferred_topic,
            "importance": importance,
        }
        # (4d) Context chain: session_id for conversation flow linking
        if session_id:
            entry["session_id"] = session_id
        mem_file = mem_dir / (stream + ".jsonl")
        _jsonl_append(mem_file, entry)

        # Graph Memory: auto-extract entities and relations
        try:
            from machina_graph import graph_ingest
            graph_ingest(text, metadata={"stream": stream, "topic": inferred_topic})
        except Exception as ge:
            logger.debug(f"Graph ingest error: {ge}")

        logger.info("Memory saved to " + stream + ": " + text[:80])
        return "saved to memory (" + stream + "): " + text[:100]
    except Exception as e:
        logger.error("Memory save error: " + str(e))
        return "memory save error: " + str(e)


# ---------------------------------------------------------------------------
# Memory Search (C++ hybrid + Python BM25 fallback)
# ---------------------------------------------------------------------------

def _cpp_hybrid_memory_search(query: str, stream: str = "telegram", top_k: int = 5) -> str:
    """C++ hybrid memory search via toolhost (BM25 + vector + recency + MMR reranking)."""
    cli_path = os.path.join(MACHINA_ROOT, "build", "machina_cli")
    if not os.path.exists(cli_path):
        return ""
    try:
        input_json = {
            "stream": stream,
            "query": query,
            "mode": "hybrid",
            "top_k": top_k,
            "max_entries": 1000,
            "rerank": "mmr",
            "mmr_lambda": 0.72,
        }
        req = json.dumps(
            {
                "input_json": json.dumps(input_json, ensure_ascii=False),
                "ds_state": {"slots": {}},
            },
            ensure_ascii=False,
        )
        result = subprocess.run(
            [cli_path, "tool_exec", "AID.MEMORY.QUERY.v1"], input=req + "\n",
            capture_output=True, text=True, timeout=5, cwd=MACHINA_ROOT,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
        envelope = json.loads(result.stdout.strip().split("\n")[0])
        if envelope.get("status") != "OK":
            return ""
        output_json = envelope.get("output_json", "")
        if not isinstance(output_json, str) or not output_json:
            return ""
        resp = json.loads(output_json)
        if not isinstance(resp, dict) or not resp.get("ok") or not resp.get("matches"):
            return ""
        texts = []
        for m in resp["matches"][:top_k]:
            raw = m.get("raw", "")
            text = m.get("text", "")
            if not text and raw:
                try:
                    text = json.loads(raw).get("text", raw[:500])
                except (json.JSONDecodeError, TypeError):
                    text = raw[:500]
            if text:
                texts.append(text[:500])
        return "\n".join(texts) if texts else ""
    except Exception as e:
        logger.debug(f"C++ hybrid search fallback: {e}")
        return ""


def _python_bm25_memory_search(query: str, stream: str = "telegram",
                               limit: int = 5, session_id: str = "") -> str:
    """Python BM25 memory search with importance boosting + session context.

    Improvements over plain BM25:
      1. importance boost: score *= (1 + 0.2 * importance) -- high-importance memories surface first
      2. session chain: memories from same session_id get +50% boost
      3. topic matching: if query matches a topic_tag, boost those memories
    """
    mem_file = MEM_DIR / f"{stream}.jsonl"
    if not mem_file.exists():
        return ""
    result = subprocess.run(
        ["tail", "-500", str(mem_file)],
        capture_output=True, text=True, timeout=5,
    )
    if not result.stdout:
        return ""
    all_entries = []  # list of dicts with text + metadata
    for line in result.stdout.strip().split("\n"):
        try:
            entry = json.loads(line)
            text = entry.get("text", entry.get("content", ""))
            # Defensive: JSONL entries may have dict/list values
            if not isinstance(text, str):
                if isinstance(text, dict):
                    text = text.get("text", text.get("content", str(text)))
                elif isinstance(text, list):
                    text = " ".join(str(t) for t in text)
                else:
                    text = str(text) if text else ""
            if text:
                all_entries.append({
                    "text": text,
                    "importance": entry.get("importance", 2),
                    "topic_tag": entry.get("topic_tag", ""),
                    "session_id": entry.get("session_id", ""),
                })
        except json.JSONDecodeError:
            continue
    if not all_entries:
        return ""

    # Split into recent (last 3) and search pool
    recent = all_entries[-3:]
    search_pool = all_entries[:-3] if len(all_entries) > 3 else []

    relevant = []
    if search_pool and query.strip():
        texts = [e["text"] for e in search_pool]
        bm25 = BM25Okapi()
        bm25.index(texts)
        hits = bm25.query(query, top_k=limit * 3)  # oversample for reranking

        # Infer query topic for topic-matching boost
        query_topic = _infer_topic_tag(query)

        scored = []
        for idx, bm25_score in hits:
            if bm25_score < 0.05:
                continue
            entry = search_pool[idx]
            score = bm25_score

            # Importance boost: importance 5 -> 2x, importance 1 -> 1.2x
            imp = entry.get("importance", 2)
            score *= (1.0 + 0.2 * imp)

            # Session context boost: same session -> +50%
            if session_id and entry.get("session_id") == session_id:
                score *= 1.5

            # Topic match boost: matching topic -> +30%
            if query_topic != "fact" and entry.get("topic_tag") == query_topic:
                score *= 1.3

            scored.append((entry, score))

        scored.sort(key=lambda x: -x[1])
        for entry, _ in scored[:limit]:
            tag = entry.get("topic_tag", "")
            prefix = f"[{tag}] " if tag and tag != "fact" else ""
            relevant.append(prefix + entry["text"][:500])

    combined = []
    if relevant:
        combined.append("[관련 기억]")
        combined.extend(relevant)
    if recent:
        combined.append("[최근 대화]")
        combined.extend(r["text"][:500] for r in recent)
    return "\n".join(combined) if combined else ""


def memory_search_recent(query: str, stream: str = "telegram",
                         limit: int = 5, session_id: str = "") -> str:
    """Hybrid memory search: C++ hybrid with Python BM25+importance fallback.

    C++ path: fast hybrid (BM25+vector+recency+MMR). No importance boost yet.
    Python path: BM25 + importance boost + session context + topic matching.
    """
    try:
        if query.strip():
            cpp_result = _cpp_hybrid_memory_search(query, stream, top_k=limit)
            if cpp_result:
                return cpp_result
        return _python_bm25_memory_search(query, stream, limit, session_id=session_id)
    except Exception as e:
        logger.error(f"Memory auto-recall error: {e}")
        return ""


# ---------------------------------------------------------------------------
# Genesis Suggestions (failure pattern analysis -> new tool proposals)
# ---------------------------------------------------------------------------

def _genesis_suggest(tool_stats: dict, fail_types: dict, failures: list):
    """Genesis autonomous suggestion: detect repeated failure patterns and propose new tools.

    Non-destructive -- only writes suggestions to genesis_suggestions.jsonl.
    The user (or a future auto-trigger) decides whether to actually create the tool.

    Triggers when:
    1. A tool fails 3+ times with >60% failure rate
    2. A failure type repeats 3+ times (parse, timeout, runtime)
    3. User requests a capability that no existing tool handles (repeated 'wrong_tool')
    """
    try:
        mem_dir = MEM_DIR
        suggest_file = mem_dir / "genesis_suggestions.jsonl"

        # Load existing suggestions to avoid duplicates
        existing_suggestions = set()
        if suggest_file.exists():
            with open(suggest_file, "r", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            s = json.loads(line)
                            existing_suggestions.add(s.get("suggestion_key", ""))
                        except json.JSONDecodeError:
                            continue
                fcntl.flock(f, fcntl.LOCK_UN)

        new_suggestions = []

        # Pattern 1: Tool with high failure rate -> suggest a better replacement
        for tool, stats in tool_stats.items():
            total = stats["ok"] + stats["fail"]
            if total >= 3 and stats["fail"] / total > 0.6:
                key = f"replace_{tool}"
                if key not in existing_suggestions:
                    new_suggestions.append({
                        "ts_ms": int(time.time() * 1000),
                        "suggestion_key": key,
                        "type": "replace_tool",
                        "target_tool": tool,
                        "reason": f"'{tool}' fails {stats['fail']}/{total} times ({stats['fail']/total:.0%})",
                        "proposal": f"Create a more robust version of '{tool}' with better error handling or alternative approach",
                        "priority": min(stats["fail"], 5),
                    })

        # Pattern 2: Repeated failure type -> suggest a preprocessing/validation tool
        for ftype, count in fail_types.items():
            if count >= 3:
                key = f"fix_{ftype}"
                if key not in existing_suggestions:
                    proposals = {
                        "parse": "Create an input sanitizer/validator tool that pre-checks format before tool execution",
                        "timeout": "Create an async wrapper tool with progressive timeout and partial result capture",
                        "runtime": "Create a sandbox pre-check tool that validates commands before execution",
                    }
                    new_suggestions.append({
                        "ts_ms": int(time.time() * 1000),
                        "suggestion_key": key,
                        "type": "new_tool",
                        "failure_pattern": ftype,
                        "reason": f"'{ftype}' errors repeat {count}x in recent experiences",
                        "proposal": proposals.get(ftype, f"Create a tool to handle '{ftype}' failures"),
                        "priority": min(count, 5),
                    })

        # Pattern 3: Repeated 'wrong_tool' -> detect unmet capability
        wrong_tool_requests = []
        for f in failures:
            preview = f.get("result_preview", "").lower()
            req = f.get("user_request", "")
            if not preview or ("error" not in preview[:30] and "timeout" not in preview):
                # Likely wrong_tool or empty -- user wanted something we can't do
                wrong_tool_requests.append(req)

        if len(wrong_tool_requests) >= 3:
            # Cluster by keyword overlap
            key = f"capability_gap_{len(wrong_tool_requests)}"
            if key not in existing_suggestions:
                sample = "; ".join(r[:60] for r in wrong_tool_requests[:3])
                new_suggestions.append({
                    "ts_ms": int(time.time() * 1000),
                    "suggestion_key": key,
                    "type": "new_capability",
                    "reason": f"{len(wrong_tool_requests)} requests could not be handled by existing tools",
                    "sample_requests": sample,
                    "proposal": "Analyze these requests and create a Genesis tool to handle this capability gap",
                    "priority": min(len(wrong_tool_requests), 5),
                })

        # Write new suggestions
        if new_suggestions:
            for s in new_suggestions:
                _jsonl_append(suggest_file, s)
            logger.info(f"Genesis suggestions: {len(new_suggestions)} new proposals recorded")
    except Exception as e:
        logger.error(f"Genesis suggestion error: {e}")
