#!/usr/bin/env python3
"""Machina Tool Dispatch — facade + dynamic registration + toolhost + skill recording.

Static registry data lives in machina_dispatch_registry.py.
Execution logic lives in machina_dispatch_exec.py.
This module: MCP bridge, toolhost subprocess, skill recording, facade re-exports.
"""

import hashlib
import json
import logging
import os
import subprocess
import threading
import time

from machina_shared import MACHINA_ROOT, MEM_DIR

# ---------------------------------------------------------------------------
# Re-export ALL registry symbols — every consumer imports from machina_dispatch.
# This facade pattern ensures zero breakage for existing code.
# ---------------------------------------------------------------------------
from machina_dispatch_registry import (  # noqa: F401
    # AID constants
    AID_UTIL_SAVE, AID_UTIL_RUN, AID_UTIL_LIST,
    AID_UTIL_DELETE, AID_UTIL_UPDATE,
    AID_CODE_EXEC, AID_NET_WEB_SEARCH,
    AID_FILE_LIST, AID_FILE_SEARCH, AID_FILE_DIFF,
    AID_FILE_EDIT, AID_FILE_APPEND, AID_FILE_DELETE,
    AID_PROJECT_CREATE, AID_PROJECT_BUILD,
    AID_PIP_INSTALL, AID_PIP_UNINSTALL, AID_PIP_LIST,
    PYTHON_AIDS,
    # Functions
    validate_aid, resolve_alias,
    filter_tools_for_intent, get_error_hint,
    normalize_function_call,
    # Data structures (mutable — MCP bridge merges into these)
    TOOL_ALIASES, TOOL_DESCRIPTIONS,
    INTENT_TOOL_MAP, CHAIN_RECIPES, ERROR_HINTS,
)

logger = logging.getLogger(__name__)

__all__ = [
    "validate_aid", "resolve_alias", "run_machina_toolhost",
    "execute_chain", "filter_tools_for_intent",
    "get_error_hint", "normalize_function_call",
]

# ---------------------------------------------------------------------------
# MCP Bridge — dynamic tool discovery from MCP servers
# ---------------------------------------------------------------------------
_mcp_registered = False
_mcp_lock = threading.Lock()


def _is_mcp_aid(aid: str) -> bool:
    """Check if an AID belongs to an MCP tool."""
    return aid.startswith("AID.MCP.")


async def register_mcp_tools(force: bool = False):
    """Connect to MCP servers and register discovered tools.

    Called at bot startup (async context required).
    Populates TOOL_ALIASES, TOOL_DESCRIPTIONS, and permission defaults.
    Uses atomic swap: build new dicts, then merge under lock.
    Use force=True to re-register after MCP reload.
    """
    global _mcp_registered
    if _mcp_registered and not force:
        return
    try:
        from machina_mcp import mcp_manager
        if not mcp_manager.is_started:
            await mcp_manager.start()
        if mcp_manager.tool_count == 0:
            logger.info("MCP: no tools discovered")
            return

        # Build new entries first (no mutation yet)
        new_aliases = mcp_manager.get_aliases()
        new_descriptions = mcp_manager.get_descriptions()
        new_permissions = mcp_manager.get_permissions()

        # Atomic merge under lock
        with _mcp_lock:
            # Clean old MCP entries
            for key in list(TOOL_ALIASES.keys()):
                if key.startswith("mcp_"):
                    del TOOL_ALIASES[key]
            for key in list(TOOL_DESCRIPTIONS.keys()):
                if key.startswith("AID.MCP."):
                    del TOOL_DESCRIPTIONS[key]
            # Merge new entries
            TOOL_ALIASES.update(new_aliases)
            TOOL_DESCRIPTIONS.update(new_descriptions)

        # Permissions use their own module — merge outside our lock
        from machina_permissions import DEFAULT_PERMISSIONS
        DEFAULT_PERMISSIONS.update(new_permissions)

        _mcp_registered = True
        logger.info(f"MCP: registered {mcp_manager.tool_count} tools into AID dispatch")
    except Exception as e:
        logger.error(f"MCP registration failed: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Chain Execution — uses run_machina_tool (forward ref resolved at bottom)
# ---------------------------------------------------------------------------
def execute_chain(chain_name: str, inputs: dict) -> list:
    """Execute a multi-step chain, returning all step results."""
    recipe = CHAIN_RECIPES.get(chain_name)
    if not recipe:
        return [{"error": f"unknown chain: {chain_name}"}]
    results = []
    for aid, input_fn in recipe:
        step_inputs = input_fn(inputs)
        result = run_machina_tool(aid, step_inputs)
        results.append({"tool": aid, "output": result})
        if (isinstance(result, dict) and result.get("error")) or \
           (isinstance(result, str) and result.lower().startswith("error")):
            hint = get_error_hint(str(result))
            if hint:
                results[-1]["hint"] = hint
            break  # Stop chain on error
    return results


# ---------------------------------------------------------------------------
# Toolhost Subprocess — C++ tool execution
# ---------------------------------------------------------------------------
_TOOLHOST_MAX_OUTPUT = 1_048_576  # 1MB response size limit
_TOOLHOST_TIMEOUT = int(os.environ.get("MACHINA_TOOLHOST_TIMEOUT", "90"))


def _toolhost_error(aid: str, error_type: str, detail: str) -> dict:
    """Build structured error dict for toolhost failures."""
    return {"error": True, "aid": aid, "type": error_type, "detail": detail}


def run_machina_toolhost(aid: str, inputs: dict):
    """Run a tool via machina_toolhost subprocess.

    Returns str on success, dict on error (structured error with keys:
    error, aid, type, detail).
    """
    cli_path = os.path.join(MACHINA_ROOT, "build", "machina_cli")
    if not os.path.exists(cli_path):
        return _toolhost_error(aid, "not_found",
                               f"machina_cli not found at {cli_path}")

    req = json.dumps(
        {
            "input_json": json.dumps(inputs, ensure_ascii=False),
            "ds_state": {"slots": {}},
        },
        ensure_ascii=False,
    )
    try:
        result = subprocess.run(
            [cli_path, "tool_exec", aid],
            input=req + "\n",
            capture_output=True, text=True,
            timeout=_TOOLHOST_TIMEOUT,
            cwd=MACHINA_ROOT,
        )
        output = result.stdout if result.stdout else ""
        if len(output) > 524_288:  # 512KB early warning
            logger.warning(f"[Toolhost] Large output: {len(output)//1024}KB for {aid}")
        if len(output) > _TOOLHOST_MAX_OUTPUT:
            logger.warning(f"toolhost [{aid}]: output truncated {len(output)} -> {_TOOLHOST_MAX_OUTPUT}")
            output = output[:_TOOLHOST_MAX_OUTPUT] + f"\n...(output truncated: exceeded 1MB limit)"
        if result.returncode != 0:
            stderr_snippet = result.stderr if result.stderr else ""
            logger.error(f"toolhost [{aid}] rc={result.returncode}: {stderr_snippet}")
            detail = stderr_snippet or f"rc={result.returncode}"
            if output.strip():
                detail = f"{output.strip()}\n{detail}"
            return _toolhost_error(aid, "crash", detail)
        if not output.strip():
            return _toolhost_error(aid, "empty_output", "tool_exec returned empty output")
        try:
            payload = json.loads(output.strip().split("\n")[0])
        except json.JSONDecodeError as je:
            raw_preview = output[:200].replace("\n", "\\n")
            logger.warning(f"toolhost [{aid}]: malformed JSON response: {je}. Raw: {raw_preview}")
            return _toolhost_error(aid, "parse_error",
                                   f"malformed JSON: {je}. Raw: {raw_preview}")
        status = str(payload.get("status", ""))
        err = str(payload.get("error", "") or "")
        out_json = payload.get("output_json", "")
        if status and status != "OK":
            detail = err or f"tool_exec status={status}"
            return _toolhost_error(aid, "tool_error", detail)
        return out_json if isinstance(out_json, str) else json.dumps(out_json, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return _toolhost_error(aid, "timeout",
                               f"toolhost timed out ({_TOOLHOST_TIMEOUT}s)")
    except Exception as e:
        return _toolhost_error(aid, "exception",
                               f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Auto Skill Recording — quality gate + dedup
# ---------------------------------------------------------------------------
_ERROR_MARKERS = ("error", "traceback", "failed", "exception", "fault", "errno")

_skill_hash_cache: set = set()
_skill_hash_cache_ts: float = 0.0
_skill_cache_lock = threading.Lock()


def _refresh_skill_hash_cache():
    """Load code hashes from skills.jsonl into memory cache (max every 60s)."""
    global _skill_hash_cache, _skill_hash_cache_ts
    now = time.time()
    with _skill_cache_lock:
        if now - _skill_hash_cache_ts < 60:
            return
        try:
            from machina_shared import SKILLS_STREAM, _jsonl_read
            skills_file = MEM_DIR / f"{SKILLS_STREAM}.jsonl"
            if not skills_file.exists():
                _skill_hash_cache_ts = now
                return
            entries = _jsonl_read(skills_file, max_lines=200)
            _skill_hash_cache.clear()
            for e in entries:
                code = e.get("code", "")
                if code:
                    _skill_hash_cache.add(hashlib.sha256(code.encode()).hexdigest())
            _skill_hash_cache_ts = now
        except Exception as e:
            logger.debug(f"Skill hash cache refresh error: {e}")


def _should_record_skill(code: str, result: str) -> bool:
    """Quality gate for auto skill recording."""
    if not code or not result:
        return False
    if code.count("\n") < 2:
        return False
    result_lower = result.lower()[:500]
    for marker in _ERROR_MARKERS:
        if marker in result_lower:
            return False
    _refresh_skill_hash_cache()
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    with _skill_cache_lock:
        if code_hash in _skill_hash_cache:
            logger.debug(f"[Skill] Dedup: code already recorded (hash={code_hash[:12]})")
            return False
        _skill_hash_cache.add(code_hash)
    return True


# ---------------------------------------------------------------------------
# Facade re-exports — execution layer (MUST be at bottom)
# ---------------------------------------------------------------------------
from machina_dispatch_exec import run_machina_tool, execute_intent  # noqa: E402,F401
