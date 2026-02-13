# Operations

Machina can execute shell commands and load plugins. Treat it as **high risk** unless you sandbox it.

---

## Serve mode (HTTP enqueue + integrated workers)

```bash
./build/machina_cli serve --host 127.0.0.1 --port 8080 --workers 4
```

Default port: **8080**. Workers capped at 64.

### Auth (recommended)

- `MACHINA_API_TOKEN` : bearer token
- `MACHINA_API_HMAC_SECRET` : request signature secret
- `MACHINA_API_HMAC_TTL_SEC` : timestamp TTL (default: 60)

### Queue root

- `MACHINA_ROOT` : working root (must exist)

### Durability / fast restart (serve)

Serve mode can maintain a small **WAL + checkpoint** for quick restart.

- `MACHINA_WAL_ENABLE` (default: 1)
- `MACHINA_WAL_CHECKPOINT_MS` (default: 5000)
- `MACHINA_WAL_FSYNC` (default: 0, set to 1 in `MACHINA_PROFILE=prod`)
- `MACHINA_WAL_CRC32` (default: 0) — optional CRC32 integrity checksums on WAL entries

Files are written to: `<MACHINA_ROOT>/queue/wal/`.

### Scan / retry knobs

- `MACHINA_SERVE_SCAN_MS` (default: 150)
- `MACHINA_RETRY_MIN_MS` / `MACHINA_RETRY_MAX_MS`
- `MACHINA_MAX_ATTEMPTS`

### Enqueue dedup

- `MACHINA_DEDUP_TTL_MS` (default: 300000 = 5 min) — request_id-based idempotency cache TTL
- Dedup entries are WAL-persisted for crash recovery

---

## Chat mode (interactive REPL)

```bash
source machina_env.sh
./build/machina_cli chat
```

Requires:
- `MACHINA_CHAT_CMD` : chat driver script (e.g., `python3 policies/chat_driver.py`)
- `MACHINA_CHAT_BACKEND` : `oai_compat` or `anthropic`
- `MACHINA_CHAT_TIMEOUT_MS` : chat driver timeout (default: 60000)

The chat system uses a 3-phase Pulse Loop:
1. **Intent parsing** — user message → structured action (run_goal, exec_tool, config, etc.)
2. **Execution** — dispatch to tool/goal engine
3. **Summary** — LLM summarizes results into natural language

Fallback pattern matching is used when LLM is unavailable.

---

## Shell execution hardening

Tier0 shell tool is intentionally restrictive.

- `MACHINA_SHELL_TIMEOUT_MS`
- `MACHINA_SHELL_STDOUT_MAX` / `MACHINA_SHELL_STDERR_MAX`
- `MACHINA_SHELL_ALLOWED_EXE` (comma-separated basenames)

Shell allowlist uses `canonical()` (symlink-resolved) path verification.

Prefer OS-level sandboxing (container/bwrap/nsjail) for real production.

---

## HTTP tool hardening (SSRF defense)

- `MACHINA_HTTP_ALLOWED_HOSTS` : host allowlist with wildcard support (`*.example.com`)
- `MACHINA_HTTP_DEFAULT_DENY` : set to `1` for default-deny when no allowlist configured
- `MACHINA_HTTP_TIMEOUT_MS` (default: 3000)
- `MACHINA_HTTP_STDOUT_MAX` (default: 128KB)
- `MACHINA_HTTP_RLIMIT_*` : CPU/AS/FSIZE/NOFILE/NPROC limits

SSRF protection:
- DNS resolution checks all IPs against private/reserved ranges (RFC 1918/5737/6598, loopback, link-local, cloud metadata)
- `curl --resolve` pins the validated IP to prevent DNS rebinding
- `--max-redirs 0` blocks redirect chains

---

## Policy driver hardening

The policy selector runs as an **external process** (`MACHINA_POLICY_CMD`).

Key knobs:
- `MACHINA_POLICY_TIMEOUT_MS`
- `MACHINA_POLICY_STDOUT_MAX`
- `MACHINA_POLICY_ALLOWED_EXE`
- `MACHINA_POLICY_ALLOWED_SCRIPT_ROOT`
- `MACHINA_POLICY_RLIMIT_*` (CPU/AS/FSIZE/NOFILE/NPROC)

See `docs/POLICY_DRIVER.md` and `docs/LLM_BACKENDS.md`.

---

## Embedding configuration

- `MACHINA_EMBED_PROVIDER` : `hash` (default, no GPU) or `cmd` (external command)
- `MACHINA_EMBED_CMD` : embedding command (e.g., `python3 tools/embed/embed_e5.py`)
- `MACHINA_EMBED_TIMEOUT_MS` (default: 5000, set higher for first load)
- `MACHINA_EMBED_CPU_MS` / `MACHINA_EMBED_AS_MB` / `MACHINA_EMBED_NPROC` : rlimits for embedding process
- `MACHINA_GPU_DIM` : embedding vector dimension (default: 128, e5-small=384)
- `MACHINA_SELECTOR` : `HEURISTIC` or `GPU_CENTROID`

Note: PyTorch/CUDA/OpenBLAS requires `RLIMIT_NPROC=0` and `RLIMIT_AS_MB=0` (unlimited).

---

## Profile system (v3.4)

One-switch environment configuration:

```bash
export MACHINA_PROFILE=prod   # or "dev" (default)
```

| Setting | DEV | PROD |
|---------|-----|------|
| `MACHINA_WAL_FSYNC` | 0 | 1 |
| `MACHINA_GENESIS_ENABLE` | 1 | 0 |
| `MACHINA_SECCOMP_ENABLE` | 0 | 1 |
| `MACHINA_GENESIS_GUARD` | 0 | 1 |
| `MACHINA_SHELL_TIMEOUT_MS` | 30000 | 10000 |
| `MACHINA_POLICY_TIMEOUT_MS` | 60000 | 30000 |
| `MACHINA_GENESIS_PROD_MODE` | — | 1 |
| `MACHINA_HTTP_DEFAULT_DENY` | — | 1 |
| `MACHINA_TOOLHOST_ISOLATE` | — | 1 |

Profile uses `setenv(overwrite=0)` — pre-existing env vars always take precedence.

---

## seccomp-BPF sandboxing (v3.4)

Kernel-level syscall filtering (x86_64 + aarch64):

```bash
export MACHINA_SECCOMP_ENABLE=1   # included in MACHINA_PROFILE=prod
```

Allowed syscalls: read, write, open, close, mmap, munmap, mprotect, brk, exit, exit_group, etc.
Blocked: ptrace, mount, reboot, clone3, and other privileged operations.

Network profile (optional):

```bash
export MACHINA_SECCOMP_PROFILE=net   # allow socket/connect/send/recv syscalls
# or legacy toggle:
export MACHINA_SECCOMP_ALLOW_NET=1
```

Default profile is strict (network syscalls blocked) when seccomp is enabled.

---

## Permission leases (v3.4)

4-tier single-use token system for privileged tool execution:

```bash
export MACHINA_LEASE_ENFORCE=1   # opt-in
```

Lease management via toolhost `--serve` mode internal commands:
- `_lease.issue` — create a lease token (tool_aid, tier, ttl_ms)
- `_lease.gc` — garbage collect expired leases

---

## Plugin hash pinning (v3.4)

SHA-256 verification before `dlopen`:

```cpp
PluginManager pm;
pm.set_expected_hash("/path/to/plugin.so", "abc123...sha256hex...");
pm.load_plugin("/path/to/plugin.so", &registrar, &err);
// → rejects if hash doesn't match (constant-time comparison)
```

## Plugin capability gate (v3.8)

Plugins can declare required capabilities via `machina_plugin_capabilities()`.
The host enforces a maximum allowed capability mask:

```cpp
PluginManager pm;
pm.set_allowed_capabilities(CAP_FILE_READ | CAP_MEMORY);  // restrict
pm.load_plugin("plugin.so", &registrar, &err);
// → rejects plugin if it declares CAP_SHELL or CAP_NETWORK
```

Capability flags (`PluginCap`): `FILE_READ`, `FILE_WRITE`, `SHELL`, `NETWORK`, `MEMORY`, `GENESIS`, `GPU`.
Plugins that don't export `machina_plugin_capabilities()` are assumed `CAP_ALL` (backwards-compatible).

## Bubblewrap sandbox enforcement (v3.8)

Python `sandboxed_run()` uses bubblewrap for namespace isolation:

- `MACHINA_BWRAP_REQUIRED=1`: **hard fail** if bwrap is not installed (RuntimeError)
- `MACHINA_PROFILE=prod`: logs WARNING when bwrap is missing (soft fallback)
- DEV mode: silent fallback to plain subprocess

Install: `apt install bubblewrap`

---

## Observability (v3.4)

Prometheus `/metrics` endpoint on the serve HTTP server:

```bash
curl http://localhost:8080/metrics
# machina_jobs_processed_total, machina_jobs_ok_total, machina_jobs_fail_total
# machina_queue_inbox_size, machina_queue_processing_size, ...
# machina_tool_ok_total{aid="AID.XXX"}, machina_tool_fail_total{aid="AID.XXX"}
# machina_tool_duration_ms_total{aid="AID.XXX"}
```

Per-tool metrics are extracted from run logs after each job completes.

### request_id tracing (v3.8)

Callers can include `"request_id"` in run_request JSON. It propagates to:
- C++ audit log (`run_*.jsonl`) — every event includes `request_id` field
- Python autonomic audit log (`autonomic_audit.jsonl`) — optional `request_id` parameter
- `/enqueue` response filename (FNV1a hash suffix for traceability)

---

## Telegram bot integration

```bash
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="allowed-chat-id"  # optional filter

source machina_env.sh
nohup python3 telegram_bot.py > /tmp/telegram_bot.log 2>&1 &
```

Features:
- Natural language conversation via Ollama/Anthropic/OpenAI-compatible backends
- 3-phase Pulse Loop: Intent → Execute → Continue (profile-aware: DEV 100 cycles/3600s, PROD 30 cycles/600s; env: `MACHINA_MAX_CYCLES`, `MACHINA_PULSE_BUDGET_S`)
- Python AID tools + 23 C++ manifest tools + MCP tools via AID dispatch
- 6-layer code auto-fix: fence strip → input() → f-string→str() → colon → indent → print()
- Code-fence-aware message splitting: auto-close/reopen ``` across Telegram 4096-char chunks
- 18 Telegram commands (core: /start /clear /status /gpu /models /use /auto_status /auto_route /stop; ext: /mcp_status /mcp_reload /mcp_enable /mcp_disable /mcp_add /mcp_remove /dev_mode /tools /graph_status)
- Autonomic heartbeat alerts: 2-tier (stream/milestone) + proactive regression/rollback
- Permission engine: 4 modes (open/standard/locked/supervised) + per-tool overrides
- Self-evolution: `MACHINA_SELF_EVOLVE=1` enables autonomous source patching with safety gates
- **LLM-free Fast Path** (v6.5): keyword-based routing for common ops (shell/file/search/memory) — skips LLM intent call
- **Policy Distillation** (v6.5): `distill_rules()` extracts (keyword→tool, success_rate) from experiences, `lookup_distilled()` wired into Pulse fast path fallback (≥0.8 confidence → skip LLM)

---

## Maintenance guardrails (v6.5+)

### AID reference validation

```bash
python3 scripts/validate_aid_refs.py
python3 scripts/validate_docs_refs.py
scripts/run_guardrails.sh
```

- `validate_aid_refs.py`: Python/policies code 내 AID 문자열이 canonical set(Manifest + registry constants)과 일치하는지 검사
- `validate_docs_refs.py`: `README.md`, `docs/*.md`의 AID 표기가 실제 도구 정의와 일치하는지 검사

### Secret hygiene validation

```bash
python3 scripts/security_guardrails.py
```

- `mcp_servers.json`에서 평문 Bearer/API 키를 차단
- 허용 형식: `${ENV_VAR}` 플레이스홀더 사용

### work/memory rotation (safe by default)

```bash
# dry-run
python3 scripts/work_memory_maintenance.py

# apply (archive + truncate)
python3 scripts/work_memory_maintenance.py --apply --keep-lines 5000 --min-size-mb 32
```

- 기본 모드: dry-run
- 적용 시: `work/memory/archive/YYYYMMDD/`에 원본 백업 후 tail 유지

### Process restart orchestration (machine-wide cleanup + single-stack start)

```bash
# 1) detect
scripts/ops_detect.sh

# 2) restart (TERM -> KILL, then start + healthcheck)
scripts/ops_restart.sh

# dry-run
scripts/ops_restart.sh --dry-run
```

- `ops_detect.sh`: 현재 Machina/Telegram/MCP 관련 프로세스 스냅샷 생성 (`ops/pids.current.json`)
- `ops_kill.sh`: 단계적 종료(SIGTERM 후 대기, 잔존 시 SIGKILL)
- `ops_start.sh`: 표준 단일 스택 기동 (`machina_cli serve` + `telegram_bot.py`)
- `ops_healthcheck.sh`: `/health` + bot/serve PID 기반 상태 확인 (`ops/health.report.json`)
- **Atomic FILE.WRITE** (v6.5): tmp→fsync→rename+.bak pattern prevents data loss on crash

---

## Self-Improvement Guarantees (v4.0)

The autonomic engine includes safety mechanisms ensuring monotonically improving behavior:

### Regression Gate
E2E test suite gating in `machina_gvu.py`:
- Runs full 34-test E2E suite after any self-heal or genesis change
- Maintains monotonically improving baseline (`work/memory/regression_baseline.json`)
- Rejects changes that reduce test pass count → automatic rollback

### Reward Tracker
Rolling-window reward signal in `machina_learning.py`:
- Compares success_rate across 100-experience windows
- Threshold: >5% drop triggers regression alert
- Wired into Level 4 (Hygiene) for periodic monitoring

### Auto-Rollback
Automatic artifact reversion in `machina_autonomic/_engine_ops.py`:
- `_rollback_artifact()`: removes util script + skill JSONL entry (flock-safe)
- `_auto_rollback_recent()`: removes most recent skill when reward drops
- Conservative: one rollback per hygiene cycle

### Trust Scoring
- Composite: `recency_decay(half-life=7d) × quality(success=1.0, fail=0.3)`
- L4 Hygiene prunes experiences + skills with trust < 0.1
- CuriosityDriver Relevance Gate: 60+ keyword alignment check before execution

### Insight Dedup (v4.0)
- 5-minute proximity check: skip if any tool_stats insight within 300s
- Content match: skip if identical rules set already exists
- Max lookback: 20 recent entries (up from 5)

### Knowledge Query Dedup (v4.0)
- 24-hour dedup window: skip web search if same query in knowledge.jsonl within 86400s
- Applied in `_stim_web()` before DDGS search call

---

## Autonomous Modes (v4.0)

### DEV Explore Mode

```bash
export MACHINA_DEV_EXPLORE=1
python3 -m machina_autonomic._engine   # or via telegram_bot.py
```

Aggressive timings for rapid self-improvement:
- L1=1min, L2=2min, L3=3min, L5=3min idle
- Curiosity: 20/day (vs 3), 10min cooldown (vs 2hr)
- Proactive Telegram reports every 10min (rate-limited per category)
- All safety guards remain active (regression gate, relevance gate, trust scoring)

### LLM Self-Questioning Loop (v4.0)
Intelligent self-directed action replaces static random stimuli:
- LLM asks itself "what to do next?" based on current state context
- 4 action types: `search` (web), `test_tool` (dispatch), `code` (sandbox), `reflect` (insight)
- Context-aware: sees recent experiences, skills, knowledge, past self-questions
- Seed hints: randomly rotated prompts prevent action repetition
- Safety: dedup + no-op classification + cooldown/backoff, per-burst cap (3)
- All actions produce real tool calls (no parametric-only learning)
- Python-level tool dispatch (`_PY_TOOLS`): memory_save, memory_query, code_exec, web_search, file_read, file_list
- RandomStimulus only fires when SQ limit reached or all levels idle

### Multi-Turn Burst Mode
Sustained autonomous work session triggered by long idle:
- Duration: max 30min per burst session (PROD), 1hr (DEV)
- Priority scheduler: Test(5) > Heal/Inbox(4) > Web(3) > Curiosity(2) > Reflect(1) > SelfQuestion(0)
- Exit conditions: time limit, user activity (idle < 30s), stall detection (5 turns)
- Trigger: 2hr idle (NORMAL), 3min idle (DEV)
- L2 burst throttle: after 3 consecutive perfect passes, rate increases 8x

### Cloud Rate Factor
- `_cloud_rate_factor()`: always 1.0 (internal routing handles backend differences)
- `/auto_status` shows current cloud_auto status

### Web Exploration
Autonomous web search → LLM summarize → knowledge stream:
- Topic selection from capability gaps + recent skills context
- 24-hour dedup: skips search if same query already in knowledge.jsonl within 24h
- Requires `duckduckgo_search` (`pip install duckduckgo-search`)
- Results stored in `work/memory/knowledge.jsonl`
- Rate: 1hr (NORMAL), 15min (DEV)

### State Persistence
- `autonomic_state.json`: saves level_done timestamps + stasis state
- Restored on startup; saved on every heartbeat + SIGTERM/SIGINT
- PID file: `MACHINA_ROOT/autonomic.pid`

### Log Size Management
- Per-file cap: 2GB; total cap: 10GB
- Exceeding → automatic half-life rotation (keep newest 50%)

---

## Timeout configuration (v6.3.1)

Key timeout values (env-var overridable where noted):

| Component | Location | Default | Env Override |
|-----------|----------|---------|-------------|
| Toolhost subprocess | machina_dispatch.py | 90s | `MACHINA_TOOLHOST_TIMEOUT` |
| Code test (GVU) | machina_gvu.py | 60s | — |
| Error scan plugin | machina_dispatch_exec.py | 30s | — |
| Chat LLM API | policies/chat_llm.py | 180s | — |
| MCP tool call | machina_mcp_connection.py | 180s | — |
| Future.result (async bridge) | machina_dispatch_exec.py | 120s | — |
| Code execution | machina_tools.py | 60s | `MACHINA_CODE_TIMEOUT` |
| Chat driver subprocess | telegram_bot.py | 60s | `MACHINA_CHAT_TIMEOUT_MS` |

Autonomic ASK handling:
- `MACHINA_AUTONOMIC_APPROVE_ALL_ASK=0` (default): ASK prompts remain enabled.
- `MACHINA_AUTONOMIC_APPROVE_ALL_ASK=1`: self-improvement loops auto-accept ASK tools (no interactive approval popup).
- `MACHINA_AUTONOMIC_AUTO_APPROVE_AIDS`: optional explicit allowlist for low-risk ASK tools.

---

## Production checklist

- Run as **non-root**
- Set `MACHINA_PROFILE=prod` (enables fsync, seccomp, guard; disables Genesis)
- Dedicated `MACHINA_ROOT` (no sensitive mounts)
- Tight allowlists (shell + policy + HTTP hosts)
- OS sandbox (cgroups/ulimit + container/bwrap/nsjail)
- Export audit logs (and rotate)
- Keep policy and plugins **reviewed & hash-pinned**
- Enable `MACHINA_LEASE_ENFORCE=1` for tiered tool access control
- Monitor via `/metrics` endpoint (Prometheus/Grafana) — includes per-tool counters
- Set `MACHINA_API_TOKEN` or `MACHINA_API_HMAC_SECRET` for serve mode auth
- Configure `MACHINA_HTTP_ALLOWED_HOSTS` to restrict outbound HTTP
- Set `MACHINA_BWRAP_REQUIRED=1` to enforce sandbox (or install bubblewrap)
- Restrict plugin capabilities: `pm.set_allowed_capabilities()` with minimum-privilege mask
- Include `request_id` in enqueue requests for end-to-end tracing
- Run `python3 machina_reindex.py --verify` periodically to check JSONL memory integrity
- See `docs/QUICKSTART.md` for 10-minute onboarding guide
