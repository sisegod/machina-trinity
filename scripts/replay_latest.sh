#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cli=$(./scripts/build_fast.sh)
latest=$(ls -1t logs/run_*.jsonl 2>/dev/null | head -n 1 || true)
if [ -z "${latest}" ]; then
  echo "no log found. run demo first." >&2
  exit 1
fi
"$cli" replay "$latest"
