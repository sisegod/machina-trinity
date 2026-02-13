#!/usr/bin/env python3
"""Machina Runtime Config — single source of truth for model/backend/paths.

All runtime code uses get_active_model() / get_active_url() instead of
frozen module-level constants. Config state persists across restarts
via work/config_state.json.

Extracted from machina_shared.py to eliminate the God Module pattern.
"""

import fcntl
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("machina")

# ---------------------------------------------------------------------------
# Path Constants
# ---------------------------------------------------------------------------
MACHINA_ROOT = os.getenv("MACHINA_ROOT", os.path.dirname(os.path.abspath(__file__)))

MEM_DIR = Path(MACHINA_ROOT) / "work" / "memory"
CHAT_LOG_DIR = Path(MACHINA_ROOT) / "work" / "memory"
CHAT_LOG_FILE = CHAT_LOG_DIR / "telegram_chat.jsonl"

EXPERIENCE_STREAM = "experiences"
INSIGHTS_STREAM = "insights"
SKILLS_STREAM = "skills"
ENTITIES_STREAM = "entities"
RELATIONS_STREAM = "relations"

UTILS_DIR = os.path.join(MACHINA_ROOT, "work", "scripts", "utils")
UTILS_MANIFEST = os.path.join(UTILS_DIR, "manifest.json")
MANIFEST_PATH = Path(MACHINA_ROOT) / "toolpacks" / "tier0" / "manifest.json"

# ---------------------------------------------------------------------------
# Runtime Config State — persisted to disk, always read from os.environ
# ---------------------------------------------------------------------------
_CONFIG_STATE_FILE = Path(MACHINA_ROOT) / "work" / "config_state.json"
_CONFIG_KEYS = [
    "MACHINA_CHAT_BACKEND", "OAI_COMPAT_MODEL", "OAI_COMPAT_BASE_URL",
    "OAI_COMPAT_API_KEY", "ANTHROPIC_MODEL", "MACHINA_CHAT_TEMPERATURE",
    "MACHINA_CHAT_MAX_TOKENS", "MACHINA_AUTO_ROUTE",
]


def load_runtime_config():
    """Load persisted config state into os.environ (startup only)."""
    if not _CONFIG_STATE_FILE.exists():
        return
    try:
        with open(_CONFIG_STATE_FILE, "r") as f:
            state = json.load(f)
        for k in _CONFIG_KEYS:
            if k in state and state[k]:
                os.environ[k] = str(state[k])
        logger.info(f"Runtime config loaded: backend={os.getenv('MACHINA_CHAT_BACKEND','?')}, model={os.getenv('OAI_COMPAT_MODEL','?')}")
    except Exception as e:
        logger.warning(f"Failed to load runtime config: {e}")


def save_runtime_config():
    """Persist current env config to disk (survives restart)."""
    import time as _time_mod
    state = {k: os.getenv(k, "") for k in _CONFIG_KEYS}
    state["_saved_at"] = int(_time_mod.time())
    try:
        _CONFIG_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_STATE_FILE.touch(exist_ok=True)
        with open(_CONFIG_STATE_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                json.dump(state, f, indent=2)
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        logger.warning(f"Failed to save runtime config: {e}")


def get_active_model() -> str:
    """Get the currently active LLM model name (always fresh from env)."""
    backend = os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")
    if backend == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
    return os.getenv("OAI_COMPAT_MODEL", "qwen3:14b-q8_0")


def get_active_url() -> str:
    """Get the currently active LLM base URL (always fresh from env)."""
    return os.getenv("OAI_COMPAT_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def get_active_backend() -> str:
    """Get the currently active backend name (always fresh from env)."""
    return os.getenv("MACHINA_CHAT_BACKEND", "oai_compat")


def get_brain_label() -> str:
    """Human-readable label: 'Claude (opus)' or 'Ollama (qwen3:14b-q8_0)'."""
    backend = get_active_backend()
    if backend == "anthropic":
        return f"Claude ({os.getenv('ANTHROPIC_MODEL', 'claude-opus-4-6')})"
    return f"Ollama ({get_active_model()})"


def is_auto_route_enabled() -> bool:
    """Check if automatic multi-model routing is enabled."""
    return os.getenv("MACHINA_AUTO_ROUTE", "0") in ("1", "true", "yes")


def set_auto_route(enabled: bool):
    """Enable or disable automatic multi-model routing and persist."""
    os.environ["MACHINA_AUTO_ROUTE"] = "1" if enabled else "0"
    save_runtime_config()
