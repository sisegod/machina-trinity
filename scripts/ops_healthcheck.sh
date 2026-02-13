#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${MACHINA_SERVE_HOST:-127.0.0.1}"
PORT="${MACHINA_SERVE_PORT:-8091}"
SERVE_URL="http://${HOST}:${PORT}"
OUT="$ROOT/ops/health.report.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serve-url) SERVE_URL="$2"; shift 2 ;;
    --json-out) OUT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$OUT")"

BOT_PIDS="$(pgrep -f "python3 .*telegram_bot.py|python .*telegram_bot.py" || true)"
SERVE_PIDS="$(pgrep -f "$ROOT/build/machina_cli serve|machina_cli serve" || true)"

HEALTH_OK=0
HEALTH_BODY=""
if HEALTH_BODY="$(curl -fsS --max-time 3 "$SERVE_URL/health" 2>/dev/null)"; then
  HEALTH_OK=1
fi

python3 - "$OUT" "$SERVE_URL" "$HEALTH_OK" "$HEALTH_BODY" "$BOT_PIDS" "$SERVE_PIDS" <<'PY'
import json, sys, time
out, serve_url = sys.argv[1], sys.argv[2]
health_ok = bool(int(sys.argv[3]))
health_body = sys.argv[4]
bot_pids = [int(x) for x in sys.argv[5].split()] if len(sys.argv) > 5 and sys.argv[5].strip() else []
serve_pids = [int(x) for x in sys.argv[6].split()] if len(sys.argv) > 6 and sys.argv[6].strip() else []
payload = {
    "ts": int(time.time()),
    "serve_url": serve_url,
    "serve_health_ok": health_ok,
    "serve_health_body": health_body,
    "bot_pids": bot_pids,
    "serve_pids": serve_pids,
    "ok": health_ok and len(bot_pids) >= 1 and len(serve_pids) >= 1,
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
print(json.dumps(payload, ensure_ascii=False))
PY
