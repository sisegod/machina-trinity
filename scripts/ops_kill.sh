#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IN_JSON="$ROOT/ops/pids.current.json"
TERM_SECONDS="${MACHINA_OPS_TERM_TIMEOUT_SEC:-8}"
INCLUDE_OLLAMA=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) IN_JSON="$2"; shift 2 ;;
    --term-seconds) TERM_SECONDS="$2"; shift 2 ;;
    --include-ollama) INCLUDE_OLLAMA=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$IN_JSON" ]]; then
  "$ROOT/scripts/ops_detect.sh" --json-out "$IN_JSON"
fi

mkdir -p "$ROOT/ops"
KILLED_JSON="$ROOT/ops/pids.killed.json"
REPORT_TXT="$ROOT/ops/kill.report.txt"

PIDS="$(python3 - "$IN_JSON" "$INCLUDE_OLLAMA" $$ $PPID <<'PY'
import json
import sys
from pathlib import Path

p = Path(sys.argv[1])
include_ollama = (sys.argv[2] == "1")
self_pid = int(sys.argv[3])
self_ppid = int(sys.argv[4])
data = json.loads(p.read_text(encoding="utf-8"))
out = []
for row in data.get("processes", []):
    pid = int(row.get("pid", 0))
    role = row.get("role", "")
    protected = bool(row.get("protected", False))
    if pid in (self_pid, self_ppid):
        continue
    if protected and not include_ollama:
        continue
    out.append(str(pid))
print(" ".join(out))
PY
)"

echo "[ops-kill] targets: ${PIDS:-<none>}" | tee "$REPORT_TXT"
if [[ -z "${PIDS}" ]]; then
  echo '{"targets":[],"terminated":[],"killed":[]}' > "$KILLED_JSON"
  exit 0
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[ops-kill] dry-run, no signals sent" | tee -a "$REPORT_TXT"
  python3 - "$KILLED_JSON" "$PIDS" <<'PY'
import json, sys
out = sys.argv[1]
targets = [int(x) for x in sys.argv[2].split()] if len(sys.argv) > 2 and sys.argv[2].strip() else []
with open(out, "w", encoding="utf-8") as f:
    json.dump({"targets": targets, "terminated": [], "killed": []}, f, indent=2)
PY
  exit 0
fi

TERMINATED=()
KILLED=()

for pid in $PIDS; do
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
    TERMINATED+=("$pid")
  fi
done

sleep "$TERM_SECONDS"

for pid in $PIDS; do
  if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
    KILLED+=("$pid")
  fi
done

python3 - "$KILLED_JSON" "${PIDS}" "${TERMINATED[*]:-}" "${KILLED[*]:-}" <<'PY'
import json, sys
out = sys.argv[1]
targets = [int(x) for x in sys.argv[2].split()] if len(sys.argv) > 2 and sys.argv[2].strip() else []
terminated = [int(x) for x in sys.argv[3].split()] if len(sys.argv) > 3 and sys.argv[3].strip() else []
killed = [int(x) for x in sys.argv[4].split()] if len(sys.argv) > 4 and sys.argv[4].strip() else []
with open(out, "w", encoding="utf-8") as f:
    json.dump(
        {"targets": targets, "terminated": terminated, "killed": killed},
        f, ensure_ascii=False, indent=2
    )
PY

echo "[ops-kill] term=${#TERMINATED[@]} kill=${#KILLED[@]}" | tee -a "$REPORT_TXT"
