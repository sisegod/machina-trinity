#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${MACHINA_PROFILE:-dev}"
HOST="${MACHINA_SERVE_HOST:-127.0.0.1}"
PORT="${MACHINA_SERVE_PORT:-8091}"
STACK_ID="${MACHINA_STACK_ID:-machina-$(hostname)-$(date +%Y%m%d%H%M%S)}"
LAUNCHER="${MACHINA_OPS_LAUNCHER:-auto}"  # auto|nohup|systemd
FORCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --serve-host) HOST="$2"; shift 2 ;;
    --serve-port) PORT="$2"; shift 2 ;;
    --stack-id) STACK_ID="$2"; shift 2 ;;
    --launcher) LAUNCHER="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$ROOT/ops"

# Prefer external secrets file outside repo.
SECRETS_FILE="${MACHINA_SECRETS_FILE:-$HOME/.config/machina/.secrets.env}"
if [[ -f "$SECRETS_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$SECRETS_FILE" || true
elif [[ -f "$ROOT/.secrets.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.secrets.env" || true
fi
# shellcheck disable=SC1091
set +u
source "$ROOT/machina_env.sh" >/dev/null 2>&1 || true
set -u

export MACHINA_PROFILE="$PROFILE"
export MACHINA_STACK_ID="$STACK_ID"

CLI_BIN="${MACHINA_CLI_BIN:-}"
if [[ -z "$CLI_BIN" ]]; then
  if [[ -x "$ROOT/build/machina_cli" ]]; then
    CLI_BIN="$ROOT/build/machina_cli"
  elif command -v machina_cli >/dev/null 2>&1; then
    CLI_BIN="$(command -v machina_cli)"
  fi
fi
if [[ -z "$CLI_BIN" || ! -x "$CLI_BIN" ]]; then
  echo "[ops-start] machina_cli binary not found. expected \$MACHINA_CLI_BIN or $ROOT/build/machina_cli" >&2
  echo "[ops-start] build first: cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j\$(nproc)" >&2
  echo "missing_machina_cli" > "$ROOT/ops/start.error"
  exit 2
fi

if [[ "$FORCE" -ne 1 ]]; then
  if pgrep -f "$ROOT/build/machina_cli serve" >/dev/null 2>&1; then
    echo "[ops-start] machina serve already running; use --force or ops_restart.sh" >&2
    exit 1
  fi
  if pgrep -f "python3 $ROOT/telegram_bot.py|python3 telegram_bot.py" >/dev/null 2>&1; then
    echo "[ops-start] telegram_bot already running; use --force or ops_restart.sh" >&2
    exit 1
  fi
fi

SERVE_LOG="${MACHINA_SERVE_LOG:-/tmp/machina_serve.log}"
BOT_LOG="${MACHINA_BOT_LOG:-/tmp/machina_bot.log}"
USE_SYSTEMD=0
if [[ "$LAUNCHER" == "systemd" ]]; then
  USE_SYSTEMD=1
elif [[ "$LAUNCHER" == "auto" ]]; then
  if command -v systemd-run >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    USE_SYSTEMD=1
  fi
fi

if [[ "$USE_SYSTEMD" -eq 1 ]]; then
  systemctl --user stop machina-serve.service machina-bot.service >/dev/null 2>&1 || true

  systemd-run --user --unit machina-serve --collect \
    --working-directory="$ROOT" \
    /bin/bash -lc "set +u; source \"$ROOT/machina_env.sh\" >/dev/null 2>&1 || true; set -u; export MACHINA_STACK_ID=\"$STACK_ID\" MACHINA_INSTANCE_ROLE=\"serve\" MACHINA_SERVE_HOST=\"$HOST\" MACHINA_SERVE_PORT=\"$PORT\"; exec \"$CLI_BIN\" serve --host \"$HOST\" --port \"$PORT\"" >/dev/null
  systemd-run --user --unit machina-bot --collect \
    --working-directory="$ROOT" \
    /bin/bash -lc "set +u; source \"$ROOT/machina_env.sh\" >/dev/null 2>&1 || true; set -u; export MACHINA_STACK_ID=\"$STACK_ID\" MACHINA_INSTANCE_ROLE=\"telegram_bot\"; exec python3 \"$ROOT/telegram_bot.py\"" >/dev/null

  sleep 1
  SERVE_PID="$(systemctl --user show -p MainPID --value machina-serve.service 2>/dev/null || echo 0)"
  BOT_PID="$(systemctl --user show -p MainPID --value machina-bot.service 2>/dev/null || echo 0)"
  if [[ -z "$SERVE_PID" || "$SERVE_PID" == "0" || -z "$BOT_PID" || "$BOT_PID" == "0" ]]; then
    echo "[ops-start] systemd launch failed (MainPID not found)" >&2
    exit 1
  fi
else
  nohup env \
    MACHINA_STACK_ID="$STACK_ID" \
    MACHINA_INSTANCE_ROLE="serve" \
    MACHINA_SERVE_HOST="$HOST" \
    MACHINA_SERVE_PORT="$PORT" \
    "$CLI_BIN" serve --host "$HOST" --port "$PORT" \
    >"$SERVE_LOG" 2>&1 &
  SERVE_PID=$!

  nohup env \
    MACHINA_STACK_ID="$STACK_ID" \
    MACHINA_INSTANCE_ROLE="telegram_bot" \
    python3 "$ROOT/telegram_bot.py" \
    >"$BOT_LOG" 2>&1 &
  BOT_PID=$!

  sleep 1
fi

python3 - "$ROOT/ops/pids.started.json" "$STACK_ID" "$SERVE_PID" "$BOT_PID" "$HOST" "$PORT" "$SERVE_LOG" "$BOT_LOG" <<'PY'
import json, sys, time
out = sys.argv[1]
payload = {
    "ts": int(time.time()),
    "stack_id": sys.argv[2],
    "serve_pid": int(sys.argv[3]),
    "bot_pid": int(sys.argv[4]),
    "serve_host": sys.argv[5],
    "serve_port": int(sys.argv[6]),
    "serve_log": sys.argv[7],
    "bot_log": sys.argv[8],
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
PY

echo "[ops-start] stack_id=$STACK_ID serve_pid=$SERVE_PID bot_pid=$BOT_PID port=$PORT"
