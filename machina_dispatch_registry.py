#!/usr/bin/env python3
"""Machina Dispatch Registry — static AID constants, aliases, descriptions, and mappings.

Pure data module with zero side effects. No I/O, no imports beyond stdlib.
Consumed by machina_dispatch.py (facade) and machina_dispatch_exec.py (execution).
"""

import json
import re as _re

# ---------------------------------------------------------------------------
# AID Constants — ALL tool identifiers follow AID.{DOMAIN}.{ACTION}.v{N}
# ---------------------------------------------------------------------------
# C++ toolhost tools (defined in toolpacks/tier0/manifest.json):
#   AID.FILE.READ.v1, AID.FILE.WRITE.v1, AID.SHELL.EXEC.v1,
#   AID.MEMORY.APPEND.v1, AID.MEMORY.QUERY.v1, AID.NET.HTTP_GET.v1,
#   AID.GENESIS.WRITE_FILE.v1, AID.GENESIS.COMPILE_SHARED.v1,
#   AID.GENESIS.LOAD_PLUGIN.v1, AID.EMBED.TEXT.v1, AID.VECDB.*.v1, etc.
#
# Python-only tools (handled in run_machina_tool):
AID_UTIL_SAVE = "AID.UTIL.SAVE.v1"
AID_UTIL_RUN = "AID.UTIL.RUN.v1"
AID_UTIL_LIST = "AID.UTIL.LIST.v1"
AID_UTIL_DELETE = "AID.UTIL.DELETE.v1"
AID_UTIL_UPDATE = "AID.UTIL.UPDATE.v1"
AID_CODE_EXEC = "AID.CODE.EXEC.v1"
AID_NET_WEB_SEARCH = "AID.NET.WEB_SEARCH.v1"
AID_FILE_LIST = "AID.FILE.LIST.v1"
AID_FILE_SEARCH = "AID.FILE.SEARCH.v1"
AID_FILE_DIFF = "AID.FILE.DIFF.v1"
AID_FILE_EDIT = "AID.FILE.EDIT.v1"
AID_FILE_APPEND = "AID.FILE.APPEND.v1"
AID_FILE_DELETE = "AID.FILE.DELETE.v1"
AID_PROJECT_CREATE = "AID.PROJECT.CREATE.v1"
AID_PROJECT_BUILD = "AID.PROJECT.BUILD.v1"
AID_PIP_INSTALL = "AID.SYSTEM.PIP_INSTALL.v1"
AID_PIP_UNINSTALL = "AID.SYSTEM.PIP_UNINSTALL.v1"
AID_PIP_LIST = "AID.SYSTEM.PIP_LIST.v1"

# Collected set of all Python-side AIDs (for scan_gaps, etc.)
PYTHON_AIDS = {
    AID_UTIL_SAVE, AID_UTIL_RUN, AID_UTIL_LIST,
    AID_UTIL_DELETE, AID_UTIL_UPDATE,
    AID_CODE_EXEC, AID_NET_WEB_SEARCH,
    AID_FILE_LIST, AID_FILE_SEARCH, AID_FILE_DIFF,
    AID_FILE_EDIT, AID_FILE_APPEND, AID_FILE_DELETE,
    AID_PROJECT_CREATE, AID_PROJECT_BUILD,
    AID_PIP_INSTALL, AID_PIP_UNINSTALL, AID_PIP_LIST,
}

# AID naming convention validation
_AID_PATTERN = _re.compile(r'^AID\.[A-Z][A-Z0-9_]+(\.[A-Z][A-Z0-9_]+)*\.v\d+$')


def validate_aid(aid: str) -> tuple:
    """Validate AID follows AID.{DOMAIN}.{ACTION}.v{N} convention."""
    if not aid:
        return (False, "empty AID")
    if not _AID_PATTERN.match(aid):
        return (False, f"'{aid}' doesn't match AID.DOMAIN.ACTION.vN pattern")
    return (True, "valid")


# ---------------------------------------------------------------------------
# Tool Aliases — convenience name → canonical AID
# Convenience names are NEVER used as dispatch keys.
# ---------------------------------------------------------------------------
TOOL_ALIASES = {
    # English convenience aliases
    "create_file": "AID.FILE.WRITE.v1",
    "read_file": "AID.FILE.READ.v1",
    "write_file": "AID.FILE.WRITE.v1",
    "file_write": "AID.FILE.WRITE.v1",
    "file_read": "AID.FILE.READ.v1",
    "run_shell": "AID.SHELL.EXEC.v1",
    "shell_exec": "AID.SHELL.EXEC.v1",
    "search_memory": "AID.MEMORY.QUERY.v1",
    "memory_query": "AID.MEMORY.QUERY.v1",
    "save_memory": "AID.MEMORY.APPEND.v1",
    "memory_append": "AID.MEMORY.APPEND.v1",
    "memory_save": "AID.MEMORY.APPEND.v1",
    "create_tool": "AID.GENESIS.WRITE_FILE.v1",
    "compile_tool": "AID.GENESIS.COMPILE_SHARED.v1",
    "load_tool": "AID.GENESIS.LOAD_PLUGIN.v1",
    "fetch_url": "AID.NET.HTTP_GET.v1",
    "http_get": "AID.NET.HTTP_GET.v1",
    "web_search": AID_NET_WEB_SEARCH,
    "error_scan": "AID.ERROR_SCAN.v1",
    "error_scan_csv": "AID.ERROR_SCAN.v1",
    "gpu_smoke": "AID.GPU_SMOKE.v1",
    "gpu_metrics": "AID.GPU_METRICS.v1",
    "proc_self_metrics": "AID.PROC.SELF_METRICS.v1",
    "delete_tool": AID_UTIL_DELETE,
    "remove_tool": AID_UTIL_DELETE,
    "update_tool": AID_UTIL_UPDATE,
    "save_util": AID_UTIL_SAVE,
    "run_util": AID_UTIL_RUN,
    "list_util": AID_UTIL_LIST,
    "execute_code": AID_CODE_EXEC,
    "code_exec": AID_CODE_EXEC,
    # Legacy flat names → AID (backward compat)
    "util_save": AID_UTIL_SAVE,
    "util_run": AID_UTIL_RUN,
    "util_list": AID_UTIL_LIST,
    "util_delete": AID_UTIL_DELETE,
    "util_update": AID_UTIL_UPDATE,
    # Korean-friendly aliases
    "파일읽기": "AID.FILE.READ.v1",
    "파일쓰기": "AID.FILE.WRITE.v1",
    "셸실행": "AID.SHELL.EXEC.v1",
    "기억검색": "AID.MEMORY.QUERY.v1",
    "기억저장": "AID.MEMORY.APPEND.v1",
    "도구생성": "AID.GENESIS.WRITE_FILE.v1",
    "도구삭제": AID_UTIL_DELETE,
    "도구수정": AID_UTIL_UPDATE,
    "도구저장": AID_UTIL_SAVE,
    "도구실행": AID_UTIL_RUN,
    "도구목록": AID_UTIL_LIST,
    "코드실행": AID_CODE_EXEC,
    "웹검색": AID_NET_WEB_SEARCH,
    # File tool aliases (English)
    "list_dir": AID_FILE_LIST,
    "list_files": AID_FILE_LIST,
    "search_files": AID_FILE_SEARCH,
    "grep_files": AID_FILE_SEARCH,
    "diff_files": AID_FILE_DIFF,
    "compare_files": AID_FILE_DIFF,
    "edit_file": AID_FILE_EDIT,
    "patch_file": AID_FILE_EDIT,
    "append_file": AID_FILE_APPEND,
    # File tool aliases (Korean)
    "파일목록": AID_FILE_LIST,
    "디렉토리": AID_FILE_LIST,
    "파일검색": AID_FILE_SEARCH,
    "내용검색": AID_FILE_SEARCH,
    "파일비교": AID_FILE_DIFF,
    "파일편집": AID_FILE_EDIT,
    "줄편집": AID_FILE_EDIT,
    "파일추가": AID_FILE_APPEND,
    # File delete aliases
    "delete_file": AID_FILE_DELETE,
    "remove_file": AID_FILE_DELETE,
    "파일삭제": AID_FILE_DELETE,
    # Project aliases
    "create_project": AID_PROJECT_CREATE,
    "프로젝트생성": AID_PROJECT_CREATE,
    "build_project": AID_PROJECT_BUILD,
    "프로젝트빌드": AID_PROJECT_BUILD,
    # Package management aliases
    "pip_install": AID_PIP_INSTALL,
    "install_package": AID_PIP_INSTALL,
    "패키지설치": AID_PIP_INSTALL,
    "pip_uninstall": AID_PIP_UNINSTALL,
    "uninstall_package": AID_PIP_UNINSTALL,
    "remove_package": AID_PIP_UNINSTALL,
    "패키지삭제": AID_PIP_UNINSTALL,
    "패키지제거": AID_PIP_UNINSTALL,
    "pip_list": AID_PIP_LIST,
    "list_packages": AID_PIP_LIST,
    "패키지목록": AID_PIP_LIST,
}

# Legacy AID variants observed in historical logs/docs.
# Normalize these to canonical AIDs so old memories/intents still execute.
_LEGACY_AID_MAP = {
    "AID.GPU.SMOKE.v1": "AID.GPU_SMOKE.v1",
    "AID.GPU.METRICS.v1": "AID.GPU_METRICS.v1",
    "AID.NET.SEARCH.v1": AID_NET_WEB_SEARCH,
    "AID.GENESIS.RUN.v1": "AID.GENESIS.WRITE_FILE.v1",
}


def resolve_alias(name: str) -> str:
    """Resolve tool alias to canonical AID. Passthrough if not alias."""
    if name in TOOL_ALIASES:
        return TOOL_ALIASES[name]
    return _LEGACY_AID_MAP.get(name, name)


# Short descriptions for LLM comprehension (injected into prompts)
TOOL_DESCRIPTIONS = {
    "AID.FILE.READ.v1": "파일 내용 읽기",
    "AID.FILE.WRITE.v1": "파일 생성/수정 (work/ 경로)",
    "AID.SHELL.EXEC.v1": "셸 명령 실행",
    "AID.MEMORY.QUERY.v1": "기억에서 검색",
    "AID.MEMORY.APPEND.v1": "기억에 저장",
    "AID.NET.HTTP_GET.v1": "URL 페이지 읽기",
    "AID.GENESIS.WRITE_FILE.v1": "C++ 도구 소스 작성",
    "AID.GENESIS.COMPILE_SHARED.v1": "C++ 도구 컴파일",
    "AID.GENESIS.LOAD_PLUGIN.v1": "컴파일된 도구 로드",
    AID_UTIL_SAVE: "유틸리티 스크립트 저장",
    AID_UTIL_RUN: "저장된 유틸리티 실행",
    AID_UTIL_LIST: "유틸리티 목록 조회",
    AID_UTIL_DELETE: "유틸리티 삭제",
    AID_UTIL_UPDATE: "유틸리티 코드/설명 수정",
    AID_CODE_EXEC: "Python/Bash/C++ 코드 실행",
    AID_NET_WEB_SEARCH: "웹 검색 (DuckDuckGo)",
    AID_FILE_LIST: "디렉토리 파일 목록 조회",
    AID_FILE_SEARCH: "파일 내용 검색 (grep)",
    AID_FILE_DIFF: "두 파일 비교 (unified diff)",
    AID_FILE_EDIT: "줄 단위 파일 편집 (replace/insert/delete)",
    AID_FILE_APPEND: "파일 끝에 내용 추가",
    AID_FILE_DELETE: "파일/디렉토리 삭제 (work/ 전용, 휴지통 이동)",
    AID_PROJECT_CREATE: "멀티파일 프로젝트 생성 (C++/Python)",
    AID_PROJECT_BUILD: "C++ 프로젝트 빌드 (shared/executable)",
    AID_PIP_INSTALL: "Python 패키지 설치 (격리 venv)",
    AID_PIP_UNINSTALL: "Python 패키지 제거 (격리 venv)",
    AID_PIP_LIST: "venv 설치된 패키지 목록 조회",
}


# ---------------------------------------------------------------------------
# Smart Menu Filter — intent keywords → relevant tools (3-5)
# ---------------------------------------------------------------------------
INTENT_TOOL_MAP = {
    "file": ["AID.FILE.READ.v1", "AID.FILE.WRITE.v1", AID_FILE_LIST,
             AID_FILE_SEARCH, AID_FILE_DIFF, AID_FILE_EDIT, AID_FILE_APPEND,
             AID_FILE_DELETE],
    "project": [AID_PROJECT_CREATE, AID_PROJECT_BUILD,
                "AID.GENESIS.WRITE_FILE.v1", "AID.GENESIS.COMPILE_SHARED.v1"],
    "install": [AID_PIP_INSTALL, AID_PIP_UNINSTALL, AID_PIP_LIST],
    "memory": ["AID.MEMORY.QUERY.v1", "AID.MEMORY.APPEND.v1"],
    "search": [AID_NET_WEB_SEARCH, "AID.NET.HTTP_GET.v1"],
    "shell": ["AID.SHELL.EXEC.v1", "AID.FILE.READ.v1"],
    "genesis": ["AID.GENESIS.WRITE_FILE.v1", "AID.GENESIS.COMPILE_SHARED.v1",
                "AID.GENESIS.LOAD_PLUGIN.v1"],
    "code": [AID_CODE_EXEC],
    "web": ["AID.NET.HTTP_GET.v1", AID_NET_WEB_SEARCH],
    "util": [AID_UTIL_SAVE, AID_UTIL_RUN, AID_UTIL_LIST,
             AID_UTIL_DELETE, AID_UTIL_UPDATE],
}


def filter_tools_for_intent(intent_type: str) -> list:
    """Return relevant AID tool list for given intent type (3-5 tools)."""
    tools = INTENT_TOOL_MAP.get(intent_type, [])
    if not tools:
        tools = ["AID.SHELL.EXEC.v1", "AID.FILE.READ.v1", "AID.MEMORY.QUERY.v1"]
    return tools[:5]


# ---------------------------------------------------------------------------
# Multi-step Auto-Chaining — composite operations
# ---------------------------------------------------------------------------
CHAIN_RECIPES = {
    "create_tool": [
        ("AID.GENESIS.WRITE_FILE.v1", lambda i: {
            "relative_path": i.get("name", "tool") + ".cpp",
            "content": i.get("code", i.get("content", "")),
        }),
        ("AID.GENESIS.COMPILE_SHARED.v1", lambda i: {
            "src_relative_path": i.get("name", "tool") + ".cpp",
            "out_name": i.get("name", "tool"),
        }),
        ("AID.GENESIS.LOAD_PLUGIN.v1", lambda i: {}),
    ],
    "analyze_file": [
        ("AID.FILE.READ.v1", lambda i: {"path": i.get("path", ""), "max_bytes": 8192}),
    ],
    "save_and_remember": [
        ("AID.FILE.WRITE.v1", lambda i: {
            "path": i.get("path", "work/note.txt"),
            "content": i.get("content", ""),
        }),
        ("AID.MEMORY.APPEND.v1", lambda i: {
            "stream": "telegram", "event": "user_note",
            "text": i.get("content", "")[:300],
        }),
    ],
}


# ---------------------------------------------------------------------------
# Error Hints — pattern → actionable hint mapping
# ---------------------------------------------------------------------------
ERROR_HINTS = {
    "undefined reference": {
        "hint": "#include 누락 가능성",
        "suggested_action": "create_tool",
        "fix": "missing include 추가",
    },
    "no such file": {
        "hint": "파일 경로 확인 필요",
        "suggested_action": "file_read",
        "fix": "경로를 절대경로로 변경",
    },
    "permission denied": {
        "hint": "권한 부족. work/ 경로만 쓰기 가능",
        "suggested_action": "file_write",
        "fix": "work/ 하위로 경로 변경",
    },
    "compilation failed": {
        "hint": "C++ 문법 에러. 코드 수정 필요",
        "suggested_action": "create_tool",
        "fix": "compile error 수정 후 재시도",
    },
    "timed out": {
        "hint": "시간 초과. 더 간단한 명령 사용",
        "suggested_action": "run_shell",
        "fix": "timeout 증가 또는 명령 단순화",
    },
    "command not found": {
        "hint": "명령어 미설치",
        "suggested_action": "run_shell",
        "fix": "apt install 또는 대체 명령어 사용",
    },
    "json": {
        "hint": "JSON 파싱 실패. 입력 형식 확인",
        "suggested_action": None,
        "fix": "유효한 JSON 형식으로 재시도",
    },
    "traceback": {
        "hint": "Python 런타임 에러",
        "suggested_action": "code",
        "fix": "에러 메시지 확인 후 코드 수정",
    },
    "path outside sandbox": {
        "hint": "보안 샌드박스 밖 경로 접근 불가",
        "suggested_action": "file_read",
        "fix": "MACHINA_ROOT 하위 경로만 접근 가능",
    },
}


def get_error_hint(error_text: str) -> dict:
    """Match error text to actionable hint."""
    if not error_text:
        return {}
    lower = error_text.lower()
    for pattern, hint in ERROR_HINTS.items():
        if pattern in lower:
            return dict(hint)
    return {}


# ---------------------------------------------------------------------------
# Function-call Format Normalization
# ---------------------------------------------------------------------------
def normalize_function_call(fc: dict) -> tuple:
    """Normalize OpenAI-style {"tool":"x","args":{}} to (aid, inputs).

    Accepts:
      {"tool": "create_file", "args": {"path": "...", "content": "..."}}
      {"pick": "AID.FILE.WRITE.v1", "input_patch_json": "..."}
      {"aid": "AID.FILE.WRITE.v1", "inputs": {...}}
    Returns: (canonical_aid, inputs_dict)
    """
    # Style 1: function-calling {"tool":"x","args":{}}
    if "tool" in fc and "args" in fc:
        tool_name = fc["tool"]
        aid = resolve_alias(tool_name)
        args = fc["args"]
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return aid, args

    # Style 2: legacy {"pick":"AID.XX","input_patch_json":"..."}
    if "pick" in fc:
        aid = fc["pick"]
        inputs_raw = fc.get("input_patch_json", "{}")
        if isinstance(inputs_raw, str):
            try:
                inputs = json.loads(inputs_raw)
            except json.JSONDecodeError:
                inputs = {}
        else:
            inputs = inputs_raw
        return aid, inputs

    # Style 3: direct {"aid":"AID.XX","inputs":{...}}
    if "aid" in fc:
        return fc["aid"], fc.get("inputs", {})

    return "", {}
