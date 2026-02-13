#!/usr/bin/env python3
"""Machina Tool Dispatch — execution layer.

Contains run_machina_tool() dispatcher and execute_intent() action runner.
Split from machina_dispatch.py for maintainability.
"""
import asyncio, json, logging, os, re as _re, shlex, subprocess, time
from pathlib import Path

from machina_shared import _jsonl_append, MACHINA_ROOT, MEM_DIR, sandboxed_run
from machina_tools import (
    run_machina_goal, run_machina_http_get, execute_code, web_search,
    util_save, util_run, util_list, util_delete, util_update,
    file_list, file_search, file_diff, file_edit, file_append, file_delete,
    project_create, project_build, pip_install, pip_uninstall, pip_list,
)
from machina_permissions import check_permission, ALLOW, ASK, DENY
from machina_autonomic._autoapprove import is_autonomic_auto_approved_aid
from machina_dispatch import (
    AID_UTIL_SAVE, AID_UTIL_RUN, AID_UTIL_LIST,
    AID_UTIL_DELETE, AID_UTIL_UPDATE,
    AID_CODE_EXEC, AID_NET_WEB_SEARCH,
    AID_FILE_LIST, AID_FILE_SEARCH, AID_FILE_DIFF,
    AID_FILE_EDIT, AID_FILE_APPEND, AID_FILE_DELETE,
    AID_PROJECT_CREATE, AID_PROJECT_BUILD,
    AID_PIP_INSTALL, AID_PIP_UNINSTALL, AID_PIP_LIST,
    resolve_alias, validate_aid, get_error_hint, execute_chain,
    run_machina_toolhost, _toolhost_error, _is_mcp_aid, _should_record_skill,
)

logger = logging.getLogger(__name__)


def _autonomic_approve_all_ask() -> bool:
    """Auto-accept ASK tools for non-interactive autonomic loops when enabled."""
    return os.getenv("MACHINA_AUTONOMIC_APPROVE_ALL_ASK", "1").lower() in ("1", "true", "yes", "on")


def run_machina_tool(aid: str, inputs: dict, force_code: bool = False,
                     allow_net: bool = False, _caller_approved: bool = False) -> str:
    """Execute a single Machina tool by AID -- dispatches to appropriate handler.

    ALL tools (C++ and Python-only) are routed through this function.
    Python-only AIDs: AID.UTIL.*, AID.CODE.EXEC, AID.NET.WEB_SEARCH.

    _caller_approved: Set True when the caller (telegram_bot) has already
    handled ASK permission approval. When False (e.g., autonomic engine),
    ASK-level tools are blocked to prevent unattended execution.
    """
    aid = resolve_alias(aid)  # (2a) resolve short aliases

    # Permission check (sync — ASK handled by caller, e.g. telegram_bot.py)
    perm = check_permission(aid)
    if perm == DENY:
        return f"error: permission denied for {aid} (mode={__import__('machina_permissions').get_mode()})"
    # ASK-level tools: block if caller hasn't explicitly approved.
    # telegram_bot.py pre-checks and sets _caller_approved=True.
    # Autonomic engine and other non-interactive callers get blocked here.
    if perm == ASK and not _caller_approved and not _autonomic_approve_all_ask() and not is_autonomic_auto_approved_aid(aid):
        return f"error: {aid} requires approval (ASK permission) — not available in autonomous mode"

    try:
        # --- Python-only AID handlers ---

        # AID.UTIL.SAVE.v1
        if aid == AID_UTIL_SAVE:
            name = inputs.get("name", "unnamed")
            lang = inputs.get("lang", "python")
            code = inputs.get("code", "")
            desc = inputs.get("description", "")
            if not code:
                return "error: no code provided"
            return util_save(name, lang, code, desc)

        # AID.UTIL.RUN.v1
        if aid == AID_UTIL_RUN:
            name = inputs.get("name", "")
            args = inputs.get("args", "")
            if not name:
                return "error: no utility name"
            return util_run(name, args)

        # AID.UTIL.LIST.v1
        if aid == AID_UTIL_LIST:
            return util_list()

        # AID.UTIL.DELETE.v1
        if aid == AID_UTIL_DELETE:
            name = inputs.get("name", "")
            if not name:
                return "error: no utility name"
            return util_delete(name)

        # AID.UTIL.UPDATE.v1
        if aid == AID_UTIL_UPDATE:
            name = inputs.get("name", "")
            code = inputs.get("code", "")
            desc = inputs.get("description", "")
            if not name:
                return "error: no utility name"
            return util_update(name, code, desc)

        # AID.CODE.EXEC.v1
        if aid == AID_CODE_EXEC:
            lang = inputs.get("lang", "python")
            code = inputs.get("code", "")
            if not code:
                return "error: no code provided"
            return execute_code(lang, code, force=force_code, allow_net=allow_net)

        # AID.NET.WEB_SEARCH.v1
        if aid == AID_NET_WEB_SEARCH:
            query = inputs.get("query", "")
            if not query:
                return "error: no search query"
            return web_search(query)

        # --- Python file tool handlers ---

        # AID.FILE.LIST.v1
        if aid == AID_FILE_LIST:
            path = inputs.get("path", ".")
            try:
                max_items = int(inputs.get("max_items", 100))
            except (ValueError, TypeError):
                max_items = 100
            return file_list(path, max_items)

        # AID.FILE.SEARCH.v1
        if aid == AID_FILE_SEARCH:
            root = inputs.get("root", ".")
            pattern = inputs.get("pattern", "")
            if not pattern:
                return "error: no search pattern"
            ext_filter = inputs.get("ext_filter", "")
            max_results = int(inputs.get("max_results", 50))
            return file_search(root, pattern, ext_filter, max_results)

        # AID.FILE.DIFF.v1
        if aid == AID_FILE_DIFF:
            path1 = inputs.get("path1", "")
            path2 = inputs.get("path2", "")
            if not path1 or not path2:
                return "error: need both path1 and path2"
            context = int(inputs.get("context", 3))
            return file_diff(path1, path2, context)

        # AID.FILE.EDIT.v1
        if aid == AID_FILE_EDIT:
            path = inputs.get("path", "")
            operation = inputs.get("operation", "")
            line = int(inputs.get("line", 0))
            content = inputs.get("content", "")
            if not path or not operation:
                return "error: need path and operation (replace/insert/delete)"
            if line < 1:
                return "error: line must be >= 1"
            return file_edit(path, operation, line, content)

        # AID.FILE.APPEND.v1
        if aid == AID_FILE_APPEND:
            path = inputs.get("path", "")
            content = inputs.get("content", "")
            if not path or not content:
                return "error: need path and content"
            return file_append(path, content)

        # AID.FILE.DELETE.v1
        if aid == AID_FILE_DELETE:
            path = inputs.get("path", "")
            if not path:
                return "error: need path"
            recursive = inputs.get("recursive", False)
            return file_delete(path, recursive)

        # AID.PROJECT.CREATE.v1
        if aid == AID_PROJECT_CREATE:
            name = inputs.get("name", "")
            lang = inputs.get("lang", "cpp")
            files = inputs.get("files", [])
            if not name or not files:
                return "error: need name and files list"
            return project_create(name, lang, files)

        # AID.PROJECT.BUILD.v1
        if aid == AID_PROJECT_BUILD:
            name = inputs.get("name", "")
            lang = inputs.get("lang", "cpp")
            build_type = inputs.get("build_type", "shared")
            if not name:
                return "error: need project name"
            return project_build(name, lang, build_type)

        # AID.SYSTEM.PIP_INSTALL.v1
        if aid == AID_PIP_INSTALL:
            packages = inputs.get("packages", [])
            if isinstance(packages, str):
                packages = [p.strip() for p in packages.split(",") if p.strip()]
            venv_name = inputs.get("venv_name", "default")
            if not packages:
                return "error: no packages specified"
            return pip_install(packages, venv_name)

        # AID.SYSTEM.PIP_UNINSTALL.v1
        if aid == AID_PIP_UNINSTALL:
            packages = inputs.get("packages", [])
            if isinstance(packages, str):
                packages = [p.strip() for p in packages.split(",") if p.strip()]
            venv_name = inputs.get("venv_name", "default")
            if not packages:
                return "error: no packages specified"
            return pip_uninstall(packages, venv_name)

        # AID.SYSTEM.PIP_LIST.v1
        if aid == AID_PIP_LIST:
            venv_name = inputs.get("venv_name", "default")
            return pip_list(venv_name)

        # --- MCP tool handlers (AID.MCP.*) ---
        if _is_mcp_aid(aid):
            from machina_mcp import mcp_manager
            if not mcp_manager.is_started:
                return "error: MCP not started. No MCP servers connected."
            # call_by_aid is async. This function runs in a worker thread
            # (via asyncio.to_thread), so we schedule the coroutine on the
            # main event loop where MCP sessions live.
            coro = mcp_manager.call_by_aid(aid, inputs)
            main_loop = mcp_manager._loop if hasattr(mcp_manager, '_loop') else None
            if main_loop and main_loop.is_running():
                future = asyncio.run_coroutine_threadsafe(coro, main_loop)
                return future.result(timeout=120)
            else:
                # Fallback: safe handling when event loop may already be running
                try:
                    loop = asyncio.get_running_loop()
                    future = asyncio.run_coroutine_threadsafe(coro, loop)
                    return future.result(timeout=120)
                except RuntimeError:
                    # No running loop — safe to create one
                    return asyncio.run(coro)

        # --- C++ toolhost AID handlers ---

        # HTTP_GET -> curl (exact AID match)
        if aid == "AID.NET.HTTP_GET.v1" and "url" in inputs:
            return run_machina_http_get(inputs["url"])

        # SHELL.EXEC -> subprocess (exact AID match)
        # SECURITY BOUNDARY: This tool intentionally executes LLM-chosen shell
        # commands. Isolation is enforced externally via seccomp sandbox + rlimits
        # when MACHINA_TOOLHOST_ISOLATE=1. The timeout and output truncation below
        # are defense-in-depth only; do NOT rely on them as the sole security layer.
        if aid == "AID.SHELL.EXEC.v1":
            cmd = inputs.get("cmd", "")
            if isinstance(cmd, list):
                # shlex.quote each element to prevent shell injection
                cmd = " ".join(shlex.quote(str(c)) for c in cmd)
            if not cmd:
                return "error: no command provided"
            timeout_ms = int(inputs.get("timeout_ms", 10000))
            timeout_s = max(3, min(timeout_ms // 1000, 30))
            result = sandboxed_run(
                ["bash", "-c", cmd],
                timeout=timeout_s,
                cwd=MACHINA_ROOT,
                writable_dirs=[os.path.join(MACHINA_ROOT, "work")],
            )
            output = result.stdout
            if result.returncode != 0 and result.stderr:
                output += f"\n[stderr] {result.stderr}"
            return output if output else "(no output)"

        # FILE.READ -> read file (path sandboxed to MACHINA_ROOT) (exact AID match)
        if aid == "AID.FILE.READ.v1":
            path = inputs.get("path", "")
            if not path:
                return "error: no path"
            max_bytes = int(inputs.get("max_bytes", 8192))
            if not os.path.isabs(path):
                path = os.path.join(MACHINA_ROOT, path)
            real_path = os.path.realpath(path)
            real_root = os.path.realpath(MACHINA_ROOT)
            if not real_path.startswith(real_root + os.sep) and real_path != real_root:
                # SECURITY: /proc/ whitelist — only safe informational files.
                # Block /proc/self/environ, /proc/self/maps, /proc/self/cmdline
                # which can leak secrets, memory layout, or command-line args.
                PROC_WHITELIST = (
                    "/proc/loadavg", "/proc/meminfo", "/proc/cpuinfo",
                    "/proc/uptime", "/proc/version",
                )
                PROC_BLOCKED = (
                    "/proc/self/environ", "/proc/self/maps", "/proc/self/cmdline",
                    "/proc/self/mem", "/proc/self/fd", "/proc/self/exe",
                    "/proc/self/root", "/proc/self/cwd", "/proc/self/io",
                    "/proc/self/stack", "/proc/self/status", "/proc/self/mountinfo",
                )
                if real_path.startswith("/proc/"):
                    # Block sensitive per-PID paths: /proc/<pid>/environ, cmdline, maps, mem, fd, etc.
                    _SENSITIVE_PROC_RE = r'/proc/\d+/(environ|cmdline|maps|mem|fd|exe|root|cwd|io|stack|status|mountinfo)'
                    if _re.match(_SENSITIVE_PROC_RE, real_path):
                        return f"error: blocked sensitive /proc path: {real_path}"
                    if any(real_path == p or real_path.startswith(p + "/") for p in PROC_BLOCKED):
                        return f"error: blocked sensitive /proc path: {real_path}"
                    if not any(real_path == p or real_path.startswith(p) for p in PROC_WHITELIST):
                        return f"error: /proc path not in whitelist: {real_path}"
                SAFE_SYSTEM_PREFIXES = ("/etc/hostname", "/proc/", "/sys/class/")
                if not any(real_path.startswith(p) for p in SAFE_SYSTEM_PREFIXES):
                    return f"error: path outside sandbox ({real_path} not under {real_root})"
            try:
                with open(real_path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read(max_bytes)
            except Exception as e:
                return f"file read error: {e}"

        # FILE.WRITE -> write file (sandboxed to MACHINA_ROOT/work) (exact AID match)
        if aid == "AID.FILE.WRITE.v1":
            path = inputs.get("path", "")
            content = inputs.get("content", "")
            if not path:
                return "error: no path"
            if len(content) > 1_048_576:
                return "error: content exceeds 1MB limit"
            try:
                safe_dir = os.path.realpath(os.path.join(MACHINA_ROOT, "work"))
                if os.path.isabs(path):
                    path = os.path.basename(path)
                if not path.startswith("work/") and not path.startswith("work\\"):
                    path = os.path.join("work", path)
                full_path = os.path.realpath(os.path.join(MACHINA_ROOT, path))
            except (OSError, ValueError) as e:
                return f"error: invalid path: {e}"
            if not full_path.startswith(safe_dir + os.sep) and full_path != safe_dir:
                return f"error: write path escapes sandbox ({full_path} not under {safe_dir})"
            overwrite = inputs.get("overwrite", True)
            if not overwrite and os.path.exists(full_path):
                return f"error: file exists and overwrite=false: {full_path}"
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            # Atomic write: tmp -> fsync -> rename(.bak) -> rename(final)
            tmp_path = full_path + ".tmp"
            try:
                fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                if os.path.exists(full_path):
                    try: os.replace(full_path, full_path + ".bak")
                    except OSError: pass
                os.rename(tmp_path, full_path)
            except Exception:
                if os.path.exists(tmp_path):
                    try: os.unlink(tmp_path)
                    except OSError: pass
                raise
            return f"wrote {len(content)} bytes to {full_path}"

        # MEMORY.APPEND -> save to JSONL (exact AID match)
        if aid == "AID.MEMORY.APPEND.v1":
            stream = inputs.get("stream", "telegram")
            text = inputs.get("text", "")
            event = inputs.get("event", "user_note")
            if not text:
                return "error: no text to save"
            mem_dir = Path(MACHINA_ROOT) / "work" / "memory"
            mem_dir.mkdir(parents=True, exist_ok=True)
            ts_ms = int(time.time() * 1000)
            entry = {
                "ts_ms": ts_ms,
                "stream": stream,
                "event": event,
                "text": text,
            }
            mem_file = mem_dir / f"{stream}.jsonl"
            _jsonl_append(mem_file, entry)
            return f"saved to memory ({stream}): {text[:100]}"

        # MEMORY.QUERY / MEMORY.SEARCH -> C++ hybrid with Python fallback + graph enrichment
        if aid in ("AID.MEMORY.QUERY.v1", "AID.MEMORY.SEARCH.v1"):
            from machina_learning import memory_search_recent, _cpp_hybrid_memory_search
            stream = inputs.get("stream", "telegram")
            query = inputs.get("query", "")
            top_k = int(inputs.get("top_k", 5))
            cpp_result = _cpp_hybrid_memory_search(query, stream, top_k)
            text_result = cpp_result if cpp_result else memory_search_recent(
                query, stream=stream, limit=top_k)
            # Enrich with Graph Memory context
            try:
                from machina_graph import graph_query
                graph_ctx = graph_query(query, limit=top_k)
                if graph_ctx and text_result:
                    text_result = text_result + "\n" + graph_ctx
                elif graph_ctx:
                    text_result = graph_ctx
            except Exception as e:
                logger.debug(f"{type(e).__name__}: {e}")
                pass
            return text_result if text_result else ""

        # GENESIS.WRITE_FILE -> write source to genesis dir (exact AID match)
        if aid == "AID.GENESIS.WRITE_FILE.v1":
            rel = inputs.get("relative_path", "")
            content = inputs.get("content", "")
            if not rel or not content:
                return "error: missing relative_path or content"
            if ".." in rel:
                return "error: relative_path may not contain '..'"
            base = os.path.join(MACHINA_ROOT, "toolpacks", "runtime_genesis", "src")
            os.makedirs(base, exist_ok=True)
            dst = os.path.realpath(os.path.join(base, rel))
            real_base = os.path.realpath(base)
            if not dst.startswith(real_base + os.sep) and dst != real_base:
                return f"error: genesis path escapes sandbox ({dst} not under {real_base})"
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(content)
            return json.dumps({"ok": True, "written": dst, "bytes": len(content)})

        # GENESIS.COMPILE_SHARED -> compile C++ to .so (exact AID match)
        if aid == "AID.GENESIS.COMPILE_SHARED.v1":
            src_rel = inputs.get("src_relative_path", "")
            out_name = inputs.get("out_name", "")
            if not src_rel or not out_name:
                return "error: missing src_relative_path or out_name"
            src_dir = os.path.join(MACHINA_ROOT, "toolpacks", "runtime_genesis", "src")
            src_path = os.path.join(src_dir, src_rel)
            if not os.path.exists(src_path):
                return f"error: source not found: {src_path}"
            plugin_dir = os.path.join(MACHINA_ROOT, "toolpacks", "runtime_plugins")
            os.makedirs(plugin_dir, exist_ok=True)
            out_path = os.path.join(plugin_dir, f"{out_name}.so")
            include_dir = os.path.join(MACHINA_ROOT, "core", "include")
            cmd = [
                "g++", "-shared", "-fPIC", "-std=c++2a", "-O2",
                f"-I{include_dir}",
                "-o", out_path, src_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                return f"compile error:\n{result.stderr[:2000]}"
            return json.dumps({"ok": True, "plugin": out_path})

        # GENESIS.LOAD_PLUGIN -> try dlopen via toolhost (exact AID match)
        if aid == "AID.GENESIS.LOAD_PLUGIN.v1":
            plugin_rel = inputs.get("plugin_relative_path", "")
            plugin_dir = os.path.join(MACHINA_ROOT, "toolpacks", "runtime_plugins")
            if plugin_rel:
                plugin_path = os.path.join(plugin_dir, plugin_rel)
            else:
                so_files = sorted(Path(plugin_dir).glob("*.so"),
                                  key=lambda p: p.stat().st_mtime,
                                  reverse=True) if os.path.isdir(plugin_dir) else []
                plugin_path = str(so_files[0]) if so_files else ""
            if not plugin_path or not os.path.exists(plugin_path):
                return json.dumps({"ok": False, "error": f"plugin not found: {plugin_path}"})
            toolhost_path = os.path.join(MACHINA_ROOT, "build", "machina_toolhost")
            if os.path.exists(toolhost_path):
                try:
                    result = subprocess.run(
                        [toolhost_path, "--load-plugin", plugin_path],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        return json.dumps({"ok": True, "loaded": plugin_path, "method": "toolhost"})
                except Exception as e:
                    logger.warning(f"toolhost plugin load failed: {e}")
            # Validate any AID declared in the plugin's metadata
            plugin_aid = inputs.get("aid", "")
            if plugin_aid:
                valid, msg = validate_aid(plugin_aid)
                if not valid:
                    logger.warning(f"Genesis LOAD: plugin AID naming violation: {msg}")
            reg_file = os.path.join(plugin_dir, ".pending_load")
            with open(reg_file, "a") as f:
                f.write(plugin_path + "\n")
            result_payload = {"ok": True, "registered": plugin_path,
                              "note": "will load on next engine restart"}
            if plugin_aid:
                valid, msg = validate_aid(plugin_aid)
                if not valid:
                    result_payload["aid_warning"] = msg
            return json.dumps(result_payload)

        # Fallback: try machina_toolhost for any unrecognized tool
        return run_machina_toolhost(aid, inputs)

    except subprocess.TimeoutExpired:
        return _toolhost_error(aid, "timeout", "command timed out")
    except Exception as e:
        logger.error(f"tool_error [{aid}]: {type(e).__name__}: {e}")
        return _toolhost_error(aid, "exception", f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Execute Intent Actions
# ---------------------------------------------------------------------------

def execute_intent(intent: dict, user_text: str, force_code: bool = False,
                    allow_net: bool = False) -> str:
    """Execute parsed intent and return results."""
    # Lazy imports to avoid circular dependency
    from machina_learning import skill_record

    intent_type = intent.get("type", "")

    if intent_type == "reply":
        return intent.get("content", "")

    if intent_type == "action":
        actions = intent.get("actions", [])
        all_results = []

        for action in actions:
            kind = action.get("kind", "")
            if kind == "goal":
                goal_id = action.get("goal_id", "")
                inputs = action.get("inputs", {})
                tags = action.get("candidate_tags", ["tag.meta"])
                if isinstance(inputs, str):
                    try:
                        inputs = json.loads(inputs)
                    except json.JSONDecodeError:
                        inputs = {}
                logger.info(f"Executing goal: {goal_id}")
                result = run_machina_goal(goal_id, inputs, tags)
                all_results.append({"goal": goal_id, "output": result})

            elif kind == "tool":
                aid = resolve_alias(action.get("aid", ""))
                inputs = action.get("inputs", {})
                if isinstance(inputs, str):
                    try:
                        inputs = json.loads(inputs)
                    except json.JSONDecodeError:
                        inputs = {}
                logger.info(f"Executing tool: {aid}")
                result = run_machina_tool(aid, inputs, force_code=force_code,
                                         allow_net=allow_net, _caller_approved=True)
                # Blocked/Network pattern: bubble up immediately for caller to handle
                if isinstance(result, str) and (
                        result.startswith("BLOCKED_PATTERN_ASK:")
                        or result.startswith("NETWORK_CODE_ASK:")):
                    return result
                entry = {"tool": aid, "output": result}
                # (2d) Enrich errors with actionable hints
                if (isinstance(result, dict) and result.get("error")) or \
                   (isinstance(result, str) and result.lower().startswith("error")):
                    hint = get_error_hint(str(result))
                    if hint:
                        entry["hint"] = hint
                # Auto skill recording for successful code execution
                if aid in (AID_CODE_EXEC, AID_UTIL_SAVE):
                    code = inputs.get("code", "")
                    try:
                        if _should_record_skill(code, str(result)):
                            skill_record(user_text, inputs.get("lang", "python"),
                                         code, str(result))
                            logger.info(f"[Skill] Auto-recorded from {aid} "
                                        f"({code.count(chr(10))+1} lines)")
                    except Exception as e:
                        logger.warning(f"[Skill] Auto-record failed: "
                                       f"{type(e).__name__}: {e}")
                all_results.append(entry)

            elif kind == "chain":
                # (2c) Multi-step auto-chaining
                chain_name = action.get("chain", "")
                chain_inputs = action.get("inputs", {})
                if isinstance(chain_inputs, str):
                    try:
                        chain_inputs = json.loads(chain_inputs)
                    except json.JSONDecodeError:
                        chain_inputs = {}
                logger.info(f"Executing chain: {chain_name}")
                chain_results = execute_chain(chain_name, chain_inputs)
                for cr in chain_results:
                    all_results.append(cr)

        if not all_results:
            return intent.get("assistant_prefix", "실행할 작업이 없어.")

        # Return raw results directly — no LLM summary, no truncation
        parts = []
        for r in all_results:
            out = r.get("output", "")
            hint = r.get("hint", "")
            if isinstance(out, dict):
                if out.get("error"):
                    _etype = out.get("type", "unknown")
                    _edetail = out.get("detail", "")
                    _eaid = out.get("aid", "?")
                    parts.append(f"[오류] {_eaid} \u2014 {_etype}: {_edetail}")
                    if hint:
                        parts.append(f"  힌트: {hint}")
                else:
                    parts.append(json.dumps(out, ensure_ascii=False))
            else:
                out_str = str(out)
                parts.append(out_str)
                if hint and ("error" in out_str.lower() or "failed" in out_str.lower()):
                    parts.append(f"  힌트: {hint}")
        result = "\n".join(parts)
        # Guard: truncate oversized results to 1MB
        if isinstance(result, str) and len(result) > 1_048_576:
            aid_label = actions[0].get("aid", "?") if actions else "?"
            result = result[:1_048_576] + "\n... [truncated at 1MB]"
            logger.warning(f"[Dispatch] Output truncated to 1MB for {aid_label}")
        return result

    return intent.get("content", "잘 모르겠어. 다시 말해줄래?")
