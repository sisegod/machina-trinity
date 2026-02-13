#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
latest=$(ls -1t logs/run_*.jsonl 2>/dev/null | head -n 1 || true)
if [ -z "$latest" ]; then
  echo "no log found. run demo first." >&2
  exit 1
fi
CLI=$(./scripts/build_fast.sh)
$CLI replay_strict examples/run_request.error_scan.json "$latest"
