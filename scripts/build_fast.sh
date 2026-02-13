#!/usr/bin/env bash
# Fast incremental build — prints the path to machina_cli on success.
set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_DIR="${1:-build}"
mkdir -p "$BUILD_DIR"
if command -v nproc >/dev/null 2>&1; then
  JOBS="$(nproc)"
elif command -v sysctl >/dev/null 2>&1; then
  JOBS="$(sysctl -n hw.ncpu)"
else
  JOBS=4
fi

cmake -S . -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD_DIR" -j"$JOBS"

echo "$BUILD_DIR/machina_cli"
