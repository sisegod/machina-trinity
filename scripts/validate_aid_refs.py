#!/usr/bin/env python3
"""Validate AID references used in Python/policies against known canonical AIDs.

Known set:
- toolpacks/tier0/manifest.json tool AIDs
- Python dispatch AIDs declared in machina_dispatch_registry.py constants
"""

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "toolpacks" / "tier0" / "manifest.json"
REGISTRY = ROOT / "machina_dispatch_registry.py"
SCAN_DIRS = ("policies",)
SCAN_FILES = (
    "telegram_bot.py",
    "telegram_bot_handlers.py",
    "telegram_bot_pulse.py",
    "machina_dispatch.py",
    "machina_dispatch_exec.py",
    "machina_dispatch_registry.py",
    "machina_learning.py",
    "machina_tools.py",
)

_AID_LITERAL_RE = re.compile(r"['\"](AID\.[A-Z0-9_.]+\.v\d+)['\"]")
_REGISTRY_CONST_RE = re.compile(r"^\s*AID_[A-Z0-9_]+\s*=\s*['\"](AID\.[A-Z0-9_.]+\.v\d+)['\"]")
_ALLOWED_LEGACY = {
    "AID.GPU.SMOKE.v1",
    "AID.GPU.METRICS.v1",
    "AID.NET.SEARCH.v1",
    "AID.GENESIS.RUN.v1",
}


def load_manifest_aids() -> set[str]:
    with MANIFEST.open("r", encoding="utf-8") as f:
        data = json.load(f)
    tools = data.get("tools", [])
    return {t.get("aid", "") for t in tools if isinstance(t, dict) and t.get("aid")}


def load_python_registry_aids() -> set[str]:
    aids: set[str] = set()
    with REGISTRY.open("r", encoding="utf-8") as f:
        for line in f:
            m = _REGISTRY_CONST_RE.search(line)
            if m:
                aids.add(m.group(1))
    return aids


def iter_scan_targets():
    for rel in SCAN_FILES:
        p = ROOT / rel
        if p.exists():
            yield p
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            yield p


def main() -> int:
    known = load_manifest_aids() | load_python_registry_aids()
    unknown: list[tuple[Path, str]] = []
    seen = set()

    for path in iter_scan_targets():
        txt = path.read_text(encoding="utf-8", errors="replace")
        for aid in _AID_LITERAL_RE.findall(txt):
            if aid.startswith("AID.MCP."):
                continue
            if aid in _ALLOWED_LEGACY:
                continue
            key = (str(path), aid)
            if key in seen:
                continue
            seen.add(key)
            if aid not in known:
                unknown.append((path, aid))

    if unknown:
        print("Unknown AID references found:")
        for path, aid in sorted(unknown, key=lambda x: (str(x[0]), x[1])):
            rel = path.relative_to(ROOT)
            print(f"- {rel}: {aid}")
        return 1

    print(f"OK: validated {len(seen)} AID references against {len(known)} known AIDs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
