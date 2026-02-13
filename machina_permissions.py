"""Machina Permission Engine — 3-tier allow/ask/deny control for tool execution.

Modes (MACHINA_PERMISSION_MODE env var):
  open       — all tools auto-allowed (dev mode)
  standard   — safe=allow, dangerous=ask, configurable (default)
  locked     — read-only tools only, everything else denied
  supervised — all tools require approval

Per-tool overrides (MACHINA_PERMISSION_OVERRIDES env var):
  JSON object: {"AID.FILE.DELETE.v1": "allow", "AID.NET.HTTP_GET.v1": "deny"}

Session-level runtime grants (for "always allow" button):
  _session_grants set — bypasses ask for remainder of session.
"""

import json
import logging
import os
import threading
from pathlib import Path

from machina_config import MANIFEST_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission Levels
# ---------------------------------------------------------------------------
ALLOW = "allow"
ASK = "ask"
DENY = "deny"

# ---------------------------------------------------------------------------
# Default permission map — per-AID level for "standard" mode
# ---------------------------------------------------------------------------
DEFAULT_PERMISSIONS = {
    # --- Always allow (read-only / sandboxed / safe) ---
    "AID.FILE.READ.v1": ALLOW,
    "AID.FILE.LIST.v1": ALLOW,
    "AID.FILE.SEARCH.v1": ALLOW,
    "AID.FILE.DIFF.v1": ALLOW,
    "AID.MEMORY.QUERY.v1": ALLOW,
    "AID.MEMORY.APPEND.v1": ALLOW,
    "AID.UTIL.LIST.v1": ALLOW,
    "AID.UTIL.SAVE.v1": ALLOW,
    "AID.UTIL.RUN.v1": ALLOW,
    "AID.UTIL.DELETE.v1": ALLOW,
    "AID.UTIL.UPDATE.v1": ALLOW,
    "AID.CODE.EXEC.v1": ASK,          # code execution requires approval
    "AID.NET.WEB_SEARCH.v1": ALLOW,   # read-only search
    "AID.FILE.WRITE.v1": ALLOW,       # work/ only
    "AID.FILE.EDIT.v1": ALLOW,        # work/ only
    "AID.FILE.APPEND.v1": ALLOW,      # work/ only
    "AID.PROJECT.CREATE.v1": ALLOW,   # work/ only

    # --- Ask (destructive / network / system) ---
    "AID.FILE.DELETE.v1": ASK,         # destructive
    "AID.SHELL.EXEC.v1": ASK,         # arbitrary commands
    "AID.NET.HTTP_GET.v1": ASK,        # network access
    "AID.GENESIS.COMPILE_SHARED.v1": ASK,
    "AID.GENESIS.LOAD_PLUGIN.v1": ASK,
    "AID.PROJECT.BUILD.v1": ASK,
    "AID.SYSTEM.PIP_INSTALL.v1": ASK,  # package installation
    "AID.SYSTEM.PIP_UNINSTALL.v1": ASK,  # package removal
    "AID.SYSTEM.PIP_LIST.v1": ALLOW,    # read-only listing

    # --- Allow (genesis write is sandboxed) ---
    "AID.GENESIS.WRITE_FILE.v1": ALLOW,
}

# Read-only AIDs — allowed even in locked mode
_READONLY_AIDS = {
    "AID.FILE.READ.v1", "AID.FILE.LIST.v1", "AID.FILE.SEARCH.v1",
    "AID.FILE.DIFF.v1", "AID.MEMORY.QUERY.v1", "AID.UTIL.LIST.v1",
    "AID.SYSTEM.PIP_LIST.v1",
}

# Session-level runtime grants ("always allow" from Telegram)
_session_grants: set = set()
_grants_lock = threading.Lock()
_manifest_perm_cache = {"mtime": 0.0, "map": {}}


def _permission_from_side_effects(side_effects: set[str]) -> str:
    """Derive default permission from manifest side_effects."""
    if not side_effects or side_effects == {"none"}:
        return ALLOW
    # Read-only introspection is safe by default.
    safe_read_like = {"filesystem_read", "process_introspection", "gpu_probe"}
    if side_effects.issubset(safe_read_like):
        return ALLOW
    # Anything that can mutate state, spawn processes, load binaries, or use network
    # should require explicit approval.
    risky = {
        "filesystem_write",
        "filesystem_delete",
        "network_io",
        "proc_exec",
        "process_spawn",
        "dynamic_library_load",
    }
    if side_effects & risky:
        return ASK
    return ASK


def _load_manifest_permission_map() -> dict:
    """Load AID->permission map inferred from tool manifest side_effects."""
    try:
        path = Path(MANIFEST_PATH)
        if not path.exists():
            return {}
        mtime = path.stat().st_mtime
        if _manifest_perm_cache["map"] and _manifest_perm_cache["mtime"] == mtime:
            return _manifest_perm_cache["map"]
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pmap = {}
        for t in data.get("tools", []):
            aid = t.get("aid", "")
            if not aid:
                continue
            side = set(t.get("side_effects", []))
            pmap[aid] = _permission_from_side_effects(side)
        _manifest_perm_cache["mtime"] = mtime
        _manifest_perm_cache["map"] = pmap
        return pmap
    except Exception as e:
        logger.debug(f"Manifest permission map load failed: {type(e).__name__}: {e}")
        return {}


def _load_overrides() -> dict:
    """Load per-tool permission overrides from env var."""
    raw = os.getenv("MACHINA_PERMISSION_OVERRIDES", "")
    if not raw:
        return {}
    try:
        overrides = json.loads(raw)
        if isinstance(overrides, dict):
            return {k: v for k, v in overrides.items()
                    if v in (ALLOW, ASK, DENY)}
    except json.JSONDecodeError:
        logger.warning(f"Invalid MACHINA_PERMISSION_OVERRIDES JSON: {raw[:100]}")
    return {}


def get_mode() -> str:
    """Get current permission mode."""
    return os.getenv("MACHINA_PERMISSION_MODE", "standard")


def check_permission(aid: str) -> str:
    """Check permission level for a tool AID.

    Returns: "allow", "ask", or "deny".
    """
    mode = get_mode()

    # Mode overrides
    if mode == "open":
        return ALLOW
    if mode == "locked":
        return ALLOW if aid in _READONLY_AIDS else DENY
    if mode == "supervised":
        return ALLOW if aid in _READONLY_AIDS else ASK

    # Standard mode: check session grants → overrides → defaults
    with _grants_lock:
        if aid in _session_grants:
            return ALLOW

    overrides = _load_overrides()
    if aid in overrides:
        return overrides[aid]

    if aid in DEFAULT_PERMISSIONS:
        return DEFAULT_PERMISSIONS[aid]

    # Fallback: infer from C++ manifest side_effects for tools not explicitly mapped.
    manifest_map = _load_manifest_permission_map()
    if aid in manifest_map:
        return manifest_map[aid]

    return ASK  # unknown tools default to ASK


def grant_session(aid: str):
    """Grant session-level permission (from 'always allow' button)."""
    with _grants_lock:
        _session_grants.add(aid)
    logger.info(f"Session grant: {aid}")


def revoke_session(aid: str):
    """Revoke a session-level grant."""
    with _grants_lock:
        _session_grants.discard(aid)


def clear_session_grants():
    """Clear all session grants (on bot restart / user /clear)."""
    with _grants_lock:
        _session_grants.clear()


def get_permission_summary() -> str:
    """Return human-readable permission summary for /status command."""
    mode = get_mode()
    lines = [f"권한 모드: {mode}"]
    with _grants_lock:
        if _session_grants:
            lines.append(f"세션 허용: {', '.join(sorted(_session_grants))}")
    overrides = _load_overrides()
    if overrides:
        lines.append(f"오버라이드: {json.dumps(overrides, ensure_ascii=False)}")
    return "\n".join(lines)


def format_approval_message(aid: str, inputs: dict) -> str:
    """Format a human-readable approval request message."""
    # Tool-specific formatting
    desc_map = {
        "AID.FILE.DELETE.v1": ("🗑️ 파일 삭제", lambda i: i.get("path", "?")),
        "AID.SHELL.EXEC.v1": ("⚡ 셸 명령", lambda i: i.get("cmd", "?")[:200]),
        "AID.NET.HTTP_GET.v1": ("🌐 네트워크", lambda i: i.get("url", "?")),
        "AID.GENESIS.COMPILE_SHARED.v1": ("🔨 컴파일", lambda i: i.get("src_relative_path", "?")),
        "AID.GENESIS.LOAD_PLUGIN.v1": ("📦 플러그인 로드", lambda i: i.get("plugin_relative_path", "최신")),
        "AID.PROJECT.BUILD.v1": ("🏗️ 프로젝트 빌드", lambda i: i.get("name", "?")),
        "AID.SYSTEM.PIP_INSTALL.v1": ("📦 패키지 설치", lambda i: ", ".join(i.get("packages", []))),
    }

    if aid in desc_map:
        label, detail_fn = desc_map[aid]
        detail = detail_fn(inputs)
        return f"{label} 요청:\n`{detail}`\n\n허용하시겠습니까?"

    return f"도구 실행 요청: `{aid}`\n허용하시겠습니까?"
