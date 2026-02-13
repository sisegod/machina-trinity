#!/usr/bin/env bash
# Fast incremental build — prints the path to machina_cli on success.
set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_DIR="${1:-build}"
mkdir -p "$BUILD_DIR"
cmake -S . -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release 2>/dev/null || true
cmake --build "$BUILD_DIR" -j"$(nproc)" >/dev/null 2>&1

echo "$BUILD_DIR/machina_cli"
