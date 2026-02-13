#!/usr/bin/env python3
"""Validate AID references found in docs/README against known canonical AIDs."""

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "toolpacks" / "tier0" / "manifest.json"
REGISTRY = ROOT / "machina_dispatch_registry.py"
TARGETS = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]

_AID_LITERAL_RE = re.compile(r"(AID\.[A-Z0-9_.]+\.v\d+)")
_REGISTRY_CONST_RE = re.compile(r"^\s*AID_[A-Z0-9_]+\s*=\s*['\"](AID\.[A-Z0-9_.]+\.v\d+)['\"]")
_ALLOWED_UNKNOWN = {
    "AID.RUNTIME.POLICY_ECHO.v1",  # policy driver tutorial runtime sample
    "AID.XX.YY.v1",                # ipc_schema placeholder sample
}


def load_known_aids(manifest_path: Path = MANIFEST, registry_path: Path = REGISTRY) -> set[str]:
    known: set[str] = set()
    with manifest_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for t in data.get("tools", []):
        aid = t.get("aid", "")
        if aid:
            known.add(aid)
    with registry_path.open("r", encoding="utf-8") as f:
        for line in f:
            m = _REGISTRY_CONST_RE.search(line)
            if m:
                known.add(m.group(1))
    return known


def collect_unknown_doc_aids(
    targets: list[Path],
    known: set[str],
    allowed_unknown: set[str] | None = None,
) -> list[tuple[Path, str]]:
    allow = allowed_unknown if allowed_unknown is not None else _ALLOWED_UNKNOWN
    unknown: list[tuple[Path, str]] = []
    seen = set()
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for aid in _AID_LITERAL_RE.findall(text):
            if aid.startswith("AID.MCP."):
                continue
            if aid in allow:
                continue
            key = (str(path), aid)
            if key in seen:
                continue
            seen.add(key)
            if aid not in known:
                unknown.append((path, aid))
    return unknown


def main() -> int:
    known = load_known_aids()
    unknown = collect_unknown_doc_aids(TARGETS, known)
    ref_count = 0
    refs_seen = set()
    for path in TARGETS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for aid in _AID_LITERAL_RE.findall(text):
            if aid.startswith("AID.MCP."):
                continue
            if aid in _ALLOWED_UNKNOWN:
                continue
            key = (str(path), aid)
            if key in refs_seen:
                continue
            refs_seen.add(key)
            ref_count += 1

    if unknown:
        print("Unknown doc AID references found:")
        for path, aid in sorted(unknown, key=lambda x: (str(x[0]), x[1])):
            print(f"- {path.relative_to(ROOT)}: {aid}")
        return 1

    print(f"OK: validated {ref_count} doc AID references against {len(known)} known AIDs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
