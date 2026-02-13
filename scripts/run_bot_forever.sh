#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${MACHINA_BOT_LOG:-/tmp/machina_bot.log}"
RETRY_DELAY="${MACHINA_BOT_RETRY_DELAY_S:-3}"
RETRY_MAX="${MACHINA_BOT_RETRY_MAX_DELAY_S:-30}"

SECRETS_FILE="${MACHINA_SECRETS_FILE:-$HOME/.config/machina/.secrets.env}"
if [[ -f "$SECRETS_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$SECRETS_FILE" || true
elif [[ -f "$ROOT/.secrets.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.secrets.env" || true
fi

# Optional (not included in some github_ready exports)
if [[ -f "$ROOT/machina_env.sh" ]]; then
  # shellcheck disable=SC1091
  set +u
  source "$ROOT/machina_env.sh" >/dev/null 2>&1 || true
  set -u
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  echo "[run_bot_forever] missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID" >&2
  exit 2
fi

mkdir -p "$(dirname "$LOG_FILE")"

echo "[run_bot_forever] root=$ROOT log=$LOG_FILE chat_id=$TELEGRAM_CHAT_ID"
echo "[run_bot_forever] retry=${RETRY_DELAY}s max=${RETRY_MAX}s"

while true; do
  {
    echo "[$(date '+%F %T')] launcher: starting telegram_bot.py"
  } >>"$LOG_FILE"

  (
    cd "$ROOT"
    exec env \
      TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
      TELEGRAM_CHAT_ID="$TELEGRAM_CHAT_ID" \
      MACHINA_BOT_RETRY_DELAY_S="$RETRY_DELAY" \
      MACHINA_BOT_RETRY_MAX_DELAY_S="$RETRY_MAX" \
      MACHINA_BOT_MAX_RETRIES="0" \
      python3 telegram_bot.py
  ) >>"$LOG_FILE" 2>&1 || true

  {
    echo "[$(date '+%F %T')] launcher: telegram_bot.py exited, restart in ${RETRY_DELAY}s"
  } >>"$LOG_FILE"

  sleep "$RETRY_DELAY"
done
