# Roadmap (design → production)

This repo intentionally separates **rails** (core safety + tooling) from **intelligence** (policy).

## Shipped (v6.5, 2026-02-12)

### Infrastructure
- Disk queue + retry/DLQ + exponential backoff
- Autopilot worker pool
- Daemon mode: `serve --workers N` with in-memory CPQ fast path + disk persistence
- WAL with auto-rotation (16MB/1h segments, configurable retention)
- Prometheus `/metrics` endpoint (counters + gauges)

### Security
- seccomp-BPF syscall allowlist (x86_64 + aarch64, opt-in)
- Permission leases: 4-tier single-use tokens
- Plugin SHA-256 hash pinning with constant-time verification
- Profile system: `MACHINA_PROFILE=dev|prod` one-switch configuration
- Tool idempotency guard (LRU 1024, 60s TTL)

### Tools & Execution
- 23 built-in tools, plugin system (.so), Genesis self-evolution pipeline
- External policy driver + robust parsing
- Inputs forwarded to policy payload
- Memory: JSONL + BM25 + hash embedding hybrid search, MMR reranker

### Telegram Bot Layer (Python — 34 production files)
- **v6.0 Modular Split**: 40→33 production files (ALL ≤620 lines)
- Chat Driver v3 "Pulse": 3-phase pipeline (intent→execute→continue, profile-aware: DEV 100/3600s, PROD 30/600s)
- 6-layer code auto-fix (fence strip → input() → f-string → colon → indent → print())
- Autonomic Engine v5: 6-level GVU (Reflect→Test→Heal→Hygiene→Curiosity→WebExplore)
- **machina_autonomic/** package: 10 files (_engine, _levels, _burst, _ops, _sq, _stimulus, _random_stimulus, _web, _constants, __init__)
- **RegressionGate**: E2E suite gating — blocks changes that reduce test pass count
- **RewardTracker**: Rolling-window reward signal (100-exp windows, 5% threshold)
- **Auto-Rollback**: Automatic skill/util reversion when regression detected
- **LLM Self-Questioning Loop**: intelligent self-directed actions (search/test_tool/code/reflect/audit)
- **Multi-Turn Burst Mode**: sustained autonomous work (30min PROD / 1hr DEV, priority scheduler, stall detection)
- **DEV Explore Mode**: aggressive timings (L1=1min, Curiosity 20/day vs 3)
- **State Persistence**: `autonomic_state.json` saves/restores across restarts
- **Log Size Management**: 2GB/file cap, 10GB total cap, automatic half-life rotation
- **4 Memory Streams**: experiences, insights, skills, knowledge
- **Graph Memory 2.0**: entity extraction + relation graph + multi-hop BFS + time decay
- **Permission System**: 3-tier (ALLOW/ASK/DENY), 4 modes, per-tool overrides
- **70+ tool aliases** (Korean + English + legacy) + tool descriptions
- **MCP Bridge**: dynamic tool discovery from MCP servers → AID mapping
- **Anthropic Claude integration**: _extract_json_robust (3-layer), prompt caching, low temperature
- **2-tier Telegram alerts**: stream (DEV) + milestone (always, rate-limited 60s)
- **OTel trace context**: trace_id/span_id propagated through all audit logs
- **Self-Evolution** (v6.2): `self_evolve_patch()` with 5-layer safety (MACHINA_SELF_EVOLVE=1)
- **Silent SQ mode** (v6.2): SQ results → logger only, milestones for important outcomes
- **Extended timeouts** (v6.2): code 60s (MACHINA_CODE_TIMEOUT), healer 30s, fileops 60s
- **18 Telegram commands**: core(8) + ext(9) + /stop
- **Guardrails default ON** (v6.4): CODE_EXEC=ASK, 512KB output warning, 1MB truncation
- **JSONL reindex** (v6.4): `machina_reindex.py` — one-click verify/fix/stats for 6 memory streams
- **Policy examples** (v6.4): `examples/policy_drivers/` — hello_policy.py + llm_http_policy.py
- **Onboarding** (v6.4): `docs/QUICKSTART.md` — 10-minute build→configure→run guide
- **LLM-free Fast Path** (v6.5): keyword hash-based intent routing skips LLM for common ops (shell/file/search/memory)
- **Policy Distillation** (v6.5): `distill_rules()` + `lookup_distilled()` — experience→rule cache (10min TTL, ≥0.8 confidence gate)
- **Safe Mutation Contract** (v6.5): FILE.WRITE atomic write (tmp→fsync→rename+.bak)
- **Golden Replay Test** (v6.5): in the public package, use `scripts/replay_strict_latest.sh` for the same structural validation goal
- **Quickstart Demo** (v6.5): `examples/quickstart_demo.py` — 4-step automated onboarding
- 34/34 E2E tests passing (Qwen3 14B Q8)

### Testing
- 14 C++ unit test suites (cpq, wal, tx, tx_patch_apply, memory, memory_query, toolhost, goal_registry, input_safety, sandbox, lease, wal_rotation, config, plugin_hash)
- 34 Python E2E tests across 13 groups
- 10 Golden Replay tests (structural intent validation)
- 39 simulation scenarios

## Next big axes

1) **Memory 2.0 (graph + tiered)** — SHIPPED (v6.0)
   - ~~add: graph memory (entities/relations)~~ `machina_graph.py`: entity extraction (Tier0 regex + Tier1 noun chunks), JSONL-backed relation graph, multi-hop BFS, exponential time decay (30-day half-life)
   - ~~add: retention policies + compaction~~ Auto-compaction (200 appends), entity/relation pruning (5K/20K limits)
   - ~~add: cross-stream correlation~~ Graph auto-populated from all memory saves, experiences, auto-memory, and telegram conversations

2) **Self-improvement ~~that actually improves~~ SHIPPED (v3.5)**
   - ~~nightly replay tests (regression gate)~~ RegressionGate in machina_gvu.py
   - skill discovery + tool synthesis prompts
   - ~~reward signals (success metrics) written to memory~~ RewardTracker in machina_learning.py

3) **Distributed scaling**
   - multi-node queue coordination
   - plugin federation

4) **Portability**
   - Windows parity for daemon + sandbox
   - plugin ABI stability
