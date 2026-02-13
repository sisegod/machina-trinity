#!/usr/bin/env python3
"""Validate permission policy coverage against manifest side_effects.

Checks:
1) Every manifest tool AID is either explicitly mapped in DEFAULT_PERMISSIONS
   or inferable by side_effects policy.
2) Prints explicit-vs-inferred mismatches for review (non-fatal by default).
3) Exits non-zero only when manifest cannot be read.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from machina_permissions import DEFAULT_PERMISSIONS, _permission_from_side_effects


def main() -> int:
    manifest = ROOT / "toolpacks" / "tier0" / "manifest.json"
    if not manifest.exists():
        print(f"ERROR: manifest not found: {manifest}")
        return 2
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: manifest parse failed: {type(e).__name__}: {e}")
        return 2

    tools = data.get("tools", [])
    missing = []
    mismatches = []
    for t in tools:
        aid = t.get("aid", "")
        if not aid:
            continue
        side = set(t.get("side_effects", []))
        inferred = _permission_from_side_effects(side)
        explicit = DEFAULT_PERMISSIONS.get(aid)
        if explicit is None and inferred is None:
            missing.append(aid)
            continue
        if explicit is not None and explicit != inferred:
            mismatches.append((aid, explicit, inferred, sorted(side)))

    print(f"manifest_tools={len(tools)}")
    print(f"explicit_permissions={len([t for t in tools if t.get('aid') in DEFAULT_PERMISSIONS])}")
    print(f"inferred_only={len([t for t in tools if t.get('aid') not in DEFAULT_PERMISSIONS])}")
    if missing:
        print("MISSING_POLICY:")
        for aid in missing:
            print(f"  - {aid}")
    if mismatches:
        print("EXPLICIT_VS_INFERRED_MISMATCH:")
        for aid, ex, inf, side in mismatches:
            print(f"  - {aid}: explicit={ex} inferred={inf} side_effects={side}")
    print("OK: permission policy audit complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
