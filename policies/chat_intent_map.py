"""Machina Intent Mapping — normalize variant LLM outputs to canonical Machina action format."""

import json


def _normalize_intent(raw: dict, user_msg: str = "") -> dict:
    """Normalize variant outputs from small LLMs to canonical intent format.

    Small models produce creative variations:
    - {"type":"file_read","path":"..."} instead of {"type":"run","tool":"file_read",...}
    - {"type":"run","tool":"config",...} instead of {"type":"config",...}
    - {"type":"config","value":"oai_compat: qwen2.5:7b"} — compound values
    - {"model":"qwen",...} — missing type entirely
    """
    intent = dict(raw)  # shallow copy
    itype = intent.get("type", "")
    umsg = user_msg.lower()

    # --- Fix: type is a tool name directly (e.g. "file_read", "memory_save") ---
    tool_types = {"file_read", "file_write", "memory_save", "memory_find",
                  "shell", "search", "genesis", "code", "web", "mcp",
                  "util_save", "util_run", "util_list", "util_delete", "util_update",
                  "file_list", "file_search", "file_diff", "file_edit", "file_append",
                  "file_delete", "project_create", "project_build",
                  "pip_install", "pip_uninstall", "pip_list"}
    # Normalize tool aliases (LLMs invent names)
    tool_aliases = {"util_execute": "util_run", "run_util": "util_run",
                    "execute": "shell", "cmd": "shell", "python": "code",
                    "bash": "code", "google": "search", "browse": "web",
                    "delete_util": "util_delete", "remove_util": "util_delete",
                    "update_util": "util_update", "modify_util": "util_update",
                    "list_dir": "file_list", "ls": "file_list", "dir": "file_list",
                    "grep": "file_search", "search_files": "file_search",
                    "diff": "file_diff", "compare": "file_diff",
                    "edit": "file_edit", "patch": "file_edit",
                    "append": "file_append",
                    "rm": "file_delete", "delete": "file_delete", "remove": "file_delete",
                    "create_project": "project_create", "new_project": "project_create",
                    "build": "project_build", "compile": "project_build",
                    "pip": "pip_install", "install": "pip_install",
                    "uninstall": "pip_uninstall", "pip_uninstall": "pip_uninstall",
                    "pip_list": "pip_list", "packages": "pip_list"}
    if itype in tool_aliases:
        itype = tool_aliases[itype]
        intent["type"] = itype
    if intent.get("tool") in tool_aliases:
        intent["tool"] = tool_aliases[intent["tool"]]
    if itype in tool_types:
        intent["type"] = "run"
        intent["tool"] = itype

    # --- Fix: {"type":"run","tool":"config",...} → {"type":"config",...} ---
    if itype == "run" and intent.get("tool") == "config":
        intent["type"] = "config"
        # Move key/value up if nested
        for k in ("key", "value"):
            if k not in intent and k in intent.get("config", {}):
                intent[k] = intent["config"][k]

    # --- Fix: compound value like "oai_compat: qwen2.5:7b" ---
    if intent.get("type") == "config" and ":" in str(intent.get("value", "")):
        val = intent["value"]
        # "oai_compat: qwen2.5:7b" → set backend + model
        if val.startswith("oai_compat:") or val.startswith("ollama:"):
            parts = val.split(":", 1)
            if len(parts) == 2:
                model_name = parts[1].strip()
                intent["key"] = "model"
                intent["value"] = model_name

    # --- Fix: has "model" key but no proper type ---
    if not intent.get("type") and intent.get("model"):
        intent["type"] = "config"
        intent["key"] = "model"
        intent["value"] = intent["model"]

    # --- Fix: has "cmd" but no tool ---
    if intent.get("type") == "run" and not intent.get("tool") and intent.get("cmd"):
        intent["tool"] = "shell"

    # --- Fix: has "query" but no tool ---
    if intent.get("type") == "run" and not intent.get("tool") and intent.get("query"):
        intent["tool"] = "search"

    # --- Fix: has "path" but no tool ---
    if intent.get("type") == "run" and not intent.get("tool") and intent.get("path"):
        intent["tool"] = "file_read"

    # --- Fix: has "text" but no tool — detect search vs save intent ---
    if intent.get("type") == "run" and not intent.get("tool") and intent.get("text"):
        _text_lower = intent["text"].lower()
        _search_kw = ("찾아", "검색", "뭐였", "뭐라고", "언제", "어디", "알려", "search", "find", "when", "where")
        if any(kw in umsg.lower() for kw in _search_kw) or any(kw in _text_lower for kw in _search_kw):
            intent["tool"] = "memory_find"
        else:
            intent["tool"] = "memory_save"

    # --- Fix: has "code" key but no tool ---
    if intent.get("type") == "run" and not intent.get("tool") and intent.get("code"):
        intent["tool"] = "code"

    # --- Fix: has "url" key but no tool ---
    if intent.get("type") == "run" and not intent.get("tool") and intent.get("url"):
        intent["tool"] = "web"

    # --- Fix: user asked to switch model but LLM only set backend ---
    # Detect model name keywords in user message and override
    model_keywords = {
        "qwen": "qwen2.5:7b", "exaone": "exaone3.5:7.8b",
        "gemma": "gemma2:latest", "llama": "llama3:latest",
    }
    if intent.get("type") == "config" and umsg:
        for kw, model_val in model_keywords.items():
            if kw in umsg:
                # User clearly wants to switch model, not just backend
                intent["key"] = "model"
                intent["value"] = model_val
                break
        # Detect backend switch keywords
        backend_kw = {"클로드": "anthropic", "claude": "anthropic",
                      "올라마": "oai_compat", "ollama": "oai_compat",
                      "로컬": "oai_compat"}
        for kw, bval in backend_kw.items():
            if kw in umsg:
                intent["key"] = "backend"
                intent["value"] = bval
                break

    return intent


def _intent_to_machina_action(intent: dict, user_msg: str = "") -> dict:
    """Convert simplified intent JSON to full Machina action format.

    This bridges the gap between the simple intent format (optimized for small LLMs)
    and the full Machina action format expected by telegram_bot.py.
    """
    intent = _normalize_intent(intent, user_msg=user_msg)
    itype = intent.get("type", "")

    if itype == "chat":
        return {
            "type": "reply",
            "content": intent.get("msg", ""),
        }

    if itype == "config":
        key = intent.get("key", "")
        value = intent.get("value", "")
        changes = []
        # Map simplified keys to actual env var names
        key_map = {
            "backend": "MACHINA_CHAT_BACKEND",
            "model": "OAI_COMPAT_MODEL",
            "api_key": "ANTHROPIC_API_KEY",
            "temperature": "MACHINA_CHAT_TEMPERATURE",
        }
        env_key = key_map.get(key, key)

        # Normalize backend values
        backend_aliases = {
            "claude": "anthropic", "anthropic": "anthropic", "opus": "anthropic",
            "sonnet": "anthropic", "ollama": "oai_compat", "local": "oai_compat",
            "oai_compat": "oai_compat",
        }
        if key == "backend" and value.lower() in backend_aliases:
            value = backend_aliases[value.lower()]

        if env_key and value:
            changes.append({"key": env_key, "value": value})
            # If switching to a specific model, also ensure backend is oai_compat
            if key == "model":
                changes.insert(0, {"key": "MACHINA_CHAT_BACKEND", "value": "oai_compat"})
            # If switching to anthropic, set the backend
            if key == "backend" and value == "anthropic":
                changes = [{"key": "MACHINA_CHAT_BACKEND", "value": "anthropic"}]
        return {
            "type": "config",
            "changes": changes,
            "content": intent.get("msg", f"{key}를 {value}로 변경할게!"),
        }

    if itype == "run":
        tool = intent.get("tool", "")
        actions = []

        if tool == "shell":
            actions.append({
                "kind": "tool",
                "aid": "AID.SHELL.EXEC.v1",
                "inputs": {"cmd": intent.get("cmd", "echo 'no command'"), "timeout_ms": 10000},
            })

        elif tool == "search":
            query = intent.get("query", "")
            actions.append({
                "kind": "tool",
                "aid": "AID.NET.WEB_SEARCH.v1",
                "inputs": {"query": query},
            })

        elif tool == "memory_save":
            actions.append({
                "kind": "tool",
                "aid": "AID.MEMORY.APPEND.v1",
                "inputs": {
                    "stream": "telegram",
                    "event": "user_note",
                    "text": intent.get("text", ""),
                },
            })

        elif tool == "memory_find":
            actions.append({
                "kind": "tool",
                "aid": "AID.MEMORY.QUERY.v1",
                "inputs": {
                    "stream": "telegram",
                    "query": intent.get("text", ""),
                    "mode": "hybrid",
                    "top_k": 5,
                },
            })

        elif tool == "file_read":
            actions.append({
                "kind": "tool",
                "aid": "AID.FILE.READ.v1",
                "inputs": {"path": intent.get("path", ""), "max_bytes": 8192},
            })

        elif tool == "file_write":
            path = intent.get("path", "")
            # Ensure path is under work/
            if path and not path.startswith("work/") and not path.startswith("work\\"):
                path = f"work/{path}"
            actions.append({
                "kind": "tool",
                "aid": "AID.FILE.WRITE.v1",
                "inputs": {
                    "path": path,
                    "content": intent.get("content", ""),
                    "overwrite": True,
                },
            })

        elif tool == "genesis":
            # Genesis: auto-chained tool creation (write→compile→load)
            name = intent.get("name", "custom_tool")
            desc = intent.get("description", "")
            code = intent.get("code", "")
            if code:
                # Full code provided: use auto-chain
                actions.append({
                    "kind": "chain",
                    "chain": "create_tool",
                    "inputs": {"name": name, "code": code},
                })
            else:
                # Only description: write a stub
                actions.append({
                    "kind": "tool",
                    "aid": "AID.GENESIS.WRITE_FILE.v1",
                    "inputs": {
                        "relative_path": f"{name}.cpp",
                        "content": f'// Genesis auto-generated: {name}\n// {desc}\n#include <machina/plugin_api.h>\n',
                    },
                })

        elif tool == "code":
            # Code: write + execute Python/bash program
            lang = intent.get("lang", "python")
            code = intent.get("code", "")
            actions.append({
                "kind": "tool",
                "aid": "AID.CODE.EXEC.v1",
                "inputs": {"lang": lang, "code": code},
            })

        elif tool == "web":
            # Web: fetch URL content
            url = intent.get("url", "")
            actions.append({
                "kind": "tool",
                "aid": "AID.NET.HTTP_GET.v1",
                "inputs": {"url": url},
            })

        elif tool == "util_save":
            actions.append({
                "kind": "tool",
                "aid": "AID.UTIL.SAVE.v1",
                "inputs": {
                    "name": intent.get("name", "unnamed"),
                    "lang": intent.get("lang", "python"),
                    "code": intent.get("code", ""),
                    "description": intent.get("description", ""),
                },
            })

        elif tool == "util_run":
            actions.append({
                "kind": "tool",
                "aid": "AID.UTIL.RUN.v1",
                "inputs": {
                    "name": intent.get("name", ""),
                    "args": intent.get("args", ""),
                },
            })

        elif tool == "util_list":
            actions.append({
                "kind": "tool",
                "aid": "AID.UTIL.LIST.v1",
                "inputs": {},
            })

        elif tool == "util_delete":
            actions.append({
                "kind": "tool",
                "aid": "AID.UTIL.DELETE.v1",
                "inputs": {"name": intent.get("name", "")},
            })

        elif tool == "util_update":
            actions.append({
                "kind": "tool",
                "aid": "AID.UTIL.UPDATE.v1",
                "inputs": {
                    "name": intent.get("name", ""),
                    "code": intent.get("code", ""),
                    "description": intent.get("description", ""),
                },
            })

        elif tool == "file_list":
            actions.append({
                "kind": "tool",
                "aid": "AID.FILE.LIST.v1",
                "inputs": {
                    "path": intent.get("path", "."),
                    "max_items": int(intent.get("max_items", 100)),
                },
            })

        elif tool == "file_search":
            actions.append({
                "kind": "tool",
                "aid": "AID.FILE.SEARCH.v1",
                "inputs": {
                    "root": intent.get("root", "."),
                    "pattern": intent.get("pattern", ""),
                    "ext_filter": intent.get("ext_filter", ""),
                    "max_results": int(intent.get("max_results", 50)),
                },
            })

        elif tool == "file_diff":
            actions.append({
                "kind": "tool",
                "aid": "AID.FILE.DIFF.v1",
                "inputs": {
                    "path1": intent.get("path1", ""),
                    "path2": intent.get("path2", ""),
                    "context": int(intent.get("context", 3)),
                },
            })

        elif tool == "file_edit":
            actions.append({
                "kind": "tool",
                "aid": "AID.FILE.EDIT.v1",
                "inputs": {
                    "path": intent.get("path", ""),
                    "operation": intent.get("operation", "replace"),
                    "line": int(intent.get("line", 1)),
                    "content": intent.get("content", ""),
                },
            })

        elif tool == "file_append":
            actions.append({
                "kind": "tool",
                "aid": "AID.FILE.APPEND.v1",
                "inputs": {
                    "path": intent.get("path", ""),
                    "content": intent.get("content", ""),
                },
            })

        elif tool == "file_delete":
            actions.append({
                "kind": "tool",
                "aid": "AID.FILE.DELETE.v1",
                "inputs": {
                    "path": intent.get("path", ""),
                    "recursive": intent.get("recursive", False),
                },
            })

        elif tool == "project_create":
            actions.append({
                "kind": "tool",
                "aid": "AID.PROJECT.CREATE.v1",
                "inputs": {
                    "name": intent.get("name", ""),
                    "lang": intent.get("lang", "cpp"),
                    "files": intent.get("files", []),
                },
            })

        elif tool == "project_build":
            actions.append({
                "kind": "tool",
                "aid": "AID.PROJECT.BUILD.v1",
                "inputs": {
                    "name": intent.get("name", ""),
                    "lang": intent.get("lang", "cpp"),
                    "build_type": intent.get("build_type", "shared"),
                },
            })

        elif tool == "pip_install":
            actions.append({
                "kind": "tool",
                "aid": "AID.SYSTEM.PIP_INSTALL.v1",
                "inputs": {
                    "packages": intent.get("packages", []),
                    "venv_name": intent.get("venv_name", "default"),
                },
            })

        elif tool == "pip_uninstall":
            actions.append({
                "kind": "tool",
                "aid": "AID.SYSTEM.PIP_UNINSTALL.v1",
                "inputs": {
                    "packages": intent.get("packages", []),
                    "venv_name": intent.get("venv_name", "default"),
                },
            })

        elif tool == "pip_list":
            actions.append({
                "kind": "tool",
                "aid": "AID.SYSTEM.PIP_LIST.v1",
                "inputs": {
                    "venv_name": intent.get("venv_name", "default"),
                },
            })

        elif tool == "mcp":
            # MCP tool call: {"type":"run","tool":"mcp","mcp_server":"x","mcp_tool":"y","args":{}}
            mcp_server = intent.get("mcp_server", "")
            mcp_tool = intent.get("mcp_tool", "")
            mcp_args = intent.get("args", {})
            if isinstance(mcp_args, str):
                try:
                    mcp_args = json.loads(mcp_args)
                except (json.JSONDecodeError, ValueError):
                    mcp_args = {}
            if mcp_server and mcp_tool:
                # Build AID from server+tool names
                import re
                def _san(n):
                    return re.sub(r"[^A-Z0-9_]", "_", n.upper())
                aid = f"AID.MCP.{_san(mcp_server)}.{_san(mcp_tool)}.v1"
                actions.append({
                    "kind": "tool",
                    "aid": aid,
                    "inputs": mcp_args,
                })
            else:
                return {
                    "type": "reply",
                    "content": "MCP 도구 호출엔 mcp_server와 mcp_tool이 필요해!",
                }

        else:
            # Unknown tool → treat as chat
            return {
                "type": "reply",
                "content": intent.get("msg", f"'{tool}' 도구를 잘 모르겠어. 다시 말해줄래?"),
            }

        prefix_map = {
            "shell": "실행 중... ⏳",
            "search": "검색 중... 🔍",
            "memory_save": "기억할게! 💾",
            "memory_find": "기억 찾는 중... 🔎",
            "file_read": "파일 읽는 중... 📄",
            "file_write": "파일 쓰는 중... ✏️",
            "genesis": "도구 생성 중... 🔨",
            "code": "코드 실행 중... 💻",
            "web": "URL 가져오는 중... 🌐",
            "util_save": "유틸리티 저장 중... 🔧",
            "util_run": "유틸리티 실행 중... ▶️",
            "util_list": "유틸리티 목록 조회 중... 📋",
            "util_delete": "유틸리티 삭제 중... 🗑️",
            "util_update": "유틸리티 수정 중... ✏️",
            "file_list": "파일 목록 조회 중... 📁",
            "file_search": "파일 검색 중... 🔍",
            "file_diff": "파일 비교 중... ↔️",
            "file_edit": "파일 편집 중... ✏️",
            "file_append": "파일 추가 중... 📝",
            "file_delete": "파일 삭제 중... 🗑️",
            "project_create": "프로젝트 생성 중... 📁",
            "project_build": "프로젝트 빌드 중... 🏗️",
            "pip_install": "패키지 설치 중... 📦",
            "pip_uninstall": "패키지 제거 중... 🗑️",
            "pip_list": "패키지 목록 조회 중... 📋",
            "mcp": "MCP 도구 실행 중... 🔌",
        }

        # Pass through _next chain marker from LLM intent
        result = {
            "type": "action",
            "actions": actions,
            "assistant_prefix": prefix_map.get(tool, "작업 중... ⏳"),
            "needs_summary": tool not in ("memory_save",),
        }
        if intent.get("_next"):
            result["_next"] = intent["_next"]
        return result

    # Fallback: treat as chat
    return {"type": "reply", "content": intent.get("msg", "뭐라고? 다시 말해줘!")}
