#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_FILE="${MACHINA_SECRETS_FILE:-$HOME/.config/machina/.secrets.env}"

ok()   { echo "[OK] $*"; }
warn() { echo "[WARN] $*"; }
fail() { echo "[FAIL] $*"; exit 1; }

echo "[doctor] root=$ROOT"

command -v python3 >/dev/null 2>&1 || fail "python3 not found"
ok "python3: $(python3 --version 2>&1)"

if python3 - <<'PY' >/dev/null 2>&1
import telegram
print(telegram.__version__)
PY
then
  ok "python-telegram-bot import: passed"
else
  fail "python-telegram-bot not installed (pip install python-telegram-bot)"
fi

if [[ -f "$SECRETS_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$SECRETS_FILE" || true
  ok "loaded secrets: $SECRETS_FILE"
elif [[ -f "$ROOT/.secrets.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.secrets.env" || true
  ok "loaded local secrets: $ROOT/.secrets.env"
else
  fail "no secrets file found ($SECRETS_FILE or $ROOT/.secrets.env)"
fi

if [[ -f "$ROOT/machina_env.sh" ]]; then
  set +u
  # shellcheck disable=SC1091
  source "$ROOT/machina_env.sh" >/dev/null 2>&1 || true
  set -u
  ok "loaded optional env: machina_env.sh"
else
  warn "machina_env.sh missing (optional)"
fi

[[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] || fail "TELEGRAM_BOT_TOKEN is empty"
[[ -n "${TELEGRAM_CHAT_ID:-}" ]] || fail "TELEGRAM_CHAT_ID is empty"
ok "telegram env present (chat_id=$TELEGRAM_CHAT_ID)"

if command -v getent >/dev/null 2>&1; then
  if getent hosts api.telegram.org >/dev/null 2>&1; then
    ok "DNS resolve: api.telegram.org"
  else
    warn "DNS resolve failed for api.telegram.org"
  fi
fi

if command -v curl >/dev/null 2>&1; then
  if curl -fsS --connect-timeout 5 --max-time 10 \
      "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" >/tmp/machina_getme.json 2>/dev/null; then
    if grep -q '"ok":true' /tmp/machina_getme.json; then
      ok "telegram token check (getMe): passed"
    else
      warn "telegram getMe returned non-ok response"
    fi
  else
    warn "telegram getMe request failed (network/firewall/DNS)"
  fi
fi

echo
echo "Next:"
echo "  nohup ./scripts/run_bot_forever.sh >/tmp/machina_bot.launcher.out 2>&1 &"
echo "  tail -f /tmp/machina_bot.log"
