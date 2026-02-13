# Policy Driver (External Selector) - Snapshot 6

This document describes how to connect an external policy process to Machina.

The objective is simple:
- call an external LLM/rule engine from the runtime
- receive a tool-selection decision in a strict selector format
- optionally attach an inputs patch with the selection
- support Genesis flows (write -> compile -> load) through policy decisions

---

## 1) Environment variables

- `MACHINA_POLICY_CMD`
  - Example: `python3 examples/policy_drivers/hello_policy.py`
  - In `POLICY_ONLY` mode, the runner executes this command and passes a JSON payload file path as `argv[1]`.

### Hardening options

`ExternalProcessSelector` supports timeout, allowlist, and best-effort resource limits.

- Timeout and output limit
  - `MACHINA_POLICY_TIMEOUT_MS` (DEV: `60000`, PROD: `30000`, code default: `2500`)
  - `MACHINA_POLICY_STDOUT_MAX` (default: `65536`)

- Allowlist controls
  - `MACHINA_POLICY_ALLOWED_EXE` (default: `python3,python,bash,sh,node`)
  - `MACHINA_POLICY_ALLOWED_SCRIPT_ROOT`
    - default: `<repo_root>/policies`
    - for included examples: set to `<repo_root>/examples/policy_drivers`
  - `MACHINA_POLICY_ALLOW_UNSAFE=1` disables allowlist enforcement (not recommended in production)

- Resource limits (best effort)
  - `MACHINA_POLICY_RLIMIT_CPU_SEC` (default: `2`)
  - `MACHINA_POLICY_RLIMIT_AS_MB` (default: `768`)
  - `MACHINA_POLICY_RLIMIT_FSIZE_MB` (default: `10`)
  - `MACHINA_POLICY_RLIMIT_NOFILE` (default: `64`)
  - `MACHINA_POLICY_RLIMIT_NPROC` (default: `32`)

---

## 2) Policy Driver I/O contract

### Input (`argv[1]`)

The policy driver reads the JSON payload file received as `argv[1]`.

Payload fields:
- `goal_digest`: `goal_id|menu_digest|FLAGS:...`
- `state_digest`: current DSState digest
- `control_mode`: e.g., `POLICY_ONLY`
- `inputs`: current step inputs (JSON object)
  - examples: `inputs.cmd`, `inputs.url`, `inputs.path`
- `menu`: candidate tool list
  - `sid`: menu-local selection ID (e.g., `SID0007`)
  - `aid`: tool AID (e.g., `AID.GENESIS.WRITE_FILE.v1`)
  - `name`, `tags`

### Output (`stdout`)

Print exactly one selector block:

- Pick
  - `<PICK><SID0007><END>`
- Pick + inputs patch
  - `<PICK><SID0007><INP>{...}</INP><END>`
  - `<PICK><SID0007><INP64>BASE64(JSON_OBJECT)</INP64><END>`
- Ask supervisor
  - `<ASK_SUP><END>`
- Stop
  - `<NOOP><END>`

`INP` and `INP64` payloads must be JSON objects.
The runner applies them via shallow merge (key overwrite) onto `inputs`.

---

## 3) Implementation notes

### (a) Selector implementation

- File: `core/src/selector_external.cpp`
- External process is invoked only in `ControlMode::POLICY_ONLY`
- `ControlMode::FALLBACK_ONLY` delegates to internal selector paths (heuristic/GPU)

### (b) Inputs patch flow

- `core/src/selector.cpp`: `parse_selector_output()` parses `<INP>` and `<INP64>`
- `runner/main.cpp`: merges patch into `inputs` and logs `inputs_patched`
- `replay_strict`: replays the same input path by consuming `inputs_patched`

### (c) `tool_ok.tx_patch` contract (replay-critical)

- `tool_ok.tx_patch` must be a JSON array
- Supported patch items:
  - `{"op":"add"|"replace","path":"/slots/<0..7>","value":{Artifact...}}`
  - `{"op":"remove","path":"/slots/<0..7>"}`
- `value` follows the Artifact schema: `type`, `provenance`, `content_json`, `size_bytes`
- `replay_strict` does not re-run non-deterministic tools (`deterministic=false`); it reconstructs DSState from `tx_patch`
- Invalid `op`/`path`/`value` shape results in `REPLAY_STRICT FAIL`

---

## 4) Demo

### Genesis + policy demo

```bash
./scripts/build_fast.sh

# Connect a policy driver
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"

# Run policy-only Genesis bootstrap
./scripts/run_demo.sh genesis_policy_codegen
```

Demo flow:
1. writes C++ tool source under `runtime_genesis/src`
2. compiles to `.so`
3. loads plugin into `runtime_plugins`
4. runs loaded runtime tool and produces DS output

---

## 5) Safety and operations notes

- Prefer local-only use for external process invocation.
- Keep trust boundaries explicit (allowlist + sandbox wrappers).
- Inputs patch is shallow merge only; add explicit diff/merge policy if you need deep merge semantics.

---

## 6) 24/7 operating topology (recommended)

Goal: keep an always-on loop where policy continues to generate next actions.

### Topology A: file queue + autopilot (simple and robust)

1. Start worker

```bash
./scripts/build_fast.sh
./build/machina_cli autopilot work/queue
```

2. Policy enqueues next jobs
- choose `AID.QUEUE.ENQUEUE.v1`
- include next `run_request` in `request_json`
- autopilot consumes from `inbox/` automatically

### Topology B: local HTTP adapter + autopilot

```bash
./build/machina_cli serve --host 127.0.0.1 --port 8080 --queue work/queue
./build/machina_cli autopilot work/queue
```

External orchestrators can push run requests via `POST /enqueue`.

---

## 7) Network restrictions (`HTTP_GET`)

`AID.NET.HTTP_GET.v1` can be constrained with:

- `MACHINA_HTTP_ALLOWED_HOSTS`
  - empty (default): allow all
  - set value: enforce allowlist
  - format: `example.com,api.openai.com,*.github.com,*`

---

## 8) LLM connection template

Included HTTP bridge template:

- `examples/policy_drivers/llm_http_policy.py`
  - POSTs payload to `MACHINA_POLICY_LLM_URL`
  - prints selector output from response (`machina_out` or `output`)

Example:

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
# optional: export MACHINA_POLICY_LLM_AUTH="Bearer ..."
```

---

## Circuit breaker (operational stability)

`ExternalProcessSelector` provides a circuit breaker for repeated policy failures.

- `MACHINA_POLICY_FAIL_THRESHOLD` (default `5`)
  - enter cooldown after this many consecutive failures
- `MACHINA_POLICY_COOLDOWN_MS` (default `30000`)
  - cooldown duration; fallback selector is used during this period

Failure conditions:
- process launch failure
- timeout
- non-zero exit code
- empty output
- invalid selector-contract output

During cooldown, behavior falls back to `ControlMode::FALLBACK_ONLY` path.

---

## Templates

### Engine policy drivers (tool selection)

Engine policy drivers use the `MACHINA_POLICY_CMD` external process protocol.
Legacy `policy_*.py` templates were removed in v6.3.
Custom drivers should follow the protocol above.

### Chat system drivers (interactive mode)

- `chat_driver.py` + `chat_driver_util.py`: 3-phase Pulse pipeline
- `chat_llm.py`: Ollama/Anthropic/OAI API call layer
- `chat_intent_map.py`: intent normalization and mapping

Chat config:
- `MACHINA_CHAT_CMD` (e.g. `python3 policies/chat_driver.py`)
- `MACHINA_CHAT_BACKEND` (`oai_compat` or `anthropic`)
- `MACHINA_CHAT_TIMEOUT_MS` (default `60000`)

See also: `docs/LLM_BACKENDS.md`.
