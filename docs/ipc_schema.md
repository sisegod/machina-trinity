# IPC JSON Schema — C++ / Python Boundary Contract

This document defines the JSON structures exchanged between the Python dispatch layer
(`machina_dispatch.py`) and the C++ toolhost (`toolhost/main.cpp`).

---

## 1. RunHeader (C++ `machina::RunHeader`)

Used by the runner/autopilot, not directly by toolhost IPC.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `spec_version` | string | `"1.4.2"` | Protocol version |
| `profile_id` | string | `"A"` | Profile identifier |
| `run_id` | string | required | UUID-like run identifier |
| `request_id` | string | optional | Caller-supplied tracing ID |

## 2. Selection (C++ `machina::Selection`)

Returned by selector (heuristic or external policy process).

| Field | Type | Description |
|-------|------|-------------|
| `kind` | enum | `PICK` / `ASK_SUP` / `NOOP` / `INVALID` |
| `sid` | string? | SID (e.g. `"SID0007"`), present only for `PICK` |
| `input_patch_json` | string? | Optional JSON object to merge into runner inputs |
| `raw` | string | Raw text returned by selector |

## 3. Toolhost Request (Python -> C++ stdin)

### 3a. Single-shot mode (`--run <plugin> <aid>`)

Entire stdin is consumed as one JSON object:

```json
{
  "input_json": "{\"query\": \"test\"}",
  "ds_state": { ... }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `input_json` | string | JSON-encoded tool input |
| `ds_state` | object? | Optional DSState snapshot |

### 3b. Serve mode (`--serve <plugin>`) — NDJSON

Each line is one request:

```json
{
  "aid": "AID.MEMORY.QUERY.v1",
  "input_json": "{\"query\": \"test\", \"k\": \"3\"}",
  "ds_state": { "delta": true, ... },
  "idempotency_key": "req-abc-123",
  "_lease_token": "tok_..."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `aid` | string | yes | Action ID of the tool to invoke |
| `input_json` | string | no | JSON-encoded tool input (default: `"{}"`) |
| `ds_state` | object | no | State snapshot or delta (`"delta": true`) |
| `idempotency_key` | string | no | Dedup key (cached 60s, max 1024 entries) |
| `_lease_token` | string | no | Single-use permission token (when `MACHINA_LEASE_ENFORCE=1`) |

### 3c. Python dispatch mode (no toolhost)

`machina_dispatch.py:run_machina_toolhost()` sends:

```json
{"aid": "AID.XX.YY.v1", "inputs": {"key": "value"}}
```

Note: Python dispatch uses `"inputs"` (dict), while C++ toolhost expects `"input_json"` (string).

## 4. Toolhost Response (C++ -> Python stdout)

### 4a. Success

```json
{
  "ok": true,
  "status": "OK",
  "output_json": "{\"result\": \"...\"}",
  "error": "",
  "ds_state": { ... }
}
```

### 4b. Error

```json
{
  "ok": false,
  "error": "tool not found: AID.XX"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `ok` | bool | `true` on success |
| `status` | string | `"OK"` / `"INVALID_PICK"` / `"TOOL_ERROR"` / `"BREAKER_TRIP"` |
| `output_json` | string | JSON-encoded tool output |
| `error` | string | Error message (empty on success) |
| `ds_state` | object | State delta (serve mode) or full state (run mode) |
| `idempotent_hit` | bool? | `true` if result was served from cache |

### 4c. Lease-specific errors (serve mode, `MACHINA_LEASE_ENFORCE=1`)

```json
{"ok": false, "error": "lease_required", "tier": 2, "aid": "AID.XX"}
{"ok": false, "error": "lease_rejected", "reason": "expired"}
```

## 5. Python Structured Error (machina_dispatch.py)

When `run_machina_toolhost()` encounters a failure, it returns a dict:

```json
{
  "error": true,
  "aid": "AID.XX.YY.v1",
  "type": "timeout|crash|parse_error|not_found|exception",
  "detail": "human-readable description"
}
```

| `type` | Meaning |
|--------|---------|
| `timeout` | Process exceeded `MACHINA_TOOLHOST_TIMEOUT` seconds |
| `crash` | Non-zero exit code |
| `parse_error` | Stdout was not valid JSON |
| `not_found` | Toolhost binary missing |
| `exception` | Python-side exception |

## 6. ToolResult (C++ `machina::ToolResult`)

Internal C++ struct, serialized in toolhost responses:

| Field | C++ Type | JSON Key | Description |
|-------|----------|----------|-------------|
| `status` | `StepStatus` enum | `"status"` | `OK` / `INVALID_PICK` / `TOOL_ERROR` / `BREAKER_TRIP` |
| `output_json` | `std::string` | `"output_json"` | Tool output as JSON string |
| `error` | `std::string` | `"error"` | Error description (empty on success) |

## 7. Constants

| Name | Value | Source |
|------|-------|--------|
| `MACHINA_TOOLHOST_TIMEOUT` | env or 30s | `machina_dispatch.py` |
| `_TOOLHOST_MAX_OUTPUT` | 1MB (1048576) | `machina_dispatch.py` |
| `MAX_STDIN_BYTES` | 10MB | `toolhost/main.cpp` |
| `IDEMP_DEFAULT_TTL_MS` | 60000ms | `toolhost/main.cpp` |
| `IDEMP_MAX_ENTRIES` | 1024 | `toolhost/main.cpp` |
