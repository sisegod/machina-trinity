<div align="center">

<br>

```
 ███╗   ███╗ █████╗  ██████╗██╗  ██╗██╗███╗   ██╗ █████╗
 ████╗ ████║██╔══██╗██╔════╝██║  ██║██║████╗  ██║██╔══██╗
 ██╔████╔██║███████║██║     ███████║██║██╔██╗ ██║███████║
 ██║╚██╔╝██║██╔══██║██║     ██╔══██║██║██║╚██╗██║██╔══██║
 ██║ ╚═╝ ██║██║  ██║╚██████╗██║  ██║██║██║ ╚████║██║  ██║
 ╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝
                    T R I N I T Y
```

**The agent runtime that assumes the LLM will fail.**

*C++20 safety core · Transactional execution · Cryptographic audit · Self-evolution*<br>
*9 layers of defense-in-depth · Deterministic replay · Runtime tool synthesis*

[![C++20](https://img.shields.io/badge/C%2B%2B-20-00599C?style=flat-square&logo=cplusplus)](https://isocpp.org/std/the-standard)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-14_C%2B%2B_·_34_E2E_·_10_replay-brightgreen?style=flat-square)](#testing)
[![License](https://img.shields.io/badge/license-Apache_2.0-blue?style=flat-square)](LICENSE)
[![v6.5](https://img.shields.io/badge/release-v6.5-orange?style=flat-square)](docs/ROADMAP.md)

<br>

</div>

---

<details>
<summary><strong>📑 Table of Contents</strong></summary>

- [The Problem](#the-problem)
- [How It Works](#how-it-works)
- [What Makes This Different](#what-makes-this-different)
  - [Transactional Tool Execution](#1-transactional-tool-execution)
  - [Nine Layers of Defense](#2-nine-layers-of-defense)
  - [Self-Evolution at Runtime](#3-self-evolution-at-runtime)
  - [Deterministic Replay](#4-deterministic-replay)
  - [Monotonic Self-Improvement](#5-the-system-keeps-getting-better)
- [Quick Start](#quick-start)
- [Language Versions](#language-versions)
- [Runtime Modes](#runtime-modes)
- [Control Modes](#control-modes)
- [Built-in Tools (40+)](#built-in-tools-40)
- [Python Agent Runtime](#python-agent-runtime)
- [Testing](#testing)
- [How It Compares](#how-it-compares)
- [Project Structure](#project-structure)
- [Documentation](#documentation)
- [LLM Support](#llm-support)
- [Security Notice](#security-notice)
- [Contributing](#contributing)

</details>

---

## Language Versions

Machina documentation now uses locale-oriented naming based on modern BCP-47 style.

| Locale | File | Status |
|--------|------|--------|
| English (`en`) | `README.md` | Source of truth |
| Korean (`ko-KR`) | `README.ko.md` | Maintained |
| Japanese (`ja-JP`) | `README.ja.md` | Maintained |
| Simplified Chinese (`zh-Hans-CN`) | `README.zh-CN.md` | Maintained |

Language strategy and expansion roadmap:
- `docs/LANGUAGE_STRATEGY_EN.md`
- `docs/ROADMAP.md`
- Full equivalent docsets: `docs/i18n/README.md`

---

## The Problem

Every agent framework gives an LLM a knife and hopes for the best.

LLM hallucinates `rm -rf /`? No rollback. Can't figure out why the agent broke at 3 AM? No audit trail. Tool spawns a subprocess that eats 32 GB of RAM? No resource limits. External API goes down? The whole system freezes.

These aren't edge cases. They're Tuesday.

**Machina starts from a different premise: the LLM *will* make mistakes.** The architecture's job is to make those mistakes cheap, traceable, and automatically recoverable — while still letting a capable model do genuinely autonomous work.

---

## How It Works

Machina splits the world into three concerns. They never mix.

```
                    ┌─────────────────────────────────────────────────────────┐
                    │                    MACHINA TRINITY                      │
                    │                                                         │
                    │   ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  │
                    │   │             │  │              │  │              │  │
                    │   │    BODY     │  │    DRIVER    │  │    MEMORY    │  │
                    │   │             │  │              │  │              │  │
                    │   │  Tx/Rollback│◄─┤  Heuristic   │  │  Hash-chain  │  │
                    │   │  Registry   │  │  LLM Policy  │  │  WAL/Ckpt   │  │
                    │   │  Sandbox    │  │  Circuit Brk  │  │  Replay     │  │
                    │   │  Lease      │  │  Fast Path   │  │  BM25+Vec   │  │
                    │   │             │  │              │  │              │  │
                    │   └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  │
                    │          │                │                 │          │
                    └──────────┼────────────────┼─────────────────┼──────────┘
                               │                │                 │
              ┌────────────────┼────────────────┼─────────────────┼────────┐
              │                ▼                ▼                 ▼        │
              │  ┌──────────────────────────────────────────────────────┐  │
              │  │              PYTHON AGENT RUNTIME                    │  │
              │  │                                                      │  │
              │  │  Telegram ──► Pulse Loop (Intent→Execute→Continue)   │  │
              │  │  Autonomic ─► 6-Level GVU (Reflect→Test→Heal→...)   │  │
              │  │  Learning ──► ExpeL · Reflexion · Distillation       │  │
              │  │  Memory ────► Graph 2.0 · 4 Streams · Multi-hop     │  │
              │  │  MCP ───────► External tool discovery & bridging     │  │
              │  │                                                      │  │
              │  └──────────────────────────────────────────────────────┘  │
              └───────────────────────────────────────────────────────────┘
```

**Body** executes tools inside transactions. If anything fails, state rolls back. The LLM never touches raw state.

**Driver** decides *what* to execute. Heuristic selector always works. LLM policy is optional and sandboxed behind a circuit breaker. If the LLM fails 3 times, the system degrades gracefully — it doesn't crash.

**Memory** records everything as SHA-256 hash-chained audit entries. Every run can be replayed deterministically. You can prove what happened, when, and why.

> **Design invariant:** The Body is always safe regardless of Driver quality. A bad LLM can pick the wrong tool, but it cannot corrupt state, bypass sandboxing, or break the audit chain.

---

## What Makes This Different

### 1. Transactional Tool Execution

Every tool call is wrapped in a transaction. Success → commit. Failure → rollback. State is never half-written.

```
Tool runs inside Tx
        │
        ├── Success → DS deltas committed
        │
        └── Failure → DS state rolled back (as if nothing happened)
```

No other agent framework does this. In LangChain, AutoGPT, or CrewAI, a failed tool call can leave your system in an undefined state.

### 2. Nine Layers of Defense

Not one safety mechanism. Nine, stacked:

```
Layer 1   Tx + Rollback ─────────────── State integrity
Layer 2   Hash-chained Audit ─────────── Tamper-evident history
Layer 3   Allowlists ─────────────────── Command restriction
Layer 4   seccomp-BPF ────────────────── Kernel syscall filtering
Layer 5   Permission Leases ──────────── Single-use privileged tokens
Layer 6   Plugin Hash Pinning ────────── SHA-256 before dlopen
Layer 7   Capability Gates ───────────── Bitmask permission model
Layer 8   SSRF Defense ───────────────── DNS rebinding prevention
Layer 9   CRC32 WAL Framing ──────────── Crash integrity detection
```

Plus: bwrap namespace isolation, Genesis source guard, nonce replay protection, HMAC request signing, rate limiting, and input sanitization (`safe_merge_patch` blocks LLM injection of system keys).

### 3. Self-Evolution at Runtime

Machina can write, compile, and hot-load new tools while running — through the Genesis pipeline:

```
Write source ──► Compile .so ──► SHA-256 verify ──► dlopen into registry
     │                │                │
     ▼                ▼                ▼
Source guard      Hash pinning    Capability gate
(blocks dangerous  (constant-time   (rejects plugins
 APIs/headers)     verification)    exceeding caps)
```

This is opt-in (`MACHINA_GENESIS_ENABLE=1`), off by default in production, and gated behind three independent safety checks. The system can grow new capabilities without restarting — but it can't grow dangerous ones.

### 4. Deterministic Replay

Every execution can be reproduced from logs:

```bash
./build/machina_cli replay_strict path/to/run.log
# Bit-exact reproduction of selections and outputs
# Non-deterministic tools replay via logged tx_patch
```

When something goes wrong at 3 AM, you don't grep through unstructured logs. You replay the exact execution, step by step, with the exact same state transitions.

### 5. The System Keeps Getting Better

The autonomic engine runs a 6-level self-improvement cycle:

```
L1 Reflect (5min)  ──► Analyze recent experiences
L2 Test    (5min)  ──► Run self-tests, find gaps
L3 Heal    (30min) ──► Auto-fix what's broken
L4 Hygiene (30min) ──► Clean logs, compact memory
L5 Curiosity(30min)──► Explore capability gaps
L6 Web     (30min) ──► Search and learn new knowledge
```

With three guarantees:

- **Regression Gate** — changes that reduce test pass count are blocked
- **Reward Tracker** — rolling-window success metrics detect degradation
- **Auto-Rollback** — bad changes revert automatically

The result: the system is *monotonically improving*. It either gets better or stays the same. It never gets worse.

---

## Quick Start

### Build (30 seconds)

```bash
# Prerequisites: build-essential, cmake 3.21+, libjson-c-dev
git clone https://github.com/sisegod/machina-trinity.git
cd machina-trinity

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)          # Linux
# cmake --build build -j$(sysctl -n hw.ncpu)  # macOS

# Verify: 14/14 tests should pass
cd build && ctest --output-on-failure && cd ..
```

### Run Without an LLM (zero dependencies)

```bash
# No LLM needed. Heuristic selector picks the right tool deterministically.
./build/machina_cli run examples/run_request.error_scan.json
# → Scans a CSV for "ERROR" patterns → produces structured report
# Control mode defaults to FALLBACK_ONLY when no policy is configured.
```

This is the fastest way to verify the system works. Transactional execution, audit logging, and replay all function without any LLM connection.

### Connect an LLM (5 minutes, optional)

```bash
# Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b

# Built-in local policy driver (repo includes this file)
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"

# Optional: HTTP LLM bridge policy driver
# export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
# export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
# export MACHINA_POLICY_LLM_AUTH="Bearer <token>"

# Run with LLM-driven tool selection (BLENDED via request JSON)
cat > /tmp/machina_blended.json << 'EOF'
{
  "goal_id": "goal.ERROR_SCAN.v1",
  "inputs": {"input_path": "examples/test.csv", "pattern": "ERROR", "max_rows": 1000000},
  "candidate_tags": ["tag.log", "tag.error", "tag.report"],
  "control_mode": "BLENDED"
}
EOF
./build/machina_cli run /tmp/machina_blended.json
```

### Deploy to Production

```bash
export MACHINA_PROFILE=prod    # One switch: fsync, seccomp, strict timeouts
export MACHINA_API_TOKEN="your-secret"
export MACHINA_API_HMAC_SECRET="your-hmac-secret"

./build/machina_cli serve --host 127.0.0.1 --port 9090 --workers 4

# Enqueue work
curl -X POST http://localhost:9090/enqueue \
  -H "Authorization: Bearer your-secret" \
  -d @examples/run_request.error_scan.json

# Observe
curl http://localhost:9090/metrics   # Prometheus format
curl http://localhost:9090/stats     # Queue statistics
```

> **`MACHINA_PROFILE=prod`** sets 7+ security defaults at once: fsync on, seccomp on, Genesis off, strict timeouts, HTTP default-deny, tool isolation enabled.

---

## Runtime Modes

| Mode | Command | Use Case |
|------|---------|----------|
| **Run** | `machina_cli run <request.json>` | Single request (batch/CI) |
| **Serve** | `machina_cli serve --workers N` | Production HTTP daemon with WAL + crash recovery |
| **Autopilot** | `machina_cli autopilot <dir>` | Disk queue worker pool |
| **Chat** | `machina_cli chat` | Interactive REPL with LLM intent parsing |
| **Replay** | `machina_cli replay_strict <log>` | Deterministic reproduction from logs |
| **CTS** | `machina_cli cts <manifest>` | Compliance Test Suite |
| **Tool Exec** | `machina_cli tool_exec <aid>` | Direct single-tool execution |

---

## Control Modes

| Mode | What Happens | When to Use |
|------|-------------|-------------|
| `FALLBACK_ONLY` | Heuristic picks tools. Deterministic. | No LLM available |
| `BLENDED` | LLM decides, heuristic catches failures. | **Recommended for production** |
| `POLICY_ONLY` | LLM picks everything. No fallback. | Strong model + high trust |
| `SHADOW_POLICY` | Heuristic runs, LLM output logged only. | A/B testing LLM quality |

---

## Built-in Tools (40+)

<details>
<summary><strong>C++ Core Tools (23)</strong></summary>

| Tool | AID | Description |
|------|-----|-------------|
| Error Scan | `AID.ERROR_SCAN.v1` | Pattern search in CSV/log files |
| Report Summary | `AID.REPORT_SUMMARY.v1` | Structured report generation |
| Shell Exec | `AID.SHELL.EXEC.v1` | Sandboxed command execution (allowlisted) |
| File Read/Write | `AID.FILE.READ/WRITE.v1` | Path-validated file I/O |
| HTTP Get | `AID.NET.HTTP_GET.v1` | HTTP requests with SSRF defense |
| Memory Append/Search/Query | `AID.MEMORY.*.v1` | BM25 + embedding hybrid search |
| Queue Enqueue | `AID.QUEUE.ENQUEUE.v1` | Disk queue work items |
| Genesis Write/Compile/Load | `AID.GENESIS.*.v1` | Runtime tool synthesis pipeline |
| Embed/VectorDB | `AID.EMBED/VECDB.*.v1` | Text embeddings + vector search |
| GPU Metrics/Smoke | `AID.GPU_*.v1` | NVIDIA GPU status |
| Proc Metrics | `AID.PROC.SELF_METRICS.v1` | Process resource usage |
| Ask Supervisor | `AID.ASK_SUP.v1` | Human-in-the-loop checkpoint |

</details>

<details>
<summary><strong>Python Tools (19)</strong></summary>

| Tool | AID | Description |
|------|-----|-------------|
| Code Exec | `AID.CODE.EXEC.v1` | Sandboxed Python/Bash (6-layer auto-fix) |
| File Ops | `AID.FILE.LIST/SEARCH/DIFF/EDIT/APPEND/DELETE.v1` | Full filesystem toolkit |
| Utility System | `AID.UTIL.SAVE/RUN/LIST/DELETE/UPDATE.v1` | Reusable script library |
| Web Search | `AID.NET.WEB_SEARCH.v1` | DuckDuckGo search |
| Project Create/Build | `AID.PROJECT.*.v1` | Multi-file C++/Python projects |
| Package Mgmt | `AID.SYSTEM.PIP_*.v1` | Isolated venv operations |

</details>

<details>
<summary><strong>MCP Bridge Tools</strong> <code>optional</code></summary>

External tools connected through the [Model Context Protocol](https://modelcontextprotocol.io/):

| Source | Example | Description |
|--------|---------|-------------|
| `web_search` | `AID.MCP.WEB_SEARCH.WEBSEARCHPRO.v1` | Web search via MCP |
| `web_reader` | `AID.MCP.WEB_READER.WEBREADER.v1` | URL content extraction |
| `zai` | `AID.MCP.ZAI.UI_TO_ARTIFACT.v1` | Image analysis, OCR, diagrams |

Configure in `mcp_servers.json`. Supports stdio, SSE, and streamable HTTP transports.

</details>

---

## Python Agent Runtime

The C++ core handles safety. Python handles intelligence.

```
Telegram ──► telegram_bot.py                           [optional]
              ├── telegram_bot_handlers.py ─── Message routing
              ├── telegram_bot_pulse.py ────── 3-phase Pulse pipeline
              │     └── chat_driver.py ──────── Intent → Execute → Continue
              ├── machina_dispatch.py ──────── 70+ tool aliases (KR/EN)
              ├── machina_autonomic/ ───────── Self-improving engine (10 files)
              │     ├── _engine.py ──────────── 6-level GVU cycle
              │     ├── _sq.py ─────────────── Self-questioning loop
              │     └── _stimulus.py ───────── Curiosity driver
              ├── machina_learning.py ──────── ExpeL · Reflexion · Distillation
              ├── machina_graph.py ─────────── Entity/relation graph + multi-hop BFS
              ├── machina_mcp.py ───────────── MCP bridge (external tools)  [optional]
              └── machina_permissions.py ───── 3-tier permission engine
```

**36 files, all ≤ 620 lines.** Strict size limit enforced.

### 3-Tier Intent Resolution

```
User message
     │
     ▼
FastPath ──── keyword hash match? ──► Execute (no LLM call)
     │ miss
     ▼
Distillation ── cached rule ≥0.8 confidence? ──► Execute
     │ miss
     ▼
LLM ──── full intent classification ──► Execute
```

Common operations (shell, file, search, memory) skip the LLM entirely. The system learns which intents map to which tools and caches those rules with a 10-minute TTL.

### Permission System

| Mode | Behavior |
|------|----------|
| `open` | All tools auto-allowed (dev) |
| `standard` | Safe = allow, dangerous = ask via Telegram (default) |
| `locked` | Read-only tools only |
| `supervised` | All non-read tools require approval |

Telegram sends inline keyboard buttons for approval (requires Telegram bot setup). Per-tool overrides via env or JSON config.

---

## Testing

```bash
# C++ unit tests (~2s)
cd build && ctest --output-on-failure   # 14/14 expected

# Python guardrail tests
scripts/run_guardrails.sh

# Full catalog smoke/regression suite
scripts/run_test_catalog.sh

# Replay helpers
scripts/replay_latest.sh
scripts/replay_strict_latest.sh
```

<details>
<summary><strong>C++ Test Suites (14)</strong></summary>

| Suite | Tests | What It Covers |
|-------|-------|----------------|
| CPQ | 4 | Concurrent priority queue thread safety |
| WAL | 3 | Write-ahead log + checkpoint/recovery |
| WAL Rotation | 3 | Segment rotation, retention limits |
| Tx | 5 | Transaction commit/rollback/replay |
| Tx Patch | 2 | tx_patch parser/apply contract |
| Memory | 4 | Append/query/rotation |
| Memory Query | 3 | BM25 + hybrid search |
| Toolhost | 3 | Plugin load/execute/isolation |
| GoalRegistry | 5 | Manifest parsing/validation |
| Input Safety | 12 | safe_merge_patch, capability filtering |
| Sandbox | 4 | seccomp-BPF syscall filtering |
| Lease | 5 | Permission lease lifecycle |
| Config | 6 | Profile detection/defaults |
| Plugin Hash | 3 | SHA-256 hash pinning |

</details>

<details>
<summary><strong>Python E2E Tests (34 cases, 13 groups)</strong></summary>

| Group | Tests | Coverage |
|-------|-------|----------|
| Chat Intent | 8 | Greetings, emotions, casual, context |
| Shell Command | 4 | GPU, memory, disk, process |
| Web Search | 4 | Price, weather, person, EN enforcement |
| Code Execution | 4 | Fibonacci, calc, sort, tables |
| Memory | 2 | Save + recall |
| File Operations | 2 | Read + write |
| Config | 2 | Backend + model switching |
| URL Fetch | 1 | HTTP GET |
| Utility System | 1 | Util list |
| Chat Response | 1 | Natural language generation |
| Summary | 1 | Tool result summarization |
| Continue Loop | 2 | Done + action continuation |
| Auto-Memory | 2 | Personal info detection + skip |

Plus 39 simulation scenarios (multi-step, adversarial LLM, crash recovery).

</details>

---

## How It Compares

Out-of-the-box capabilities — other frameworks may achieve some of these through additional tooling or configuration.

| | **Machina Trinity** | LangChain | AutoGPT | CrewAI |
|---|:---:|:---:|:---:|:---:|
| Transactional execution | ✅ | — | — | — |
| Cryptographic audit trail | ✅ | — | — | — |
| Deterministic replay | ✅ | — | — | — |
| Kernel-level sandboxing | ✅ seccomp-BPF | — | — | — |
| Permission leases | ✅ | — | — | — |
| Plugin hash verification | ✅ | — | — | — |
| Input sanitization | ✅ | — | — | — |
| Circuit breaker | ✅ | — | — | — |
| Runtime self-evolution | ✅ Genesis | — | — | — |
| Process isolation | ✅ fork+exec+bwrap | — | — | — |
| Prometheus /metrics | ✅ | — | — | — |
| One-switch profiles | ✅ dev/prod | — | — | — |
| Native C++ performance | ✅ | — | — | — |

> These projects serve different goals and excel in their own domains (LangChain's ecosystem breadth, CrewAI's multi-agent orchestration, etc.). This table highlights where Machina's safety-first architecture provides capabilities that would require significant additional work to replicate elsewhere.

---

## Project Structure

```
machina-trinity/
├── core/                    # C++ engine library
│   ├── include/machina/     #   26 public headers
│   ├── src/                 #   Implementation
│   └── cuda/                #   Optional CUDA kernels
│
├── runner/                  # C++ CLI + runtime modes
│   ├── cmd_run.cpp          #   Single request execution
│   ├── cmd_serve.cpp        #   HTTP daemon + WAL + workers
│   ├── cmd_chat.cpp         #   Interactive REPL (Pulse Loop)
│   └── serve_http.h         #   HTTP parsing, HMAC, rate limiting
│
├── tools/tier0/             # 23 built-in C++ tools
├── toolhost/                # Plugin host (NDJSON + fork modes)
│
├── machina_autonomic/       # Self-improving engine (10 files)
├── policies/                # Chat driver + LLM bridge (4 files)
│
├── machina_dispatch.py      # Tool dispatch facade (70+ aliases)
├── machina_learning.py      # ExpeL, Reflexion, Voyager
├── machina_graph.py         # Graph Memory 2.0
├── machina_mcp.py           # MCP bridge
├── machina_permissions.py   # 3-tier permission engine
├── telegram_bot.py          # Telegram bot interface
│
├── examples/                # Policy driver examples + quickstart
├── docs/                    # Architecture, operations, API, policy
└── scripts/                 # Build, guardrails, ops, replay helpers
```

---

## Documentation

| Document | What You'll Find |
|----------|-----------------|
| **[Architecture](docs/ARCHITECTURE.md)** | Trinity design, execution lifecycle, security model, module map |
| **[Operations](docs/OPERATIONS.md)** | Production deployment, profiles, hardening, environment variables |
| **[Serve API](docs/SERVE_API.md)** | HTTP endpoints, authentication, rate limiting |
| **[Policy Driver](docs/POLICY_DRIVER.md)** | LLM integration protocol, driver authoring guide |
| **[LLM Backends](docs/LLM_BACKENDS.md)** | Ollama, llama.cpp, Claude, OpenAI setup |
| **[Quick Start](docs/QUICKSTART.md)** | 10-minute build → configure → run guide |
| **[Language Strategy](docs/LANGUAGE_STRATEGY_EN.md)** | Locale policy, Telegram language status, multilingual rollout plan |

---

## LLM Support

Any OpenAI-compatible API works out of the box:

| Backend | Status |
|---------|--------|
| **Ollama** | Full support (recommended for local dev) |
| **llama.cpp** | Full support |
| **vLLM** | Full support |
| **OpenRouter** | Full support |
| **Anthropic Claude** | Native Messages API integration |

---

## Security Notice

Machina can execute shell commands and load arbitrary plugins. **Treat it as high-risk software.**

- Always run in a container or sandboxed environment in production
- Never expose `serve` to the public internet without authentication
- Keep credentials in `~/.config/machina/.secrets.env` (outside repo)
- See **[SECURITY.md](SECURITY.md)** for the security policy and vulnerability reporting

---

## Contributing

We welcome contributions. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for guidelines.

Priority areas: security hardening, new sandboxed tools, LLM policy improvements, documentation, test coverage.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

---

<div align="center">

*Built for a world where LLMs are powerful but imperfect.*

</div>
