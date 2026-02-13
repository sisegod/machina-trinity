#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/github_ready"

mkdir -p "$OUT"

rsync -a --delete "$ROOT/" "$OUT/" \
  --exclude 'github_ready/' \
  --exclude '.git/' \
  --exclude 'build/' \
  --exclude 'logs/' \
  --exclude 'work/' \
  --exclude '__pycache__/' \
  --exclude '*/__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'machina_env.sh' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.secrets.env' \
  --exclude 'ops/' \
  --exclude 'README_new.md' \
  --exclude 'docs/CLAUDE_TO_CODEX_SKILL_PLAN.md' \
  --exclude 'docs/PROJECT_DEEP_DIVE_2026-02-13.md' \
  --exclude 'docs/REPO_AUDIT_2026-02-13.md'

# Purge generated runtime plugin artifacts and local pending paths.
find "$OUT/toolpacks/runtime_plugins" -type f ! -name '.gitkeep' -delete 2>/dev/null || true

# Purge Python cache artifacts defensively.
find "$OUT" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$OUT" -type f -name '*.pyc' -delete 2>/dev/null || true

echo "[ok] exported clean GitHub package to: $OUT"
