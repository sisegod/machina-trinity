#!/usr/bin/env python3
"""Machina Shared Utilities — common helpers used across all modules.

Contains:
  - BM25Okapi: lightweight pure-Python BM25 ranking
  - JSONL helpers: atomic append with flock, tail-read
  - Constants: paths, stream names, tool normalization
  - _call_ollama: direct Ollama API call (no telegram dependency)
"""

import fcntl
import json
import logging
import math as _math
import os
import re
import shutil
import subprocess as _subprocess
import threading
import urllib.request
from pathlib import Path

logger = logging.getLogger("machina")

# ---------------------------------------------------------------------------
# Constants + Config — delegated to machina_config.py, re-exported here.
# ---------------------------------------------------------------------------
from machina_config import (  # noqa: F401, E402
    MACHINA_ROOT, MEM_DIR, CHAT_LOG_DIR, CHAT_LOG_FILE,
    EXPERIENCE_STREAM, INSIGHTS_STREAM, SKILLS_STREAM,
    ENTITIES_STREAM, RELATIONS_STREAM,
    UTILS_DIR, UTILS_MANIFEST, MANIFEST_PATH,
    _CONFIG_STATE_FILE, _CONFIG_KEYS,
    load_runtime_config, save_runtime_config,
    get_active_model, get_active_url, get_active_backend,
    get_brain_label, is_auto_route_enabled, set_auto_route,
)

# ---------------------------------------------------------------------------
# Bubblewrap Sandbox — OS-level isolation for LLM-generated code execution
# ---------------------------------------------------------------------------
_BWRAP_PATH = shutil.which("bwrap")
_BWRAP_WARNED = False


def sandboxed_run(cmd: list, *, timeout: int = 30, cwd: str = None,
                  writable_dirs: list = None,
                  allow_net: bool = False) -> "_subprocess.CompletedProcess":
    """Run a command inside a bubblewrap sandbox if available.

    Falls back to plain subprocess.run when bwrap is not installed.
    In PROD profile (MACHINA_PROFILE=prod): logs a WARNING on every fallback.
    If MACHINA_BWRAP_REQUIRED=1: raises RuntimeError instead of falling back.
    The sandbox provides: read-only root, /dev, /proc, private /tmp,
    network disabled (--unshare-net) unless allow_net=True, die-with-parent.
    Extra writable dirs can be specified (e.g. work/).
    """
    if _BWRAP_PATH:
        bwrap_cmd = [
            _BWRAP_PATH,
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
        ]
        if not allow_net:
            bwrap_cmd.append("--unshare-net")
        bwrap_cmd += [
            "--unshare-pid",
            "--die-with-parent",
        ]
        for d in (writable_dirs or []):
            real_d = os.path.realpath(d)
            if os.path.isdir(real_d):
                bwrap_cmd += ["--bind", real_d, real_d]
        bwrap_cmd += ["--"] + cmd
        return _subprocess.run(
            bwrap_cmd,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=cwd,
        )
    else:
        global _BWRAP_WARNED
        is_prod = os.getenv("MACHINA_PROFILE", "").lower() == "prod"
        bwrap_required = os.getenv("MACHINA_BWRAP_REQUIRED", "0") in ("1", "true", "yes")
        if bwrap_required:
            raise RuntimeError(
                "MACHINA_BWRAP_REQUIRED=1 but bwrap is not installed. "
                "Install bubblewrap: apt install bubblewrap"
            )
        if is_prod and not _BWRAP_WARNED:
            logger.warning(
                "PROD profile active but bwrap not found — "
                "LLM code execution is NOT sandboxed. "
                "Install bubblewrap: apt install bubblewrap"
            )
            _BWRAP_WARNED = True
        return _subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=cwd,
        )


# ---------------------------------------------------------------------------
# BM25 Okapi — Pure Python (no numpy)
# ---------------------------------------------------------------------------
class BM25Okapi:
    """Lightweight BM25 Okapi ranking. Zero external dependencies."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._docs = []
        self._doc_len = []
        self._avgdl = 0.0
        self._doc_freqs = []
        self._idf = {}
        self._corpus_size = 0

    # Korean particle suffixes to strip (common postpositions/endings)
    _KO_SUFFIXES = re.compile(
        r'(은|는|이|가|을|를|의|에|와|과|도|로|으로|에서|까지|부터|만|밖에|처럼|같이|야|이야|이다|다|해|였어|인데|하는|하고)$'
    )

    @classmethod
    def tokenize(cls, text: str) -> list:
        """Whitespace + lowercase tokenizer with Korean particle stripping.

        Korean agglutinative morphology: '생일은' → '생일', '김철수야' → '김철수'.
        This dramatically improves recall for BM25 search on Korean text.
        """
        raw = [w for w in re.sub(r'[^\w가-힣]', ' ', text.lower()).split() if len(w) >= 2]
        result = []
        for w in raw:
            stripped = cls._KO_SUFFIXES.sub('', w)
            if len(stripped) >= 2:
                if stripped != w:
                    result.extend([w, stripped])  # keep both forms for recall
                else:
                    result.append(w)
            else:
                result.append(w)
        return result

    def index(self, documents: list):
        """Build index from list of raw text strings."""
        self._docs = [self.tokenize(d) for d in documents]
        self._corpus_size = len(self._docs)
        if self._corpus_size == 0:
            return
        nd = {}
        total_len = 0
        self._doc_freqs = []
        self._doc_len = []
        for doc in self._docs:
            self._doc_len.append(len(doc))
            total_len += len(doc)
            freqs = {}
            for word in doc:
                freqs[word] = freqs.get(word, 0) + 1
            self._doc_freqs.append(freqs)
            for word in freqs:
                nd[word] = nd.get(word, 0) + 1
        self._avgdl = total_len / self._corpus_size if self._corpus_size else 1.0
        self._idf = {}
        for word, freq in nd.items():
            idf = _math.log((self._corpus_size - freq + 0.5) / (freq + 0.5) + 1.0)
            self._idf[word] = max(idf, 0.01)

    def query(self, text: str, top_k: int = 5) -> list:
        """Return top-k (index, score) tuples sorted by relevance."""
        if not self._corpus_size:
            return []
        q_tokens = self.tokenize(text)
        scores = [0.0] * self._corpus_size
        for q in q_tokens:
            idf = self._idf.get(q, 0.0)
            for i, df in enumerate(self._doc_freqs):
                tf = df.get(q, 0)
                if tf == 0:
                    continue
                dl = self._doc_len[i]
                score = idf * (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
                scores[i] += score
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])
        return [(idx, sc) for idx, sc in ranked[:top_k] if sc > 0]


# ---------------------------------------------------------------------------
# JSONL Helpers
# ---------------------------------------------------------------------------
def _jsonl_append(filepath, obj: dict):
    """Atomically append a JSON line with file locking."""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            line = json.dumps(obj, ensure_ascii=False) + "\n"
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _jsonl_read(filepath, max_lines: int = 0) -> list:
    """Read JSONL file, optionally last N lines only.

    M14 fix: read all lines under LOCK_SH, release lock immediately,
    then parse JSON outside the lock to minimize writer blocking.
    """
    if not Path(filepath).exists():
        return []
    import fcntl
    with open(filepath, "r", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            raw_lines = f.readlines()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    entries = []
    for line in raw_lines:
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if max_lines > 0:
        return entries[-max_lines:]
    return entries


# ---------------------------------------------------------------------------
# Tool Name Normalization
# ---------------------------------------------------------------------------
_AID_TO_SHORT = {
    "SHELL": "shell", "FILE": "file", "MEMORY": "memory", "NET": "web",
    "GENESIS": "genesis", "VECDB": "vecdb", "EMBED": "embed",
}


def _normalize_tool_name(raw: str) -> str:
    """Normalize AID.SHELL.EXEC.v1 -> 'shell', 'code' -> 'code', etc."""
    if not raw:
        return ""
    if "." not in raw:
        return raw
    parts = raw.upper().replace("AID.", "").split(".")
    if parts:
        return _AID_TO_SHORT.get(parts[0], parts[0].lower())
    return raw.lower()


# ---------------------------------------------------------------------------
# Ollama Direct API Call
# ---------------------------------------------------------------------------
def _call_ollama(prompt: str, system: str = "", max_tokens: int = 1024,
                 temperature: float = 0.7, format_json: bool = False,
                 timeout: int = 60, model: str = None,
                 think: bool = None) -> str:
    """Direct Ollama API call — no telegram dependency.

    Always reads model/URL from os.environ for fresh state after config changes.
    Args:
        think: Controls Qwen3 thinking mode. False=disable (saves tokens),
               None=default (auto, uses thinking field as fallback).
    """
    active_model = model or os.getenv("OAI_COMPAT_MODEL", "qwen3:14b-q8_0")
    active_url = os.getenv("OAI_COMPAT_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    body = {
        "model": active_model,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "repeat_penalty": 1.1,
            "top_p": 0.9,
        },
        "messages": [],
    }
    if system:
        body["messages"].append({"role": "system", "content": system})
    body["messages"].append({"role": "user", "content": prompt})
    if format_json:
        body["format"] = "json"
    if think is not None:
        body["think"] = think

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(active_url + "/api/chat", data=data, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8", errors="replace"))
        msg = resp.get("message", {})
        content = msg.get("content", "")
        # Qwen3 thinking mode: content may be empty, actual output in "thinking"
        if not content and msg.get("thinking"):
            content = msg["thinking"]
        return content
    except Exception as e:
        logger.error(f"Ollama call failed ({active_model}@{active_url}): {e}")
        return ""


# ---------------------------------------------------------------------------
# Robust JSON extraction for Claude API responses
# ---------------------------------------------------------------------------
import re as _re

def _extract_json_robust(text: str) -> str:
    """3-layer JSON extraction: raw → fence strip → bracket match."""
    text = text.strip()
    # Layer 1: already valid JSON
    if text.startswith(("{", "[")):
        return text
    # Layer 2: markdown fence strip
    m = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith(("{", "[")):
            return candidate
    # Layer 3: bracket-depth match for first { ... }
    idx = text.find("{")
    if idx >= 0:
        depth, end_idx = 0, idx
        for i in range(idx, len(text)):
            if text[i] == "{": depth += 1
            elif text[i] == "}": depth -= 1
            if depth == 0:
                end_idx = i + 1
                break
        return text[idx:end_idx]
    return text


# ---------------------------------------------------------------------------
# Engine LLM Router — auto-selects Ollama or Anthropic for autonomic engine
# ---------------------------------------------------------------------------
_engine_llm_daily_calls = {"date": "", "count": 0, "tokens": 0}
_engine_llm_lock = threading.Lock()
_ENGINE_DAILY_CALL_LIMIT = int(os.getenv("MACHINA_ENGINE_DAILY_CALLS", "500"))
_ENGINE_DAILY_TOKEN_LIMIT = int(os.getenv("MACHINA_ENGINE_DAILY_TOKENS", "200000"))


def _call_engine_llm(prompt: str, system: str = "", max_tokens: int = 1024,
                     temperature: float = 0.7, format_json: bool = False,
                     timeout: int = 60, think: bool = None) -> str:
    """LLM router for autonomic engine — respects active chat backend.

    When MACHINA_CHAT_BACKEND=anthropic and API key is available, uses Claude.
    Otherwise falls back to Ollama (local, free).
    Includes daily call/token budget to prevent cost explosion.
    """
    import time as _t
    backend = os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    # If not anthropic or no key, always use Ollama (free)
    if backend != "anthropic" or not api_key:
        return _call_ollama(prompt, system=system, max_tokens=max_tokens,
                            temperature=temperature, format_json=format_json,
                            timeout=timeout, think=think)

    # Budget guard for cloud API (thread-safe)
    today = _t.strftime("%Y-%m-%d")
    with _engine_llm_lock:
        if _engine_llm_daily_calls["date"] != today:
            _engine_llm_daily_calls["date"] = today
            _engine_llm_daily_calls["count"] = 0
            _engine_llm_daily_calls["tokens"] = 0
        if _engine_llm_daily_calls["count"] >= _ENGINE_DAILY_CALL_LIMIT:
            logger.warning(f"Engine daily call limit ({_ENGINE_DAILY_CALL_LIMIT}) reached — fallback to Ollama")
            return _call_ollama(prompt, system=system, max_tokens=max_tokens,
                                temperature=temperature, format_json=format_json,
                                timeout=timeout, think=think)
        if _engine_llm_daily_calls["tokens"] >= _ENGINE_DAILY_TOKEN_LIMIT:
            logger.warning(f"Engine daily token limit ({_ENGINE_DAILY_TOKEN_LIMIT}) reached — fallback to Ollama")
            return _call_ollama(prompt, system=system, max_tokens=max_tokens,
                                temperature=temperature, format_json=format_json,
                                timeout=timeout, think=think)

    # Use Anthropic Claude
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")

    messages = []
    if prompt:
        messages.append({"role": "user", "content": prompt})
    if not messages:
        messages.append({"role": "user", "content": "."})

    # JSON mode instruction for Claude (no native format:json like Ollama)
    if format_json:
        system = (system + "\n" if system else "") + (
            "Respond with ONLY a valid JSON object. "
            "No markdown, no explanation, no code fences. "
            "Your entire response must be parseable by json.loads()."
        )

    # Build system with prompt caching for repeated calls
    sys_block = [{"type": "text", "text": system or "You are a helpful assistant.",
                  "cache_control": {"type": "ephemeral"}}]

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": sys_block,
        "messages": messages,
    }

    url = f"{base_url}/v1/messages"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("anthropic-beta", "prompt-caching-2024-07-31")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8", errors="replace"))
        # Extract text
        content = ""
        for block in resp.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                content += block.get("text", "")
        if not content:
            content = resp.get("text", "")

        # Track usage (thread-safe)
        usage = resp.get("usage", {})
        tokens_used = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        with _engine_llm_lock:
            _engine_llm_daily_calls["count"] += 1
            _engine_llm_daily_calls["tokens"] += tokens_used

        content = content.strip()

        # 3-layer JSON extraction for Claude (no native format:json like Ollama)
        if format_json and content:
            content = _extract_json_robust(content)

        return content
    except Exception as e:
        logger.error(f"Engine Claude call failed ({model}): {e} — fallback to Ollama")
        return _call_ollama(prompt, system=system, max_tokens=max_tokens,
                            temperature=temperature, format_json=format_json,
                            timeout=timeout, think=think)


def _load_manifest_tools() -> list:
    """Load tool list from C++ tier0 manifest."""
    if not MANIFEST_PATH.exists():
        return []
    try:
        with open(MANIFEST_PATH, "r") as f:
            m = json.load(f)
        return [t.get("aid", "") for t in m.get("tools", []) if t.get("aid")]
    except Exception as e:
        logger.warning(f"Manifest load failed ({MANIFEST_PATH}): {type(e).__name__}: {e}")
        return []


def _load_manifest_tools_full() -> list:
    """Load full tool info from manifest: aid, name, description, inputs_schema."""
    if not MANIFEST_PATH.exists():
        return []
    try:
        with open(MANIFEST_PATH, "r") as f:
            m = json.load(f)
        result = []
        for t in m.get("tools", []):
            aid = t.get("aid", "")
            if not aid:
                continue
            result.append({
                "aid": aid,
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "inputs": list((t.get("inputs_schema", {}).get("properties", {}) or {}).keys()),
                "required": t.get("inputs_schema", {}).get("required", []),
                "side_effects": t.get("side_effects", []),
            })
        return result
    except Exception as e:
        logger.warning(f"Manifest full load failed ({MANIFEST_PATH}): {type(e).__name__}: {e}")
        return []
