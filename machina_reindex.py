#!/usr/bin/env python3
"""Machina Memory Index Verifier & Rebuilder.

Usage:
    python3 machina_reindex.py                  # verify all streams
    python3 machina_reindex.py --fix            # fix corrupt lines
    python3 machina_reindex.py --stats          # show statistics
    python3 machina_reindex.py --stream skills  # verify single stream
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

MACHINA_ROOT = Path(os.getenv("MACHINA_ROOT", Path(__file__).parent))
MEM_DIR = MACHINA_ROOT / "work" / "memory"

STREAMS = {
    "experiences": "experiences.jsonl",
    "insights": "insights.jsonl",
    "skills": "skills.jsonl",
    "knowledge": "knowledge.jsonl",
    "entities": "entities.jsonl",
    "relations": "relations.jsonl",
}


def verify_stream(name: str, filename: str, fix: bool = False) -> dict:
    """Verify a JSONL stream. Returns stats dict."""
    fpath = MEM_DIR / filename
    if not fpath.exists():
        return {"name": name, "exists": False, "lines": 0, "corrupt": 0, "size_kb": 0}

    lines = 0
    corrupt = 0
    good_lines = []
    hashes = set()
    duplicates = 0

    with open(fpath, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            lines += 1
            try:
                obj = json.loads(line)
                good_lines.append(line)
                h = hashlib.md5(line.encode()).hexdigest()
                if h in hashes:
                    duplicates += 1
                hashes.add(h)
            except json.JSONDecodeError as e:
                corrupt += 1
                print(f"  [{name}] line {i}: CORRUPT — {e}")

    if fix and corrupt > 0:
        # Atomic rewrite: tmp file -> rename
        tmp_fd, tmp_path = tempfile.mkstemp(dir=MEM_DIR, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp:
                for gl in good_lines:
                    tmp.write(gl + "\n")
            # Backup original
            bak = fpath.with_suffix(".jsonl.bak")
            if fpath.exists():
                fpath.rename(bak)
            Path(tmp_path).rename(fpath)
            print(f"  [{name}] Fixed: {corrupt} corrupt lines removed (backup: {bak.name})")
        except Exception as e:
            print(f"  [{name}] Fix failed: {e}")
            os.unlink(tmp_path)

    size_kb = fpath.stat().st_size // 1024 if fpath.exists() else 0
    return {
        "name": name, "exists": True, "lines": lines,
        "corrupt": corrupt, "duplicates": duplicates, "size_kb": size_kb,
    }


def main():
    parser = argparse.ArgumentParser(description="Machina Memory Index Verifier")
    parser.add_argument("--fix", action="store_true", help="Fix corrupt lines (atomic rewrite)")
    parser.add_argument("--stats", action="store_true", help="Show detailed statistics")
    parser.add_argument("--stream", type=str, help="Verify single stream by name")
    args = parser.parse_args()

    if not MEM_DIR.exists():
        print(f"Memory directory not found: {MEM_DIR}")
        sys.exit(1)

    streams = (
        {args.stream: STREAMS[args.stream]}
        if args.stream and args.stream in STREAMS
        else STREAMS
    )

    print(f"Machina Memory {'Fix' if args.fix else 'Verify'}")
    print(f"Directory: {MEM_DIR}")
    print("-" * 60)

    results = []
    total_lines = 0
    total_corrupt = 0

    for name, filename in streams.items():
        r = verify_stream(name, filename, fix=args.fix)
        results.append(r)
        total_lines += r.get("lines", 0)
        total_corrupt += r.get("corrupt", 0)

    # Summary table
    print(f"\n{'Stream':<15} {'Lines':>8} {'Corrupt':>8} {'Dupes':>8} {'Size':>8}")
    print("-" * 55)
    for r in results:
        if not r["exists"]:
            print(f"{r['name']:<15} {'(missing)':>8}")
        else:
            print(
                f"{r['name']:<15} {r['lines']:>8} {r['corrupt']:>8}"
                f" {r.get('duplicates', 0):>8} {r['size_kb']:>6}KB"
            )

    print("-" * 55)
    print(f"{'TOTAL':<15} {total_lines:>8} {total_corrupt:>8}")

    if total_corrupt > 0 and not args.fix:
        print(f"\n{total_corrupt} corrupt lines found. Run with --fix to repair.")
        sys.exit(1)
    elif total_corrupt > 0 and args.fix:
        print(f"\n{total_corrupt} corrupt lines fixed.")
    else:
        print(f"\nAll {total_lines} lines valid.")


if __name__ == "__main__":
    main()
