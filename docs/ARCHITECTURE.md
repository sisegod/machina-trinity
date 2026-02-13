# Architecture

Machina Trinity splits the system into **Body, Driver, Memory**.

- **Body (Kernel/Core)**: executes tools safely (Tx + Rollback) and records everything.
- **Driver (Selector/Policy)**: chooses the next tool (heuristic fallback + optional LLM policy).
- **Memory (Logs/Store)**: audit logs + replay + long-term event store.

---

## Request lifecycle

1. **Ingress**
   - `machina_cli run <request.json>` (single shot)
   - `machina_cli autopilot <queue_dir>` (disk queue workers)
   - `machina_cli serve --workers N` (daemon: HTTP enqueue + inproc workers)
   - `machina_cli chat` (interactive REPL with LLM intent parsing)
   - `machina_cli cts <manifest_dir>` (Compliance Test Suite)
   - `machina_cli tool_exec <tool_aid>` (direct single-tool execution, stdin JSON)

2. **Selection (Driver)**
   - The runner builds a **Menu** of tools (from ToolPacks + registry).
   - It calls the Selector twice:
     - `FallbackOnly` (safe heuristic)
     - `PolicyOnly` (external process, optional)
   - A deterministic merge picks the final action.
   - **Selectors**: HeuristicSelector (always), GPU_CENTROID (embedding-based cosine similarity), ExternalProcessSelector (LLM policy)

3. **Execution (Body)**
   - Tool runs inside a **Tx**.
   - On failure, DS state rolls back.
   - On success, DS deltas are committed.

4. **Audit + Replay (Memory)**
   - Every step is written as a structured record.
   - `replay --strict` can reproduce selections and outputs.
   - For non-deterministic tools (`deterministic=false`), `replay_strict` does not re-execute the tool.
     It restores state by applying logged `tool_ok.tx_patch` operations.
     `tx_patch` contract: JSON array of `{op,path,value?}` where
     - `op`: `add|replace|remove`
     - `path`: `/slots/<0..7>`
     - `value` (for add/replace): Artifact JSON (`type`,`provenance`,`content_json`,`size_bytes`)

---

## Key modules

### Core
- **Registry**: resolves tool IDs → function pointers / plugins
- **Plugin loader**: loads `.so/.dll` at runtime with SHA-256 hash verification + capability enforcement
- **Selectors**
  - HeuristicSelector (always available)
  - GPU_CENTROID Selector (embedding-based, uses `embed_texts_batch()` for cosine-similarity centroid matching)
  - ExternalProcessSelector (LLM policy via `MACHINA_POLICY_CMD`)

### Tools (Tier0)
Tier0 should exist at "Genesis time":
- Filesystem: `file_read`, `file_write` (O_NOFOLLOW, symlink-resolved path validation)
- Shell: `shell_exec` (allowlist + sandbox hooks + canonical() path verification)
- Network: `http_get` (allowlist + timeouts + SSRF defense with `--resolve` pinning)
- Queue: enqueue/dequeue/retry helpers
- Memory: append/search/query (BM25 + embedding hybrid)
- Genesis: write → compile → load new plugins (source guard + hash verification)
- Embedding: text embedding via external command provider
- VectorDB: upsert/query for embedding storage

### Queue + Autopilot
Two modes:
- **Disk queue + autopilot**: durable by design, higher latency
- **Daemon mode**: disk queue **plus** in-memory CPQ fast path
  - still writes inbox files for crash recovery
  - per-connection threading with Slowloris defense (10s socket timeout)

### Chat System
- **3-phase Pulse Loop**: Intent parsing → Execute → Continue (profile-aware: DEV 100/3600s, PROD 30/600s)
- **LLM-free Fast Path**: keyword hash-based intent routing for common ops (shell/file/search/memory) — skips LLM when single dominant match detected
- **Policy Distillation**: experience-based rule cache (`distill_rules()`, 10min TTL) → `lookup_distilled()` (≥0.8 confidence gate) → wired into Pulse fast path fallback
- `policies/chat_driver.py` + `chat_driver_util.py`: LLM bridge, DST, entity memory, skill injection, fast path
- `policies/chat_llm.py`: Ollama/Anthropic/OAI API call layer (field sanitization, base_url support)
- `policies/chat_intent_map.py`: Intent classification + normalization + adaptive prompts
- `telegram_bot.py` + `telegram_bot_handlers.py` + `telegram_bot_pulse.py`: Bot core, message handling, Pulse loop
- `telegram_commands.py` + `telegram_commands_ext.py`: 18 commands
- Fallback pattern matching when LLM unavailable

### Python Modules (v6.5 — 34 production files + examples)

**Root (20 files)**

| Module | Role |
|--------|------|
| `machina_shared.py` | BM25, JSONL helpers, `_call_ollama`, `_call_engine_llm`, `sandboxed_run`, `_extract_json_robust` |
| `machina_tools.py` | Code execution (60s default, `MACHINA_CODE_TIMEOUT` override), 6-layer auto-fix |
| `machina_tools_fileops.py` | File I/O tools, git operations, venv creation |
| `machina_learning.py` | ExpeL, Reflexion, Voyager, wisdom, RewardTracker, PolicyDistillation |
| `machina_learning_memory.py` | Memory streams (experiences, insights, skills, knowledge) |
| `machina_graph.py` | Graph Memory 2.0 — entity extraction, relation graph, multi-hop BFS |
| `machina_graph_memory.py` | Graph storage, time decay, deduplication |
| `machina_gvu.py` | SelfQuestioner, SelfTester, SelfHealer, Curriculum, RegressionGate |
| `machina_gvu_tracker.py` | Curriculum tracking, test scheduling |
| `machina_dispatch.py` | AID tool dispatch, aliases (70+), MCP bridge wiring |
| `machina_dispatch_exec.py` | `execute_intent`, tool execution, skill auto-record, atomic FILE.WRITE |
| `machina_mcp.py` | MCP client bridge — server management, tool discovery, AID mapping |
| `machina_mcp_connection.py` | MCP server connection lifecycle |
| `machina_permissions.py` | 3-tier permission engine (open/standard/locked/supervised), explicit map + manifest side_effects fallback |
| `machina_reindex.py` | JSONL memory index verifier/rebuilder (--verify/--fix/--stats) |
| `telegram_bot.py` | Bot config, globals, startup, handler registration |
| `telegram_bot_handlers.py` | Message handling, approval callbacks |
| `telegram_bot_pulse.py` | Pulse loop, auto-memory, alert delivery |
| `telegram_commands.py` | 8 core commands: /start /clear /status /gpu /models /use /auto_status /auto_route |
| `telegram_commands_ext.py` | 9 ext commands: /mcp_status /mcp_reload /mcp_enable /mcp_disable /mcp_add /mcp_remove /dev_mode /tools /graph_status |

**machina_autonomic/ (11 files)**

| Module | Role |
|--------|------|
| `__init__.py` | Public facade: AutonomicEngine + set_alert_callback + trace context |
| `_constants.py` | Config, timings (DEV/PROD), logger, audit, alert callback, OTel trace |
| `_engine.py` | AutonomicEngine class — init, tick(), state persistence, delegation |
| `_engine_levels.py` | L1-L6 handlers, tool profile, milestone/stream, helpers |
| `_engine_burst.py` | Burst mode, stimulus handlers, manifest, inbox drain |
| `_engine_ops.py` | Hygiene, rollback, log mgmt, metrics, self-evolve patch, run loops |
| `_autoapprove.py` | Autonomic ASK auto-approve policy helpers |
| `_sq.py` | Self-Questioning loop (SQ): LLM self-question, action dispatch |
| `_stimulus.py` | CuriosityDriver (relevance gate, gap scanning) |
| `_random_stimulus.py` | RandomStimulus (25-pool + dynamic generation) |
| `_web.py` | DDGS search, deep web search pipeline, knowledge→insight bridge |

**policies/ (4 files)**

| Module | Role |
|--------|------|
| `chat_driver.py` | 3-phase pipeline (Intent → Execute → Continue), DST, entity memory |
| `chat_driver_util.py` | History trimming, compression, skill hints |
| `chat_intent_map.py` | Intent prompt + normalization + MCP tool routing + adaptive prompts |
| `chat_llm.py` | Ollama/Anthropic/OAI API call layer |

---

## Safety model (v3.4+)

Machina's safety is "defense in depth" — 9 layers:

1. **Tx + rollback** — protects core state from corruption
2. **Audit log + replay** — detects regressions, tamper-evident hash chains
3. **Allowlists** — restrict *what* can run (shell, policy)
4. **seccomp-BPF** — kernel-level syscall filtering (x86_64 + aarch64, opt-in `MACHINA_SECCOMP_ENABLE=1`)
5. **Permission leases** — 4-tier single-use tokens for privileged tool access
6. **Plugin hash pinning** — SHA-256 verification before `dlopen` with constant-time comparison
7. **Plugin capability gate** — bitmask-based capability declaration (`PluginCap` enum); host rejects plugins exceeding `allowed_caps_`
8. **SSRF defense** — DNS resolution + private IP blocking + `curl --resolve` pinning (prevents DNS rebinding)
9. **CRC32 WAL framing** — optional integrity checksums on WAL entries (crash detection)

### Additional protections
- **bwrap integration** — bubblewrap namespace isolation for LLM-generated code; PROD enforcement via `MACHINA_BWRAP_REQUIRED=1` (RuntimeError if missing)
- **Genesis hash verification** — compile stage records SHA-256; load stage verifies binary integrity before `dlopen`, rejects on mismatch
- **Genesis source guard** — blocklist for dangerous C/C++ APIs and headers
- **Nonce replay protection** — TTL-based pruning (5K), hard cap (10K)
- **Enqueue dedup** — request_id-based idempotency with WAL-persisted dedup cache
- **request_id propagation** — caller-supplied tracing ID flows through RunHeader → audit log → run log for end-to-end request tracing

### Profile system

`MACHINA_PROFILE=dev|prod` applies all security defaults at once:
- PROD: fsync=1, seccomp=1, genesis=off, guard=1, strict timeouts, http_default_deny=1, toolhost_isolate=1
- DEV: fsync=0, seccomp=0, genesis=on, generous timeouts
- seccomp profile: `strict` (default) or `net` (`MACHINA_SECCOMP_PROFILE=net`)

See `docs/OPERATIONS.md`.

---

## Observability

- `/metrics` endpoint: Prometheus text format (counters + gauges + per-tool metrics)
  - `machina_tool_ok_total{aid="..."}` — successful executions per tool AID
  - `machina_tool_fail_total{aid="..."}` — failed executions per tool AID
  - `machina_tool_duration_ms_total{aid="..."}` — cumulative execution time per tool
- `/stats` endpoint: JSON queue statistics
- Hash-chained audit logs (SHA-256, tamper-evident) with optional `request_id` tracing
- Telegram bot integration: `/auto_status` command, autonomic heartbeat alerts
- Python autonomic audit log: `autonomic_audit.jsonl` (supports `request_id` field)

---

## Self-improvement (v4.0)

- **Autonomic Engine v5**: 6-level GVU (Generator→Verifier→Updater) in `machina_autonomic/`
  - L1=Reflect(5min), L2=Test(5min), L3=Heal(30min), L4=Hygiene(30min), L5=Curiosity(30min), L6=WebExplore(30min)
- **LLM Self-Questioning Loop** (v4.0): intelligent self-directed actions replace static stimuli
  - LLM asks itself "what's the most valuable action right now?" based on current state
  - 4 action types: `search` (web), `test_tool` (dispatch), `code` (sandbox), `reflect` (insight)
  - Context-aware: sees recent experiences, skills, knowledge, past self-questions
  - Seed hints for diversity: randomly rotated prompts prevent action repetition
  - Safety: dedup + no-op detection + SQ cooldown/backoff, per-burst cap (3)
  - All actions produce real tool calls (no parametric-only learning)
- **Multi-Turn Burst Mode**: sustained autonomous work session (1hr max, stall detection)
  - Priority: Test(5) > Heal/Inbox(4) > Web(3) > Curiosity(2) > Reflect(1) > SelfQuestion(0)
  - L2 burst throttle: after 3 consecutive perfect passes, rate increases 8x
  - User activity interrupt (idle < 30s), configurable stall limit (5 turns)
- **Cloud Rate Factor**: always 1.0 (`_call_engine_llm` handles routing internally)
- **RandomStimulus** (fallback): 25 static pool items + dynamic LLM generation
  - Fires only when SelfQuestion limit reached or all levels idle
  - Knowledge query dedup: skip if same query in knowledge within 24h
- **Web Exploration**: autonomous DDGS web search → LLM summarize → knowledge stream
  - Topic selection from capability gaps + recent skills context
  - Rate-limited: 1hr (NORMAL), 15min (DEV)
- **Telegram Message Splitting**: code-fence-aware chunking (fence_count % 2 check)
  - Auto-close/reopen code blocks across chunk boundaries
  - Priority: code fence > paragraph > newline > space > hard cut
- **DEV Explore Mode** (`MACHINA_DEV_EXPLORE=1`): aggressive self-improvement timings
  - L1=1min, L2=2min, L3=3min, L5=3min idle; Curiosity 20/day (vs 3)
  - Proactive Telegram status reports, per-category rate limiting
- **Regression Gate**: E2E test suite blocks changes that reduce pass count
- **Reward Tracker**: rolling-window reward signal detects success_rate drops
- **Auto-Rollback**: automatic skill/util reversion when regression detected
- **Insight Dedup**: 5-min proximity + content match prevents duplicate tool_stats entries
- **Trust Scoring**: composite score (recency decay x quality) for experience/skill pruning
- **State Persistence**: `autonomic_state.json` saves/restores level_done + stasis across restarts
- **Log Size Management**: 2GB/file cap, 10GB total cap with automatic half-life rotation
- **4 Memory Streams**: experiences, insights, skills, knowledge (web exploration results)
- **Unified Audit Log**: `autonomic_audit.jsonl` with structured JSONL events + optional `request_id`
- **Telegram Alerts**: 2-tier pattern — `stream()` (DEV-only noise) + `milestone()` (always-on, rate-limited 60s)
- **Self-Evolution** (v6.2): `self_evolve_patch()` — gated by `MACHINA_SELF_EVOLVE=1`, 5-layer safety (path/match/backup/compile/rollback)
- **Silent Mode** (v6.2): SQ results → logger only (not Telegram), milestones handle important outcomes
- **Monotonically improving**: bad changes blocked, good changes accumulate

---

## Reality check

### True today
- Rollback + audit keep failures from corrupting state
- Operational hardening knobs exist (timeout/allowlist/rlimit/seccomp)
- External "brain" attach point exists (`MACHINA_POLICY_CMD`)
- Fast-path runtime queue exists in `serve` (CPQ + delayq)
- Restart recovery exists (processing recovery + checkpoint/WAL)
- Self-evolution (Genesis) works: write → compile → hash-verify → load
- Kernel-level sandboxing via seccomp-BPF (opt-in)
- Permission gating via lease system (opt-in)
- SSRF defense with DNS rebinding prevention (`curl --resolve`)
- Interactive chat mode with multi-step continuation
- bwrap namespace isolation for LLM-generated code

### Partially true
- "Self-loop 24/7": serve can run forever, but autonomy depends on policy/LLM quality
- "Gets smarter over time": experience/insight/skill learning with regression gate + self-evolution
- "Infinite memory": rotation + hybrid search + Graph Memory 2.0 (entity/relation/multi-hop)

### Not guaranteed by architecture alone
- Perfect sandboxing: seccomp + bwrap helps, but full isolation needs containers/nsjail
- Unbounded scalability: current queue is single-node

---

## Where to look in code

- `core/` : registry, selectors, rollback, plugin loader, sandbox, lease
- `tools/tier0/` : fs/shell/http/queue/genesis/memory/embed tools
- `runner/main.cpp` : `run`, `replay`, `autopilot`, `serve`, `chat`, `cts`, `tool_exec`
- `runner/cmd_serve.cpp` : HTTP server, WAL, workers, auth, rate limiting
- `runner/cmd_chat.cpp` : Interactive chat REPL (Pulse Loop)
- `runner/serve_http.h` : HTTP parsing, HMAC auth, nonce dedup, Slowloris defense
- `policies/` : policy driver templates + chat driver (intent/LLM/summary)
- `examples/policy_drivers/` : hello_policy.py + llm_http_policy.py (onboarding examples)
- `docs/` : ops + policy + architecture + serve API + quickstart
