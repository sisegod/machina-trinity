"""Machina Chat LLM Layer — Ollama, Anthropic, and OAI-compatible API calls.

Anthropic: Messages API with field sanitization (role+content only, strips _ts etc).
           Uses ANTHROPIC_BASE_URL env for custom endpoints.
Ollama:    Native /api/chat with format:json (GBNF constrained decoding).
           Qwen3 think:false to disable reasoning in JSON mode.
OAI:       Standard /v1/chat/completions for generic backends.
"""

import json
import logging
import os
import re
import urllib.request

logger = logging.getLogger(__name__)


def _call_ollama_json(system: str, messages: list, model: str = None,
                      num_predict: int = 4096) -> dict:
    """Ollama Native API with format:json — guaranteed valid JSON output."""
    base = os.getenv("OAI_COMPAT_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = model or os.getenv("OAI_COMPAT_MODEL", "qwen3:14b-q8_0")
    timeout = float(os.getenv("OAI_COMPAT_TIMEOUT_SEC", "60"))

    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "think": False,  # Qwen3: disable thinking for JSON output
        "options": {
            "temperature": 0.0,
            "num_predict": num_predict,
            "repeat_penalty": 1.1,
        },
        "messages": [{"role": "system", "content": system}] + messages,
    }

    url = base + "/api/chat"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8", errors="replace"))

    msg = resp.get("message", {})
    content = msg.get("content", "")
    # Qwen3 thinking fallback: content may be empty with thinking field populated
    if not content and msg.get("thinking"):
        content = msg["thinking"]
    if not content:
        content = "{}"
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        # Malformed JSON from LLM — try extracting with fallback
        extracted = _extract_json_from_text(content)
        try:
            return json.loads(extracted)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Ollama format:json malformed, falling back: {content[:200]}")
            return {"type": "error", "content": content}


def _call_ollama_text(system: str, messages: list, model: str = None) -> str:
    """Ollama Native API — free text generation for conversation/summary."""
    base = os.getenv("OAI_COMPAT_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = model or os.getenv("OAI_COMPAT_MODEL", "qwen3:14b-q8_0")
    timeout = float(os.getenv("OAI_COMPAT_TIMEOUT_SEC", "60"))
    max_tokens = int(os.getenv("MACHINA_CHAT_MAX_TOKENS", "4096"))
    temperature = float(os.getenv("MACHINA_CHAT_TEMPERATURE", "0.7"))

    body = {
        "model": model,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "repeat_penalty": 1.1,
            "top_p": 0.9,
        },
        "messages": [{"role": "system", "content": system}] + messages,
    }

    url = base + "/api/chat"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8", errors="replace"))

    msg = resp.get("message", {})
    content = msg.get("content", "")
    # Qwen3 thinking fallback: content may be empty with thinking field populated
    if not content and msg.get("thinking"):
        content = msg["thinking"]
    return content


def _call_oai_compat_text(system: str, messages: list) -> str:
    """OpenAI-compatible API (vLLM, llama.cpp, etc.) — free text."""
    base = os.getenv("OAI_COMPAT_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("OAI_COMPAT_MODEL", "qwen3:14b-q8_0")
    api_key = os.getenv("OAI_COMPAT_API_KEY", "").strip()
    timeout = float(os.getenv("OAI_COMPAT_TIMEOUT_SEC", "60"))
    max_tokens = int(os.getenv("MACHINA_CHAT_MAX_TOKENS", "4096"))
    temperature = float(os.getenv("MACHINA_CHAT_TEMPERATURE", "0.7"))

    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages,
    }

    url = base + "/v1/chat/completions"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8", errors="replace"))

    try:
        choices = resp.get("choices", [])
        if not choices:
            return str(resp.get("error", {}).get("message", "(empty choices)"))
        return choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        return f"(API response error: {e})"


def _call_anthropic(system: str, messages: list, temperature: float = None) -> str:
    """Claude API call.

    Args:
        temperature: Override temp (None = use env default 0.7).
                     Pass 0.0 for deterministic JSON output.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
    # Claude supports much larger output — use ANTHROPIC_MAX_TOKENS or generous default
    max_tokens = int(os.getenv("ANTHROPIC_MAX_TOKENS",
                     os.getenv("MACHINA_CHAT_MAX_TOKENS", "4096")))
    if temperature is None:
        temperature = float(os.getenv("MACHINA_CHAT_TEMPERATURE", "0.7"))

    # H10: Warn if ANTHROPIC_BASE_URL overrides to non-HTTPS (API key plaintext risk)
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    if not base_url.startswith("https://") and not base_url.startswith("http://127.") and not base_url.startswith("http://localhost"):
        logger.warning("ANTHROPIC_BASE_URL is not HTTPS — API key may be transmitted in plaintext")

    # Anthropic requires alternating user/assistant, starting with user
    # Strip extra fields (_ts, etc.) — API rejects unknown keys
    clean = [{"role": m["role"], "content": str(m.get("content", ""))}
             for m in messages if m.get("role") in ("user", "assistant")]
    merged = []
    for m in clean:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1]["content"] += "\n" + m["content"]
        else:
            merged.append(m)
    if not merged or merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": "."})

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": [{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
        "messages": merged,
    }

    url = f"{base_url}/v1/messages"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    req.add_header("anthropic-beta", "prompt-caching-2024-07-31")

    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.loads(r.read().decode("utf-8", errors="replace"))

    # Extract text from content blocks
    try:
        text = resp["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        err_msg = resp.get("error", {}).get("message", "")
        if err_msg:
            raise RuntimeError(f"Anthropic API error: {err_msg}")
        logger.error(f"Anthropic unexpected response: {json.dumps(resp, ensure_ascii=False)[:300]}")
        raise RuntimeError(f"Anthropic: unexpected response structure")

    if not text.strip():
        stop = resp.get("stop_reason", "unknown")
        logger.warning(f"Anthropic returned empty text (stop_reason={stop})")
        raise RuntimeError(f"Anthropic returned empty response (stop_reason={stop})")

    return text


def _is_ollama(base_url: str = None) -> bool:
    """Detect if we're talking to Ollama (supports /api/chat + format:json)."""
    base = (base_url or os.getenv("OAI_COMPAT_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
    return "11434" in base or "ollama" in base.lower()


def _extract_json_from_text(text: str) -> str:
    """Extract JSON from text that may be wrapped in markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1
        end = len(lines)
        for i in range(1, len(lines)):
            if lines[i].strip() == "```":
                end = i
                break
        text = "\n".join(lines[start:end]).strip()
    # Try to find JSON object in text using bracket counting (handles any depth)
    if not text.startswith("{"):
        start = text.find('{')
        if start >= 0:
            depth, end = 0, start
            for i in range(start, len(text)):
                if text[i] == '{': depth += 1
                elif text[i] == '}': depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            text = text[start:end]
    return text
