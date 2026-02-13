#!/usr/bin/env bash
set -euo pipefail

# Example: signed /enqueue request.
# Requires:
#   export MACHINA_API_TOKEN=...
#   export MACHINA_API_HMAC_SECRET=...
#   export MACHINA_SERVE_URL=http://127.0.0.1:8787
#
# Usage:
#   ./curl_enqueue_signed.sh request.json

REQ="${1:-}"
if [[ -z "$REQ" || ! -f "$REQ" ]]; then
  echo "usage: $0 <request.json>" >&2
  exit 2
fi

URL="${MACHINA_SERVE_URL:-http://127.0.0.1:8787}"
TOKEN="${MACHINA_API_TOKEN:-}"

HDRS="$(./scripts/machina_sign.py POST /enqueue < "$REQ")"
declare -a HARGS=()
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  HARGS+=(-H "$line")
done <<< "$HDRS"

curl -sS -X POST "$URL/enqueue" \
  -H "Authorization: Bearer $TOKEN" \
  "${HARGS[@]}" \
  -H "Content-Type: application/json" \
  --data-binary @"$REQ"
echo
