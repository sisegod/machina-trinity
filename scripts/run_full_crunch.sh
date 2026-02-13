#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Load local toolchain hints (e.g., json-c pkg-config path) when available.
if [[ -f "$ROOT/machina_env.sh" ]]; then
  # shellcheck disable=SC1091
  set +u
  source "$ROOT/machina_env.sh" >/dev/null 2>&1 || true
  set -u
fi

echo "[crunch] phase 1/2: python guardrails"
scripts/run_guardrails.sh

echo "[crunch] phase 2/2: cpp build/test (best-effort)"
if ! command -v pkg-config >/dev/null 2>&1; then
  echo "[crunch] skip cpp: pkg-config not found"
  exit 0
fi
if ! pkg-config --exists json-c; then
  echo "[crunch] skip cpp: json-c not installed"
  exit 0
fi

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON
cmake --build build -j"$(nproc)"
ctest --output-on-failure --test-dir build

echo "[crunch] all phases passed"
