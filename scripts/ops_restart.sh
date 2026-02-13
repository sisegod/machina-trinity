#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
TERM_SECONDS="${MACHINA_OPS_TERM_TIMEOUT_SEC:-8}"
INCLUDE_OLLAMA=0
HOST="${MACHINA_SERVE_HOST:-127.0.0.1}"
PORT="${MACHINA_SERVE_PORT:-8091}"
STACK_ID="${MACHINA_STACK_ID:-machina-$(hostname)-$(date +%Y%m%d%H%M%S)}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --term-seconds) TERM_SECONDS="$2"; shift 2 ;;
    --include-ollama) INCLUDE_OLLAMA=1; shift ;;
    --serve-host) HOST="$2"; shift 2 ;;
    --serve-port) PORT="$2"; shift 2 ;;
    --stack-id) STACK_ID="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$ROOT/ops"

echo "[ops-restart] detect"
"$ROOT/scripts/ops_detect.sh" --json-out "$ROOT/ops/pids.current.json"

echo "[ops-restart] kill"
KILL_ARGS=(--from "$ROOT/ops/pids.current.json" --term-seconds "$TERM_SECONDS")
if [[ "$INCLUDE_OLLAMA" -eq 1 ]]; then
  KILL_ARGS+=(--include-ollama)
fi
if [[ "$DRY_RUN" -eq 1 ]]; then
  KILL_ARGS+=(--dry-run)
fi
"$ROOT/scripts/ops_kill.sh" "${KILL_ARGS[@]}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[ops-restart] dry-run done"
  exit 0
fi

echo "[ops-restart] start"
if ! "$ROOT/scripts/ops_start.sh" --serve-host "$HOST" --serve-port "$PORT" --stack-id "$STACK_ID" --force; then
  echo "[ops-restart] start failed" >&2
  exit 1
fi

echo "[ops-restart] healthcheck"
"$ROOT/scripts/ops_healthcheck.sh" --serve-url "http://${HOST}:${PORT}" --json-out "$ROOT/ops/health.report.json"

python3 - "$ROOT/ops/health.report.json" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p, "r", encoding="utf-8"))
if not data.get("ok"):
    print("[ops-restart] healthcheck failed", file=sys.stderr)
    raise SystemExit(1)
print("[ops-restart] healthcheck ok")
PY
