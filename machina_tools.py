#!/usr/bin/env python3
"""Machina Tools — code execution, auto-fix, utility system."""

import json
import logging
import os
import re
import subprocess
import time

from machina_shared import (
    MACHINA_ROOT,
    UTILS_DIR,
    UTILS_MANIFEST,
    sandboxed_run,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Python Code Auto-Fix (shared by execute_code and util_save)
# ---------------------------------------------------------------------------

def _python_code_autofix(code_text: str) -> str:
    """Apply 6-layer Python code auto-fix pipeline.

    Layers:
      0a. Strip markdown code fences
      0b. Replace input() calls with default values
      0c. Replace f-strings with str() concatenation
      1.  Missing colon auto-fix (for/if/while/def/class/elif/else/try/except/with/finally)
      2.  Indentation error fix (dedent, then aggressive strip)
      3a. Truncated code removal (pop trailing broken lines)
      3b. Print injection (if no output mechanism detected)
    """
    # Auto-fix 0a: Strip markdown code fences (LLMs often wrap code in ```)
    code_text = code_text.strip()
    if code_text.startswith("```"):
        lines = code_text.split("\n")
        # Remove opening fence (```python, ```py, ```)
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code_text = "\n".join(lines)

    # Auto-fix 0b: Replace input() calls with default values (LLMs ignore no-input rule)
    if "input(" in code_text:
        # Detect context: if wrapped in int() or used for numbers, use "10"
        # Otherwise use "hello world"
        _ct = code_text  # capture for closure

        def _replace_input(m):
            full = m.group(0)
            # Check surrounding context for int(), float(), number-related usage
            start = max(0, m.start() - 20)
            prefix = _ct[start:m.start()]
            if "int(" in prefix or "float(" in prefix or "number" in prefix.lower():
                return '"20"'
            return '"hello world"'
        code_text = re.sub(r'input\s*\([^)]*\)', _replace_input, code_text)

    # Auto-fix 0c: Replace f-strings with str() concatenation (small LLMs ignore no-fstring rule)
    # Only fix simple single-line f-strings; skip triple-quoted which break the regex
    if 'f"""' not in code_text and "f'''" not in code_text and ("f'" in code_text or 'f"' in code_text):
        def _replace_fstring(m):
            quote = m.group(1)  # ' or "
            body = m.group(2)
            parts = []
            pos = 0
            for em in re.finditer(r'\{([^}]+)\}', body):
                before = body[pos:em.start()]
                if before:
                    parts.append(quote + before + quote)
                expr = em.group(1).strip()
                # Handle format specs like {val:.2f}
                if ":" in expr:
                    var, fmt = expr.split(":", 1)
                    parts.append('format(' + var.strip() + ',"' + fmt.strip() + '")')
                else:
                    parts.append("str(" + expr + ")")
                pos = em.end()
            after = body[pos:]
            if after:
                parts.append(quote + after + quote)
            return "+".join(parts) if parts else quote + quote
        code_text = re.sub(r'f([\'"])((?:(?!\1).)*)\1', _replace_fstring, code_text)

    # Auto-fix 1: missing colons after for/if/while/def/class/elif/else/try/except/with/finally
    fixed_lines = []
    for line in code_text.split("\n"):
        rstripped = line.rstrip()
        content = rstripped.lstrip()
        indent = rstripped[:len(rstripped) - len(content)]  # preserve original indent
        if content and not content.endswith(":") and not content.endswith("\\"):
            kw_needs_colon = ("for ", "if ", "while ", "def ", "class ",
                              "elif ", "with ")
            bare_kw_needs_colon = ("else", "try", "except", "finally")
            if (any(content.startswith(kw) for kw in kw_needs_colon) or
                content in bare_kw_needs_colon or
                any(content.startswith(kw + " ") or content.startswith(kw + ":")
                    for kw in bare_kw_needs_colon)):
                # Check for unbracketed colon (ignore : inside parens/brackets/braces)
                code_part = content.split("#")[0]
                depth = 0
                has_free_colon = False
                for ch in code_part:
                    if ch in "([{": depth += 1
                    elif ch in ")]}": depth -= 1
                    elif ch == ":" and depth == 0:
                        has_free_colon = True
                        break
                if not has_free_colon:
                    content += ":"
        fixed_lines.append(indent + content)
    code_text = "\n".join(fixed_lines)

    # Auto-fix 2: fix indentation errors (try dedent, then strip leading)
    try:
        compile(code_text, "<check>", "exec")
    except IndentationError:
        import textwrap
        dedented = textwrap.dedent(code_text)
        try:
            compile(dedented, "<check>", "exec")
            code_text = dedented
        except (SyntaxError, IndentationError):
            # Aggressive fix: remove unexpected indent from non-block lines
            lines = code_text.split("\n")
            fixed = []
            for i, line in enumerate(lines):
                stripped = line.lstrip()
                if stripped and i > 0:
                    # If previous line doesn't end with ':', this shouldn't be indented more
                    prev = fixed[-1].rstrip() if fixed else ""
                    if not prev.endswith(":") and len(line) - len(stripped) > 0:
                        # Check if this line should be at top level
                        kw = stripped.split()[0] if stripped.split() else ""
                        if kw in ("for", "while", "if", "def", "class", "return", "print",
                                 "result", "import", "from", "try", "with"):
                            line = stripped  # dedent to top level
                fixed.append(line)
            code_text = "\n".join(fixed)
    except SyntaxError as e:
        logger.debug(f"SyntaxError: {e}")
        pass

    # Auto-fix 3a: remove trailing broken lines (truncated code)
    try:
        compile(code_text, "<check>", "exec")
    except SyntaxError:
        lines = code_text.rstrip().split("\n")
        for _ in range(min(3, len(lines))):
            lines.pop()
            try:
                compile("\n".join(lines), "<check>", "exec")
                code_text = "\n".join(lines)
                break
            except SyntaxError:
                continue

    # Auto-fix 3b: inject print() if no output mechanism detected
    has_output = any(kw in code_text for kw in ("print(", "print (", "sys.stdout",
                                                 "write(", "logging.", "logger."))
    if not has_output:
        # Find the last assignment or expression and wrap in print()
        lines = code_text.rstrip().split("\n")
        # Strategy: add print() of likely result variables at the end
        last_line = lines[-1].strip() if lines else ""
        # Check for common patterns: variable = expression
        assign_match = re.match(r'^(\w+)\s*=\s*.+', last_line)
        if assign_match:
            var_name = assign_match.group(1)
            lines.append(f"print({var_name})")
        else:
            # Look for result-like variable names in code
            result_vars = re.findall(r'\b(result|output|answer|res|ret|ans|sorted_?\w*|reversed_?\w*|fib\w*|nums?)\s*=',
                                    code_text)
            if result_vars:
                lines.append(f"print({result_vars[-1]})")
        code_text = "\n".join(lines)

    return code_text


# ---------------------------------------------------------------------------
# Utility manifest helpers
# ---------------------------------------------------------------------------

def _load_utils_manifest() -> dict:
    """Load the utilities manifest (name -> metadata)."""
    if os.path.exists(UTILS_MANIFEST):
        try:
            with open(UTILS_MANIFEST, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug(f"{type(e).__name__}: {e}")
            pass
    return {}


def _save_utils_manifest(manifest: dict):
    """Save the utilities manifest."""
    os.makedirs(UTILS_DIR, exist_ok=True)
    with open(UTILS_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Tool Functions
# ---------------------------------------------------------------------------

def execute_code(lang: str, code: str, timeout_s: int = 0, force: bool = False,
                  allow_net: bool = False) -> str:
    """Execute Python or Bash code safely in work/scripts/ sandbox.

    - Scripts written to MACHINA_ROOT/work/scripts/
    - Execution sandboxed to work/ directory
    - stdout+stderr captured with timeout

    SECURITY BOUNDARY: This tool intentionally executes LLM-generated code.
    Defense layers: timeout (default 15s), cwd restricted to work/,
    output truncation (4KB). For production hardening, enable
    MACHINA_TOOLHOST_ISOLATE=1 (seccomp + rlimits at the C++ toolhost level).
    The subprocess itself does NOT run with shell=True — it invokes the
    interpreter directly with the script path as an argument.
    """
    # Timeout: env override → caller arg → default 60s (was 15s)
    _DEFAULT_CODE_TIMEOUT = int(os.getenv("MACHINA_CODE_TIMEOUT", "60"))
    if timeout_s <= 0:
        timeout_s = _DEFAULT_CODE_TIMEOUT

    # Safety blocklist for LLM-generated code — detect dangerous patterns
    # Caller can bypass by passing force=True (after user approval)
    _CODE_BLOCKLIST = [
        "os.system(", "subprocess.", "eval(", "exec(", "compile(",
        "__import__(", "__builtins__", "getattr(", "setattr(",
        "base64.", "codecs.", "__class__", "__subclasses__",
        "globals()[", "locals()[",
    ]
    code_str = code if isinstance(code, str) else str(code)
    if not force and any(d in code_str for d in _CODE_BLOCKLIST):
        matched = [d for d in _CODE_BLOCKLIST if d in code_str]
        return f"BLOCKED_PATTERN_ASK:{','.join(matched)}"

    # Network access detection — code needs internet but sandbox blocks it
    _NET_PATTERNS = [
        "requests.", "urllib.", "http.client", "httpx.", "aiohttp.",
        "playwright", "selenium", "socket.connect", "urlopen(",
        "curl ", "wget ",
    ]
    _needs_net = any(p in code_str for p in _NET_PATTERNS)
    if not allow_net and _needs_net:
        matched = [p for p in _NET_PATTERNS if p in code_str]
        return f"NETWORK_CODE_ASK:{','.join(matched)}"
    # Network code gets longer timeout (page loads, API calls)
    if _needs_net and allow_net:
        timeout_s = max(timeout_s, 60)

    scripts_dir = os.path.join(MACHINA_ROOT, "work", "scripts")
    os.makedirs(scripts_dir, exist_ok=True)

    ts = int(time.time() * 1000)
    if lang == "bash":
        ext = "sh"
        runner = ["bash"]
    elif lang in ("c", "cpp", "c++"):
        ext = "cpp"
        runner = None  # compiled — handled separately below
    else:
        ext = "py"
        runner = ["python3"]

    script_path = os.path.join(scripts_dir, f"run_{ts}.{ext}")

    # Coerce code to string (LLM may return list/dict)
    if isinstance(code, list):
        code = "\n".join(str(line) for line in code)
    elif isinstance(code, dict):
        code = str(code.get("code", "") or code.get("content", "") or code)
    elif not isinstance(code, str):
        code = str(code)
    # Unescape literal \n in code string from JSON — only if code is single-line
    # (avoids damaging regex \\n patterns in already-multiline code)
    if "\n" not in code:
        code_text = code.replace("\\n", "\n").replace("\\t", "\t")
    else:
        code_text = code

    # Apply Python auto-fix pipeline
    if lang == "python":
        code_text = _python_code_autofix(code_text)
    else:
        # For bash/C++/others: strip code fences
        code_text = code_text.strip()
        if code_text.startswith("```"):
            lines = code_text.split("\n")
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code_text = "\n".join(lines)

    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code_text)

        work_dir = os.path.join(MACHINA_ROOT, "work")
        writable = [work_dir]

        # C/C++ needs compile step first
        if runner is None and ext == "cpp":
            bin_path = script_path.replace(".cpp", "")
            compile_result = sandboxed_run(
                ["g++", "-std=c++17", "-O2", "-o", bin_path, script_path],
                timeout=timeout_s,
                cwd=work_dir,
                writable_dirs=writable,
            )
            if compile_result.returncode != 0:
                err = compile_result.stderr[:2000] if compile_result.stderr else "(no stderr)"
                return f"compile error:\n{err}"
            result = sandboxed_run(
                [bin_path],
                timeout=timeout_s,
                cwd=work_dir,
                writable_dirs=writable,
                allow_net=allow_net,
            )
        else:
            result = sandboxed_run(
                runner + [script_path],
                timeout=timeout_s,
                cwd=work_dir,
                writable_dirs=writable,
                allow_net=allow_net,
            )

        output = result.stdout
        if result.returncode != 0 and result.stderr:
            output += f"\n[stderr] {result.stderr}"
        if not output.strip():
            output = f"(exit code: {result.returncode}, no output)"

        # Post-execution network error detection: if sandbox blocked network
        # and we see DNS/connection errors, bubble up for user approval
        if not allow_net and result.returncode != 0:
            _net_err_signs = (
                "name resolution", "gaierror", "Name or service not known",
                "Network is unreachable", "ConnectionRefusedError",
                "urlopen error", "ConnectionError", "MaxRetryError",
                "NewConnectionError",
            )
            _combined = (output + (result.stderr or "")).lower()
            if any(s.lower() in _combined for s in _net_err_signs):
                return f"NETWORK_CODE_ASK:sandbox_net_blocked"

        return output
    except subprocess.TimeoutExpired:
        return f"error: code execution timed out ({timeout_s}s)"
    except Exception as e:
        return f"code execution error: {e}"


def util_save(name: str, lang: str, code: str, description: str = "") -> str:
    """Save a reusable utility script to work/scripts/utils/."""
    os.makedirs(UTILS_DIR, exist_ok=True)
    # Sanitize name
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name).lower()
    if not safe_name:
        return "error: invalid utility name"

    ext = "sh" if lang == "bash" else "py"
    script_path = os.path.join(UTILS_DIR, f"{safe_name}.{ext}")
    # Coerce code to string (LLM may return list/dict)
    if isinstance(code, list):
        code = "\n".join(str(line) for line in code)
    elif isinstance(code, dict):
        code = str(code.get("code", "") or code.get("content", "") or code)
    elif not isinstance(code, str):
        code = str(code)
    # Smart \\n unescape: only for single-line strings (avoids damaging regex patterns
    # in already-multiline code) — same logic as execute_code v3.5.5
    if "\n" not in code:
        code_text = code.replace("\\n", "\n").replace("\\t", "\t")
    else:
        code_text = code

    # Python auto-fix (same pipeline as execute_code)
    if lang == "python":
        code_text = _python_code_autofix(code_text)

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code_text)

    # Update manifest
    manifest = _load_utils_manifest()
    manifest[safe_name] = {
        "name": safe_name,
        "lang": lang,
        "description": description,
        "path": script_path,
        "created": int(time.time()),
    }
    _save_utils_manifest(manifest)

    return f"utility '{safe_name}' saved ({lang}, {len(code_text)} bytes)\npath: {script_path}\nrun: util_run {safe_name}"


def util_run(name: str, args: str = "", timeout_s: int = 15) -> str:
    """Run a saved utility by name."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name).lower()
    manifest = _load_utils_manifest()

    if safe_name not in manifest:
        # Fuzzy match: substring, prefix overlap, or description match
        match = None
        for k in manifest:
            # Exact substring
            if safe_name in k or k in safe_name:
                match = k
                break
            # Shared prefix (at least 4 chars)
            min_len = min(len(safe_name), len(k))
            shared = 0
            for i in range(min_len):
                if safe_name[i] == k[i]:
                    shared += 1
                else:
                    break
            if shared >= 4:
                match = k
                break
        if not match:
            # Try matching against descriptions
            for k, v in manifest.items():
                desc = v.get("description", "").lower()
                if safe_name in desc or any(w in desc for w in safe_name.split("_") if len(w) > 2):
                    match = k
                    break
        if match:
            safe_name = match
            logger.info(f"Fuzzy matched util '{name}' -> '{safe_name}'")
        else:
            available = ", ".join(manifest.keys()) if manifest else "(none)"
            return f"error: utility '{safe_name}' not found. available: {available}"

    info = manifest[safe_name]
    script_path = info.get("path", "")
    lang = info.get("lang", "python")

    if not os.path.exists(script_path):
        return f"error: script file missing: {script_path}"

    runner = ["bash"] if lang == "bash" else ["python3"]
    cmd = runner + [script_path]
    if args:
        if isinstance(args, list):
            cmd.extend(str(a) for a in args)
        elif isinstance(args, dict):
            cmd.extend(str(v) for v in args.values())
        else:
            import shlex
            cmd.extend(shlex.split(str(args)))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout_s,
            cwd=os.path.join(MACHINA_ROOT, "work"),
        )
        output = result.stdout[:4000]
        if result.returncode != 0 and result.stderr:
            output += f"\n[stderr] {result.stderr[:1000]}"
        if not output.strip():
            output = f"(exit code: {result.returncode}, no output)"
        return output
    except subprocess.TimeoutExpired:
        return f"error: utility '{safe_name}' timed out ({timeout_s}s)"
    except Exception as e:
        return f"error running utility: {e}"


def util_list() -> str:
    """List all saved utilities."""
    manifest = _load_utils_manifest()
    if not manifest:
        return "no saved utilities yet. use 'util_save' to create one."

    lines = [f"saved utilities ({len(manifest)}):"]
    for name, info in manifest.items():
        desc = info.get("description", "")
        lang = info.get("lang", "?")
        lines.append(f"  - {name} ({lang}){': ' + desc if desc else ''}")
    return "\n".join(lines)


def util_delete(name: str) -> str:
    """Delete a saved utility by name (removes from manifest + deletes file)."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name).lower()
    manifest = _load_utils_manifest()
    if safe_name not in manifest:
        available = ", ".join(manifest.keys()) if manifest else "(none)"
        return f"error: utility '{safe_name}' not found. available: {available}"

    info = manifest[safe_name]
    script_path = info.get("path", "")

    # Remove from manifest
    del manifest[safe_name]
    _save_utils_manifest(manifest)

    # Delete file
    if script_path and os.path.exists(script_path):
        os.remove(script_path)
        return f"utility '{safe_name}' deleted (file + manifest entry removed)"
    return f"utility '{safe_name}' removed from manifest (file was already missing)"


def util_update(name: str, code: str = "", description: str = "") -> str:
    """Update an existing utility's code and/or description."""
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name).lower()
    manifest = _load_utils_manifest()
    if safe_name not in manifest:
        return f"error: utility '{safe_name}' not found"

    info = manifest[safe_name]
    script_path = info.get("path", "")
    lang = info.get("lang", "python")

    if code:
        if isinstance(code, list):
            code = "\n".join(str(line) for line in code)
        elif not isinstance(code, str):
            code = str(code)
        if "\n" not in code:
            code = code.replace("\\n", "\n").replace("\\t", "\t")
        if lang == "python":
            code = _python_code_autofix(code)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

    if description:
        info["description"] = description
    info["updated"] = int(time.time())
    manifest[safe_name] = info
    _save_utils_manifest(manifest)

    parts = []
    if code:
        parts.append(f"code updated ({len(code)} bytes)")
    if description:
        parts.append(f"description updated")
    return f"utility '{safe_name}' updated: {', '.join(parts)}"


# ---------------------------------------------------------------------------
# File ops, project/pip management, web/HTTP, tool/goal loading — re-exported
# from machina_tools_fileops for backward compatibility.
# ---------------------------------------------------------------------------
from machina_tools_fileops import (  # noqa: F401, E402
    run_machina_goal,
    run_machina_http_get,
    web_search,
    _sandbox_read_path,
    _sandbox_write_path,
    file_list,
    file_search,
    file_diff,
    file_edit,
    file_append,
    file_delete,
    project_create,
    project_build,
    pip_install,
    pip_uninstall,
    pip_list,
    load_available_tools_and_goals,
)
