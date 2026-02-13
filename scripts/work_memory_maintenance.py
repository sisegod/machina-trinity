#!/usr/bin/env python3
"""Rotate and compact work/memory JSONL streams safely.

Default is dry-run. Use --apply to perform writes.
"""

import argparse
import os
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MEM_DIR = Path(os.getenv("MACHINA_MEM_DIR", str(ROOT / "work" / "memory")))
DEFAULT_STREAMS = [
    "experiences",
    "insights",
    "skills",
    "telegram",
    "telegram_chat",
    "autonomic_audit",
]


def tail_lines(path: Path, keep_lines: int) -> list[str]:
    if keep_lines <= 0:
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return lines[-keep_lines:]


def run(
    apply_changes: bool,
    keep_lines: int,
    min_size_mb: int,
    streams: list[str],
    mem_dir: Path | None = None,
) -> int:
    target_dir = mem_dir if mem_dir is not None else MEM_DIR
    if not target_dir.exists():
        print(f"Skip: memory dir not found: {target_dir}")
        return 0

    archive_dir = target_dir / "archive" / time.strftime("%Y%m%d")
    changed = 0
    for stream in streams:
        path = target_dir / f"{stream}.jsonl"
        if not path.exists():
            continue
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb < float(min_size_mb):
            continue

        kept = tail_lines(path, keep_lines)
        print(f"[rotate] {path.name}: {size_mb:.1f}MB -> keep {len(kept)} lines")
        if not apply_changes:
            continue

        archive_dir.mkdir(parents=True, exist_ok=True)
        dst = archive_dir / f"{path.stem}.{int(time.time())}.jsonl"
        shutil.copy2(path, dst)
        with path.open("w", encoding="utf-8") as f:
            f.writelines(kept)
        changed += 1
        print(f"  archived: {dst}")

    mode = "APPLY" if apply_changes else "DRY-RUN"
    print(f"[done] mode={mode}, changed={changed}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="perform file changes")
    p.add_argument("--keep-lines", type=int, default=5000, help="lines to keep after rotation")
    p.add_argument("--min-size-mb", type=int, default=32, help="rotate only files >= this size")
    p.add_argument("--streams", nargs="*", default=DEFAULT_STREAMS, help="stream base names")
    args = p.parse_args()
    return run(args.apply, args.keep_lines, args.min_size_mb, args.streams)


if __name__ == "__main__":
    raise SystemExit(main())
