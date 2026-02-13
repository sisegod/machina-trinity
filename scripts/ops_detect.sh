#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/ops/pids.current.json"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json-out) OUT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$OUT")"

python3 - "$ROOT" "$OUT" <<'PY'
import json
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()

lines = subprocess.check_output(
    ["ps", "-eo", "pid=,ppid=,etimes=,args="], text=True, errors="replace"
).splitlines()

patterns = [
    (re.compile(r"\bpython3?\s+.*telegram_bot\.py\b"), "telegram_bot"),
    (re.compile(r"\bmachina_cli\s+serve\b"), "machina_serve"),
    (re.compile(r"\bnpm\s+exec\s+@z_ai/mcp-server\b"), "mcp_zai_launcher"),
    (re.compile(r"\bnpm\s+exec\s+n8n-mcp\b"), "mcp_n8n_launcher"),
    (re.compile(r"\bzai-mcp-server\b"), "mcp_zai"),
    (re.compile(r"\bn8n-mcp\b"), "mcp_n8n"),
    (re.compile(r"\bollama\s+serve\b"), "ollama"),
]

rows = []
for raw in lines:
    s = raw.strip()
    if not s:
        continue
    m = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$", s)
    if not m:
        continue
    pid, ppid, etimes, cmd = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    role = None
    for rx, r in patterns:
        if rx.search(cmd):
            role = r
            break
    if role is None:
        continue
    repo_owned = str(root) in cmd
    protected = (role == "ollama")
    rows.append(
        {
            "pid": pid,
            "ppid": ppid,
            "etimes": etimes,
            "role": role,
            "repo_owned": repo_owned,
            "protected": protected,
            "cmd": cmd,
        }
    )

rows.sort(key=lambda x: (x["role"], x["pid"]))
payload = {
    "root": str(root),
    "count": len(rows),
    "processes": rows,
}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[ops-detect] wrote {out} ({len(rows)} processes)")
for r in rows:
    print(f"{r['pid']:>7} {r['role']:<18} owned={str(r['repo_owned']).lower():<5} prot={str(r['protected']).lower():<5} {r['cmd'][:140]}")
PY

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[ops-detect] dry-run mode"
fi
