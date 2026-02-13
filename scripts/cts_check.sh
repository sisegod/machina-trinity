#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cli=$(./scripts/build_fast.sh)
"$cli" cts toolpacks/tier0/manifest.json goalpacks/error_scan/manifest.json
