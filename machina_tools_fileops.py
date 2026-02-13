#!/usr/bin/env python3
"""Machina Tools — File ops, project/pip management, web/HTTP, tool/goal loading."""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse

from pathlib import Path

from machina_shared import (
    MACHINA_ROOT,
    MEM_DIR,
    MANIFEST_PATH,
)

logger = logging.getLogger(__name__)

# --- Constants ---
SEARCH_ENGINE_URL = os.getenv("MACHINA_SEARCH_URL", "https://html.duckduckgo.com/html/?q={query}")


# ---------------------------------------------------------------------------
# Machina engine goal runner + HTTP fetch + web search
# ---------------------------------------------------------------------------

def run_machina_goal(goal_id: str, inputs: dict = None, tags: list = None) -> str:
    """Run a Machina engine goal via CLI subprocess."""
    if inputs is None:
        inputs = {}
    if tags is None:
        tags = ["tag.meta"]

    req = {
        "goal_id": goal_id,
        "candidate_tags": tags,
        "control_mode": "FALLBACK_ONLY",
        "inputs": inputs,
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir='/tmp') as f:
        json.dump(req, f)
        req_path = f.name

    try:
        cli_path = os.path.join(MACHINA_ROOT, "build", "machina_cli")
        result = subprocess.run(
            [cli_path, "run", req_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=MACHINA_ROOT,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            output += "\n" + result.stderr.strip()
        return output[:3000]
    except Exception as e:
        return f"엔진 에러: {e}"
    finally:
        try:
            os.unlink(req_path)
        except OSError as e:
            logger.debug(f"OSError: {e}")
            pass


def run_machina_http_get(url: str, raw: bool = False) -> str:
    """Fetch URL via curl, then extract readable text (HTML->text)."""
    try:
        result = subprocess.run(
            ["curl", "-sSL", "--max-time", "8", "-A", "Mozilla/5.0", "--", url],
            capture_output=True, text=True, timeout=12,
        )
        if result.returncode != 0:
            return "curl error: " + result.stderr[:200]
        html = result.stdout
        if raw or not html.strip():
            return html[:8000]
        # HTML -> readable text extraction
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            # Remove script, style, nav, footer, header noise
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            # Collapse excessive blank lines
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            clean = "\n".join(lines)
            return clean[:6000] if clean else "(no text content)"
        except ImportError:
            logger.warning("bs4 not available, returning raw HTML")
            return html[:8000]
    except Exception as e:
        return "HTTP error: " + str(e)


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using duckduckgo-search library with curl fallback."""
    # --- Primary: ddgs (formerly duckduckgo-search) library ---
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results, region="wt-wt"))
        if raw:
            results = []
            for i, r in enumerate(raw, 1):
                title = r.get("title", "")
                body = r.get("body", "")
                href = r.get("href", "")
                results.append(str(i) + ". " + title + "\n   " + body[:200] + "\n   " + href)
            return "\n\n".join(results)
    except Exception as e:
        logger.warning("DDGS library search failed: " + str(e) + ", trying curl fallback")

    # --- Fallback: curl-based HTML scraping ---
    try:
        encoded = urllib.parse.quote(query)
        search_url = SEARCH_ENGINE_URL.replace("{query}", encoded)
        html = run_machina_http_get(search_url)

        results = []
        for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'class="result__snippet"[^>]*>(.*?)</(?:td|div)',
            html, re.DOTALL
        ):
            href, title, body = m.group(1), m.group(2), m.group(3)
            title = re.sub(r'<[^>]+>', '', title).strip()
            body = re.sub(r'<[^>]+>', '', body).strip()
            if "uddg=" in href:
                href = urllib.parse.unquote(href.split("uddg=")[-1].split("&")[0])
            results.append(str(len(results)+1) + ". " + title + "\n   " + body + "\n   " + href)
            if len(results) >= max_results:
                break
        if results:
            return "\n\n".join(results)
    except Exception as e:
        logger.warning("Curl fallback search also failed: " + str(e))

    return "검색 결과를 가져오지 못했어. 다른 키워드로 시도해볼래?"


# ---------------------------------------------------------------------------
# File Navigation & Editing Tools (AID.FILE.LIST/SEARCH/DIFF/EDIT/APPEND)
# ---------------------------------------------------------------------------

def _sandbox_read_path(path: str) -> str:
    """Resolve and validate path is under MACHINA_ROOT (read-only access)."""
    if not os.path.isabs(path):
        path = os.path.join(MACHINA_ROOT, path)
    real = os.path.realpath(path)
    root = os.path.realpath(MACHINA_ROOT)
    if not (real.startswith(root + os.sep) or real == root):
        raise PermissionError(f"path outside sandbox: {real}")
    return real


def _sandbox_write_path(path: str) -> str:
    """Resolve and validate path is under MACHINA_ROOT/work (write access)."""
    if not os.path.isabs(path):
        if not path.startswith("work/") and not path.startswith("work\\"):
            path = os.path.join("work", path)
        path = os.path.join(MACHINA_ROOT, path)
    real = os.path.realpath(path)
    safe = os.path.realpath(os.path.join(MACHINA_ROOT, "work"))
    if not (real.startswith(safe + os.sep) or real == safe):
        raise PermissionError(f"write path outside work/: {real}")
    return real


def file_list(path: str, max_items: int = 100) -> str:
    """List directory contents with metadata (AID.FILE.LIST.v1)."""
    try:
        real = _sandbox_read_path(path)
    except PermissionError as e:
        return f"error: {e}"
    if not os.path.isdir(real):
        return f"error: not a directory: {real}"
    try:
        entries = []
        with os.scandir(real) as it:
            for entry in it:
                if len(entries) >= max_items:
                    break
                try:
                    st = entry.stat(follow_symlinks=False)
                    etype = "dir" if entry.is_dir() else ("link" if entry.is_symlink() else "file")
                    entries.append({
                        "name": entry.name,
                        "type": etype,
                        "size": st.st_size,
                        "mtime": int(st.st_mtime),
                    })
                except OSError:
                    entries.append({"name": entry.name, "type": "?", "size": 0, "mtime": 0})
        # Sort: dirs first, then files, alphabetical
        entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))
        lines = [f"{'type':4s} {'size':>8s} {'name'}"]
        for e in entries:
            sz = str(e["size"]) if e["type"] == "file" else "-"
            prefix = "d" if e["type"] == "dir" else ("l" if e["type"] == "link" else "f")
            lines.append(f"{prefix:4s} {sz:>8s} {e['name']}")
        if len(entries) >= max_items:
            lines.append(f"... (truncated at {max_items})")
        return "\n".join(lines)
    except Exception as e:
        return f"error: {e}"


def file_search(root: str, pattern: str, ext_filter: str = "", max_results: int = 50) -> str:
    """Search file contents with regex (AID.FILE.SEARCH.v1)."""
    try:
        real_root = _sandbox_read_path(root)
    except PermissionError as e:
        return f"error: {e}"
    if not os.path.isdir(real_root):
        return f"error: not a directory: {real_root}"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"error: invalid regex: {e}"
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "build"}
    results = []
    for dirpath, dirs, files in os.walk(real_root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fname in files:
            if ext_filter and not fname.endswith(ext_filter):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                if os.path.getsize(fpath) > 1_048_576:  # skip files > 1MB
                    continue
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if regex.search(line):
                            rel = os.path.relpath(fpath, real_root)
                            results.append(f"{rel}:{i}:{line.rstrip()}")
                            if len(results) >= max_results:
                                break
            except (OSError, UnicodeDecodeError):
                continue
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break
    if not results:
        return "no matches found"
    out = "\n".join(results)
    if len(results) >= max_results:
        out += f"\n... (truncated at {max_results})"
    return out


def file_diff(path1: str, path2: str, context: int = 3) -> str:
    """Unified diff between two files (AID.FILE.DIFF.v1)."""
    import difflib
    try:
        real1 = _sandbox_read_path(path1)
        real2 = _sandbox_read_path(path2)
    except PermissionError as e:
        return f"error: {e}"
    try:
        with open(real1, "r", encoding="utf-8", errors="replace") as f:
            lines1 = f.readlines()
        with open(real2, "r", encoding="utf-8", errors="replace") as f:
            lines2 = f.readlines()
    except FileNotFoundError as e:
        return f"error: {e}"
    diff = difflib.unified_diff(lines1, lines2, fromfile=path1, tofile=path2, n=context)
    result = "".join(diff)
    if not result:
        return "no differences found"
    if len(result) > 4000:
        result = result[:4000] + "\n... (diff truncated)"
    return result


def file_edit(path: str, operation: str, line: int, content: str = "") -> str:
    """Line-based file edit: replace/insert/delete (AID.FILE.EDIT.v1)."""
    try:
        real = _sandbox_write_path(path)
    except PermissionError as e:
        return f"error: {e}"
    if not os.path.exists(real):
        return f"error: file not found: {real}"
    try:
        with open(real, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        return f"error reading file: {e}"
    line = int(line)
    if operation == "replace":
        if line < 1 or line > len(lines):
            return f"error: line {line} out of range (1-{len(lines)})"
        old = lines[line - 1].rstrip("\n")
        lines[line - 1] = content + "\n"
        # Backup
        shutil.copy2(real, real + ".bak")
        with open(real, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"replaced line {line}: '{old}' -> '{content}'"
    elif operation == "insert":
        if line < 1 or line > len(lines) + 1:
            return f"error: line {line} out of range (1-{len(lines) + 1})"
        lines.insert(line - 1, content + "\n")
        shutil.copy2(real, real + ".bak")
        with open(real, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"inserted at line {line}: '{content}'"
    elif operation == "delete":
        if line < 1 or line > len(lines):
            return f"error: line {line} out of range (1-{len(lines)})"
        removed = lines.pop(line - 1).rstrip("\n")
        shutil.copy2(real, real + ".bak")
        with open(real, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return f"deleted line {line}: '{removed}'"
    else:
        return f"error: unknown operation '{operation}' (use replace/insert/delete)"


def file_append(path: str, content: str) -> str:
    """Append content to end of file (AID.FILE.APPEND.v1)."""
    try:
        real = _sandbox_write_path(path)
    except PermissionError as e:
        return f"error: {e}"
    os.makedirs(os.path.dirname(real), exist_ok=True)
    try:
        with open(real, "a", encoding="utf-8") as f:
            f.write(content)
        return f"appended {len(content)} bytes to {real}"
    except Exception as e:
        return f"error: {e}"


def file_delete(path: str, recursive: bool = False) -> str:
    """Delete file or directory in work/ (AID.FILE.DELETE.v1). Moves to .trash/ first."""
    try:
        real = _sandbox_write_path(path)
    except PermissionError as e:
        return f"error: {e}"
    if not os.path.exists(real):
        return f"error: path not found: {real}"
    # Create .trash for recovery
    trash_base = os.path.join(os.path.realpath(MACHINA_ROOT), "work", ".trash")
    os.makedirs(trash_base, exist_ok=True)
    basename = os.path.basename(real)
    trash_dst = os.path.join(trash_base, f"{basename}.{int(time.time())}")
    try:
        shutil.move(real, trash_dst)
        kind = "directory" if os.path.isdir(trash_dst) else "file"
        return f"deleted {kind}: {real} (recoverable: {trash_dst})"
    except Exception as e:
        return f"error: {e}"


def project_create(name: str, lang: str, files: list) -> str:
    """Create multi-file project (AID.PROJECT.CREATE.v1)."""
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$', name):
        return "error: invalid project name (alphanumeric + underscore only)"
    if lang == "cpp":
        base = os.path.join(MACHINA_ROOT, "toolpacks", "runtime_genesis", "src", name)
    elif lang == "python":
        base = os.path.join(MACHINA_ROOT, "work", "projects", name)
    else:
        return f"error: unsupported lang '{lang}' (use cpp or python)"
    real_root = os.path.realpath(MACHINA_ROOT)
    base_real = os.path.realpath(base)
    if not base_real.startswith(real_root + os.sep):
        return f"error: project path escapes sandbox"
    os.makedirs(base, exist_ok=True)
    created = []
    for fspec in files:
        rel = fspec.get("path", "")
        content = fspec.get("content", "")
        if not rel or ".." in rel:
            continue
        fpath = os.path.join(base, rel)
        fpath_real = os.path.realpath(fpath)
        if not fpath_real.startswith(base_real + os.sep) and fpath_real != base_real:
            continue
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)
        created.append(rel)
    # For Python projects, create __init__.py if missing
    if lang == "python":
        init_path = os.path.join(base, "__init__.py")
        if not os.path.exists(init_path):
            with open(init_path, "w") as f:
                f.write(f'"""Project {name}."""\n')
            created.append("__init__.py")
    return json.dumps({"ok": True, "project": name, "lang": lang,
                        "base": base, "files": created}, ensure_ascii=False)


def project_build(name: str, lang: str = "cpp",
                   build_type: str = "shared") -> str:
    """Build a multi-file C++ project (AID.PROJECT.BUILD.v1)."""
    if lang != "cpp":
        return "error: build only supported for cpp projects"
    src_dir = os.path.join(MACHINA_ROOT, "toolpacks", "runtime_genesis", "src", name)
    if not os.path.isdir(src_dir):
        return f"error: project not found: {src_dir}"
    # Collect all .cpp files
    sources = []
    for f in sorted(os.listdir(src_dir)):
        if f.endswith(".cpp") or f.endswith(".cc"):
            sources.append(os.path.join(src_dir, f))
    if not sources:
        return f"error: no .cpp/.cc files in {src_dir}"
    include_dir = os.path.join(MACHINA_ROOT, "core", "include")
    plugin_dir = os.path.join(MACHINA_ROOT, "toolpacks", "runtime_plugins")
    os.makedirs(plugin_dir, exist_ok=True)
    if build_type == "shared":
        out_path = os.path.join(plugin_dir, f"{name}.so")
        cmd = ["g++", "-shared", "-fPIC", "-std=c++2a", "-O2",
               f"-I{include_dir}", f"-I{src_dir}",
               "-o", out_path] + sources
    else:
        out_path = os.path.join(plugin_dir, name)
        cmd = ["g++", "-std=c++2a", "-O2",
               f"-I{include_dir}", f"-I{src_dir}",
               "-o", out_path] + sources
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return f"build error:\n{result.stderr[:2000]}"
        return json.dumps({"ok": True, "output": out_path,
                            "sources": len(sources), "type": build_type})
    except subprocess.TimeoutExpired:
        return "error: build timed out (120s)"
    except Exception as e:
        return f"error: {e}"


def pip_install(packages: list, venv_name: str = "default") -> str:
    """Install Python packages in isolated venv (AID.SYSTEM.PIP_INSTALL.v1)."""
    if not packages:
        return "error: no packages specified"
    if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', venv_name):
        return "error: invalid venv name"
    # Validate package names (block shell injection)
    for pkg in packages:
        if not re.match(r'^[a-zA-Z0-9_.-]+(\[.*\])?(==|>=|<=|~=|!=)?[a-zA-Z0-9_.*-]*$', pkg):
            return f"error: invalid package spec: {pkg}"
    venv_dir = os.path.join(MACHINA_ROOT, "work", "venvs", venv_name)
    # Create venv if not exists
    if not os.path.isdir(venv_dir):
        try:
            result = subprocess.run(
                ["python3", "-m", "venv", venv_dir],
                capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                return f"error creating venv: {result.stderr[:500]}"
        except Exception as e:
            return f"error: {e}"
    pip_path = os.path.join(venv_dir, "bin", "pip")
    if not os.path.exists(pip_path):
        return f"error: pip not found in venv: {venv_dir}"
    try:
        result = subprocess.run(
            [pip_path, "install", "--no-cache-dir"] + packages,
            capture_output=True, text=True, timeout=120,
            cwd=os.path.join(MACHINA_ROOT, "work"))
        if result.returncode != 0:
            return f"pip install error:\n{result.stderr[:1000]}"
        installed = [l for l in result.stdout.split("\n") if "Successfully" in l]
        return json.dumps({"ok": True, "venv": venv_dir,
                            "packages": packages,
                            "output": "\n".join(installed) if installed else "installed"})
    except subprocess.TimeoutExpired:
        return "error: pip install timed out (120s)"
    except Exception as e:
        return f"error: {e}"


def pip_uninstall(packages: list, venv_name: str = "default") -> str:
    """Uninstall Python packages from isolated venv (AID.SYSTEM.PIP_UNINSTALL.v1)."""
    if not packages:
        return "error: no packages specified"
    if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', venv_name):
        return "error: invalid venv name"
    for pkg in packages:
        if not re.match(r'^[a-zA-Z0-9_.-]+$', pkg):
            return f"error: invalid package name: {pkg}"
    venv_dir = os.path.join(MACHINA_ROOT, "work", "venvs", venv_name)
    if not os.path.isdir(venv_dir):
        return f"error: venv '{venv_name}' not found at {venv_dir}"
    pip_path = os.path.join(venv_dir, "bin", "pip")
    if not os.path.exists(pip_path):
        return f"error: pip not found in venv: {venv_dir}"
    try:
        result = subprocess.run(
            [pip_path, "uninstall", "-y"] + packages,
            capture_output=True, text=True, timeout=60,
            cwd=os.path.join(MACHINA_ROOT, "work"))
        if result.returncode != 0:
            return f"pip uninstall error:\n{result.stderr[:1000]}"
        return json.dumps({"ok": True, "venv": venv_dir,
                            "packages": packages,
                            "output": result.stdout.strip()[:1000]})
    except subprocess.TimeoutExpired:
        return "error: pip uninstall timed out (60s)"
    except Exception as e:
        return f"error: {e}"


def pip_list(venv_name: str = "default") -> str:
    """List installed packages in isolated venv (AID.SYSTEM.PIP_LIST.v1)."""
    if not re.match(r'^[a-zA-Z0-9_-]{1,64}$', venv_name):
        return "error: invalid venv name"
    venv_dir = os.path.join(MACHINA_ROOT, "work", "venvs", venv_name)
    if not os.path.isdir(venv_dir):
        return f"error: venv '{venv_name}' not found at {venv_dir}"
    pip_path = os.path.join(venv_dir, "bin", "pip")
    if not os.path.exists(pip_path):
        return f"error: pip not found in venv: {venv_dir}"
    try:
        result = subprocess.run(
            [pip_path, "list", "--format=json"],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.join(MACHINA_ROOT, "work"))
        if result.returncode != 0:
            return f"pip list error:\n{result.stderr[:500]}"
        pkgs = json.loads(result.stdout) if result.stdout.strip() else []
        return json.dumps({"ok": True, "venv": venv_dir,
                            "count": len(pkgs), "packages": pkgs})
    except subprocess.TimeoutExpired:
        return "error: pip list timed out (30s)"
    except Exception as e:
        return f"error: {e}"


# ---------------------------------------------------------------------------
# Tool/Goal manifest loading (used by telegram_bot at startup)
# ---------------------------------------------------------------------------
def load_available_tools_and_goals() -> tuple[list, list]:
    """Load tool/goal lists from Machina engine manifests. Returns (tools, goals)."""
    tools = []
    if MANIFEST_PATH.exists():
        try:
            with open(MANIFEST_PATH) as f:
                data = json.load(f)
            raw = data if isinstance(data, list) else data.get("tools", [])
            for t in raw:
                if not t.get("aid"):
                    continue
                info = {"aid": t["aid"], "name": t.get("name", ""),
                        "tags": t.get("tags", []),
                        "description": t.get("estimate_model", {}).get("notes", "")}
                schema = t.get("inputs_schema", {})
                props = schema.get("properties", {})
                req = schema.get("required", [])
                if props:
                    info["inputs"] = {k: {"type": v.get("type", "string"),
                                          "required": k in req} for k, v in props.items()}
                tools.append(info)
            logger.info(f"Loaded {len(tools)} tools from manifest")
        except Exception as e:
            logger.error(f"Failed to load tools manifest: {e}")

    goals = []
    goalpacks_dir = Path(MACHINA_ROOT) / "goalpacks"
    if goalpacks_dir.exists():
        for gp in goalpacks_dir.iterdir():
            mf = gp / "manifest.json"
            if mf.exists():
                try:
                    with open(mf) as f:
                        gdata = json.load(f)
                    for g in gdata.get("goals", []):
                        gid = g.get("goal_id", "")
                        if gid:
                            goals.append(gid)
                    if not gdata.get("goals") and gdata.get("goal_id"):
                        goals.append(gdata["goal_id"])
                except Exception as e:
                    logger.warning(f"{type(e).__name__}: {e}")
                    pass
    for g in ["goal.GENESIS_DEMO_HELLO.v1"]:
        if g not in goals:
            goals.append(g)
    logger.info(f"Loaded {len(goals)} goals: {goals}")
    return tools, goals
