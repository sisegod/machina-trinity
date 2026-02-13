# Machina Trinity - Full Test Catalog

This catalog consolidates test coverage across runtime tools, goal execution,
selection logic, replay, queue/serve operation, security guardrails, and MCP integration.

Approximate coverage profile:
- 23+ built-in tools
- 5 registered goals
- 7 execution modes
- 14 C++ unit test suites
- 34 Python E2E tests
- 25 full pipeline scenarios
- 8 MCP bridge scenarios

## A. Individual tool tests (1-29)

Run tools directly to verify deterministic behavior and edge handling.

### A1. File system

| # | Tool | Scenario | Validation |
|---|------|----------|------------|
| 1 | `AID.FILE.READ.v1` | Read text file | content returned |
| 2 | `AID.FILE.READ.v1` | Missing file | structured error |
| 3 | `AID.FILE.READ.v1` | Binary file | base64 handling |
| 4 | `AID.FILE.READ.v1` | `max_bytes` limit | truncation applied |
| 5 | `AID.FILE.WRITE.v1` | Create file | file created under allowed root |
| 6 | `AID.FILE.WRITE.v1` | Overwrite toggle | overwrite behavior correct |
| 7 | `AID.FILE.WRITE.v1` | Nested path | mkdir behavior |
| 8 | `AID.FILE.WRITE.v1` | Path traversal attempt | blocked |

### A2. Shell execution

| # | Tool | Scenario | Validation |
|---|------|----------|------------|
| 9 | `AID.SHELL.EXEC.v1` | Basic command | output captured |
| 10 | `AID.SHELL.EXEC.v1` | Timeout | `timed_out=true` |
| 11 | `AID.SHELL.EXEC.v1` | Large output | truncation metadata |
| 12 | `AID.SHELL.EXEC.v1` | Non-allowlisted exe | blocked |
| 13 | `AID.SHELL.EXEC.v1` | Pipeline command | expected output |
| 14 | `AID.SHELL.EXEC.v1` | Env isolation | parent secrets not leaked |

### A3. Network

| # | Tool | Scenario | Validation |
|---|------|----------|------------|
| 15 | `AID.NET.HTTP_GET.v1` | Basic fetch | status/body returned |
| 16 | `AID.NET.HTTP_GET.v1` | Timeout | timeout path |
| 17 | `AID.NET.HTTP_GET.v1` | `max_bytes` | response capped |
| 18 | `AID.NET.HTTP_GET.v1` | Invalid URL | structured error |

### A4. GPU and process diagnostics

| # | Tool | Scenario | Validation |
|---|------|----------|------------|
| 19 | `AID.GPU_SMOKE.v1` | GPU presence check | `available`, `device_count` |
| 20 | `AID.GPU_METRICS.v1` | Detailed metrics | VRAM/temp/power/name |
| 21 | `AID.GPU_METRICS.v1` | No-GPU environment | graceful fallback |
| 22 | `AID.PROC.SELF_METRICS.v1` | Process self metrics | pid/rss/vmsize/fds |

### A5. Error scan and summary tools

| # | Tool | Scenario | Validation |
|---|------|----------|------------|
| 23 | `AID.ERROR_SCAN.v1` | CSV/log scan | matches reported |
| 24 | `AID.ERROR_SCAN.v1` | Empty file | zero matches |
| 25 | `AID.ERROR_SCAN.v1` | Large input | `max_rows` respected |
| 26 | `AID.REPORT_SUMMARY.v1` | summarize scan | DS summary slot populated |
| 27 | `AID.RUN.LOG.SUMMARY.v1` | run log summary | event counts/timing/chain check |
| 28 | `AID.NOOP.v1` | no-op action | success return |
| 29 | `AID.ASK_SUP.v1` | supervisor request | DS question artifact |

## B. Memory system tests (30-59)

### B1. JSONL memory streams

| # | Scenario | Validation |
|---|----------|------------|
| 30 | Append note | line persisted in stream |
| 31 | Append multiple events | order preserved |
| 32 | Stream isolation | notes/todo separated |
| 33 | JSON event append | object payload persisted |
| 34 | String contains search | matching lines returned |
| 35 | Search `limit` | capped output count |
| 36 | Missing keyword | zero count |
| 37 | Hybrid query | BM25+embed scoring fields |
| 38 | BM25-only query | BM25 score path |
| 39 | Embed-only query | embedding score path |
| 40 | VecDB mode | vector query path |
| 41 | Auto mode | mode selection works |
| 42 | top_k behavior | result cardinality changes |
| 43 | Rotation compatibility | search/query works post-rotation |

### B2. Vector DB behavior

| # | Scenario | Validation |
|---|----------|------------|
| 44 | Embedding generation | expected vector dimension |
| 45 | Explicit dimension | dim applied |
| 46 | Normalize option | norm near 1.0 |
| 47 | Hash provider path | deterministic vectors |
| 48 | External provider path | semantic vectors |
| 49 | Upsert text | vector stored |
| 50 | Metadata upsert | metadata retained |
| 51 | Semantic query | relevant results ranked high |
| 52 | Similarity ordering | sorted by score |
| 53 | top_k limit | output bounded |
| 54 | Empty DB query | empty/graceful result |

### B3. End-to-end memory recall

| # | Scenario | Validation |
|---|----------|------------|
| 55 | Append -> text search | exact recall |
| 56 | Append -> semantic query | semantic recall |
| 57 | Vec upsert -> vector query | vector recall |
| 58 | High-volume append/query | stable behavior |
| 59 | Cross-stream isolation | no leakage across streams |

## C. Genesis self-evolution tests (60-72)

| # | Scenario | Validation |
|---|----------|------------|
| 60 | Source write | `.cpp` emitted to runtime genesis src |
| 61 | Overwrite guard | overwrite disabled behavior |
| 62 | Path traversal defense | blocked |
| 63 | Compile shared | `.so` produced |
| 64 | Compile failure path | error persisted |
| 65 | Compile retries | retry budget respected |
| 66 | Plugin load | runtime registration succeeds |
| 67 | Immediate post-load use | new tool callable |
| 68 | Full pipeline | write -> compile -> load -> use |
| 69 | Demo request | `run_request.genesis_demo_hello.json` works |
| 70 | Missing tool autostub | autostub path executes |
| 71 | Autostub env toggle | env flags control behavior |
| 72 | Policy-guided generation | policy-only Genesis path |

## D. Goal execution tests (73-84)

### D1. Registered goals

| # | Goal | Validation |
|---|------|------------|
| 73 | `goal.ERROR_SCAN.v1` | goal done + report slot |
| 74 | `goal.GPU_SMOKE.v1` | goal done + DS output |
| 75 | `goal.GPU_METRICS.v1` | goal done + DS output |
| 76 | `goal.GENESIS_DEMO_HELLO.v1` | write/compile/load flow |
| 77 | `goal.DEMO.MISSING_TOOL.v1` | missing-tool recovery path |

### D2. Goal-loop mechanics

| # | Scenario | Validation |
|---|----------|------------|
| 78 | Step budget | breaker beyond max steps |
| 79 | Loop guard | repeated state/menu guard triggers |
| 80 | Invalid picks | invalid pick budget enforced |
| 81 | NOOP termination | clean stop |
| 82 | ASK_SUP termination | supervisor path |
| 83 | Tool error rollback | tx rollback and log |
| 84 | Runtime plugin reload | new `.so` discovered next step |

## E. Selector and control-mode tests (85-100)

| # | Scenario | Validation |
|---|----------|------------|
| 85 | HEURISTIC selector | deterministic selection |
| 86 | GPU_CENTROID selector | embedding-based selection |
| 87 | GPU centroid cache | cache hit behavior |
| 88 | `FALLBACK_ONLY` | no external policy dependency |
| 89 | `POLICY_ONLY` | external policy-only selection |
| 90 | `BLENDED` | policy-first, fallback on failure |
| 91 | `SHADOW_POLICY` | fallback execution + policy logging |
| 92 | `hello_policy.py` | first SID pick behavior |
| 93 | `llm_http_policy.py` | external HTTP policy bridge |
| 94 | `policies/chat_driver.py` | chat-plane LLM routing |
| 95 | circuit breaker trip | repeated policy failures -> cooldown |
| 96 | circuit breaker recovery | cooldown expiry retry |
| 97 | `<INP64>` merge | input patch merge applied |
| 98 | SID validation | invalid SID handling |
| 99 | policy timeout path | timeout fallback |
| 100 | output format validation | invalid selector output blocked |

## F. Transaction, audit, integrity tests (101-107)

| # | Scenario | Validation |
|---|----------|------------|
| 101 | Commit | tx commit applies to DS |
| 102 | Rollback | failed step leaves DS unchanged |
| 103 | Slot isolation | DS0..DS7 isolated |
| 104 | Hash chain | `chain_prev` links valid |
| 105 | Tamper detection | chain errors detected |
| 106 | State digest | SHA-256/FNV digests consistent |
| 107 | Patch serialization | `tx.patch_json()` structure valid |

## G. Replay reproducibility tests (108-118)

| # | Scenario | Command | Validation |
|---|----------|---------|------------|
| 108 | Structural replay | `machina_cli replay logs/run_*.jsonl` | key events present |
| 109 | Strict replay success | `machina_cli replay_strict ...` | deterministic replay passes |
| 110 | strict replay mismatch | modify patch/event | replay fails fast |
| 111 | inputs_patched propagation | replay strict | merged inputs reproduced |
| 112 | non-deterministic tool handling | replay strict | `tx_patch` reconstruction used |
| 113 | chain integrity in replay | replay summary | link errors surfaced |
| 114 | partial log replay | replay subset | graceful handling |
| 115 | multi-run selection | latest log replay | correct run picked |
| 116 | malformed log line | replay | robust error reporting |
| 117 | missing field fallback | replay | safe fallback behavior |
| 118 | replay latency sanity | replay timing | practical runtime bounds |

## H. Queue, autopilot, serve tests (119-130)

| # | Scenario | Validation |
|---|----------|------------|
| 119 | Enqueue basic | inbox file created |
| 120 | Worker pickup | queue item processed |
| 121 | Retry flow | backoff + retry queue |
| 122 | DLQ flow | exhausted retries -> DLQ |
| 123 | WAL persistence | restart restores pending state |
| 124 | Checkpoint restore | queue state recovery |
| 125 | Dedup by request_id | duplicate blocked |
| 126 | `/health` endpoint | liveness response |
| 127 | `/stats` auth gate | unauthorized blocked |
| 128 | `/metrics` export | Prometheus lines present |
| 129 | `/shutdown` auth gate | controlled shutdown |
| 130 | worker clamp | `--workers` bounded to 0..64 |

## I. CTS compatibility suite (131-133)

| # | Scenario | Validation |
|---|----------|------------|
| 131 | Toolpack + goalpack CTS | `CTS: OK` |
| 132 | GPU goalpack CTS | `CTS: OK` |
| 133 | Manifest schema checks | invalid manifests rejected |

## J. C++ unit suites (134-147)

Current expected suites:
- `test_cpq`
- `test_wal`
- `test_tx`
- `test_tx_patch_apply`
- `test_memory`
- `test_memory_query`
- `test_toolhost`
- `test_goal_registry`
- `test_input_safety`
- `test_sandbox`
- `test_lease`
- `test_wal_rotation`
- `test_config`
- `test_plugin_hash`

Run:

```bash
cd build && ctest --output-on-failure
```

## J-2. Python E2E suites (34 tests across 13 groups)

Primary grouped coverage:
1. chat intent
2. shell command routing
3. web search routing
4. code execution routing
5. memory save/query
6. file operations
7. config mutation
8. URL fetch
9. utility lifecycle
10. chat response
11. summarization
12. error handling
13. fallback behavior

Recommended command:

```bash
scripts/run_guardrails.sh
```

## K. Security tests

| # | Scenario | Validation |
|---|----------|------------|
| 148 | Permission modes | open/standard/locked/supervised behavior |
| 149 | Lease consumption | one-shot permissions consumed correctly |
| 150 | Unsafe command gate | ASK/DENY paths enforced |
| 151 | Secret pattern guard | plaintext key patterns blocked |
| 152 | Path traversal guard | fileops path normalization |
| 153 | Plugin hash pinning | mismatched hash rejected |

## L. Embedding system tests

| # | Scenario | Validation |
|---|----------|------------|
| 154 | Hash provider deterministic output | stable vectors |
| 155 | External provider timeout | fallback behavior |
| 156 | Mixed retrieval mode | hybrid query still returns results |

## M. Integrated scenario tests

| # | Scenario | Validation |
|---|----------|------------|
| 157 | Error scan full path | goal completion + summary |
| 158 | GPU smoke full path | device report generated |
| 159 | Genesis demo full path | runtime plugin created/loaded |
| 160 | Replay strict on latest log | reproducibility confirmation |
| 161 | Queue + serve + worker | async operation complete |

## N. Environment matrix tests

| # | Variable | Cases |
|---|----------|-------|
| 162 | `MACHINA_PROFILE` | `dev`, `prod` |
| 163 | `MACHINA_SELECTOR` | `HEURISTIC`, `GPU_CENTROID` |
| 164 | `MACHINA_CHAT_BACKEND` | `oai_compat`, `anthropic` |
| 165 | `MACHINA_POLICY_CMD` | hello policy, HTTP bridge policy |
| 166 | `MACHINA_EMBED_PROVIDER` | `hash`, `cmd` |

## O. Practical script runs

```bash
# Build + tests
./scripts/build_fast.sh
cd build && ctest --output-on-failure
cd ..

# Guardrails
./scripts/run_guardrails.sh

# Catalog smoke run
./scripts/run_test_catalog.sh

# Replay checks
./scripts/replay_latest.sh
./scripts/replay_strict_latest.sh
```

Policy selector examples:

```bash
# HTTP bridge policy
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
./build/machina_cli run examples/run_request.error_scan.json --control_mode POLICY_ONLY

# deterministic local policy
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
./build/machina_cli run examples/run_request.error_scan.json --control_mode BLENDED
```

## P. MCP bridge tests (8)

| # | Scenario | Validation |
|---|----------|------------|
| 1 | stdio connect | server connect and tool discovery |
| 2 | SSE connect | transport + tool list |
| 3 | MCP tool call | AID routing to MCP tool works |
| 4 | MCP timeout | timeout surfaced correctly |
| 5 | enable/disable server | runtime toggle works |
| 6 | add/remove server | config update and reconnect |
| 7 | reload | disconnect then reconnect |
| 8 | permission mapping | safe prefix allow/ask behavior |

## Q. Full pipeline tests (25)

Public package validation uses `scripts/run_test_catalog.sh` and
`scripts/run_guardrails.sh` together to validate intent -> dispatch -> execution paths
without relying on private local test assets.

Typical category split:
- greeting/chat: 5
- code execution: 4
- shell execution: 3
- web search: 2
- memory: 2
- file operations: 3
- URL fetch: 1
- config/tooling routes: 5

## Summary

This catalog is designed as an operational checklist, not only a QA artifact.
For release readiness, run:

1. `./scripts/build_fast.sh`
2. `cd build && ctest --output-on-failure`
3. `./scripts/run_guardrails.sh`
4. `./scripts/run_test_catalog.sh`
5. `./scripts/replay_strict_latest.sh`
