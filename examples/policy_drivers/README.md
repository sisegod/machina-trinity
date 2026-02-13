# Policy Driver Examples

External policy drivers connect to Machina's C++ runtime via the `MACHINA_POLICY_CMD` protocol.

## Protocol

1. Runner writes JSON payload to a temp file
2. Runner executes `$MACHINA_POLICY_CMD <path-to-payload.json>`
3. Driver reads payload, decides which tool to use, prints selection to stdout

### Output Format

| Action | Format |
|--------|--------|
| Pick a tool | `<PICK><SID0007><END>` |
| Pick + modify inputs | `<PICK><SID0007><INP>{"key":"val"}</INP><END>` |
| Ask human | `<ASK_SUP><END>` |
| Do nothing | `<NOOP><END>` |

## Examples

### hello_policy.py — Minimal (picks first tool)

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
./build/machina_cli run examples/run_request.gpu_smoke.json
```

### llm_http_policy.py — LLM-connected

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
./build/machina_cli run examples/run_request.gpu_smoke.json
```

## Payload Fields

| Field | Description |
|-------|-------------|
| `goal_digest` | `goal_id\|menu_digest\|FLAGS:...` |
| `state_digest` | Current DSState digest |
| `control_mode` | `POLICY_ONLY` etc. |
| `inputs` | Current step inputs (JSON object) |
| `menu` | Array of `{sid, aid, name, tags}` |

See `docs/POLICY_DRIVER.md` for full documentation.
