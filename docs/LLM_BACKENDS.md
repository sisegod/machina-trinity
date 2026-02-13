# LLM backend templates (Policy Driver)

Machina’s **policy driver** is just a process:

- reads a JSON payload file path passed as **argv[1]** (fallback: stdin)
- prints **exactly one** selector action (and nothing else):
  - `<PICK><SID0001><END>`
  - `<NOOP><END>`
  - `<ASK_SUP><END>`

If your model outputs extra commentary, your policy should **extract the first valid selector block**.

---

## Payload shape (excerpt)

```json
{
  "goal_digest": "tag.shell|...",
  "state_digest": "...",
  "control_mode": "POLICY_ONLY",
  "inputs": {"cmd": "uname -a"},
  "menu": [{"sid":"0001","aid":"AID.SHELL.EXEC.v1","tags":["tag.shell"], ...}]
}
```

---

## Option 1: Generic HTTP bridge (recommended for local servers)

Use `examples/policy_drivers/llm_http_policy.py` as a starting point.
It reads a JSON payload and sends it to an HTTP policy endpoint:

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
export MACHINA_POLICY_LLM_AUTH=""    # Bearer token (optional)
export MACHINA_POLICY_TIMEOUT_MS=20000
```

Your server should return `{"machina_out":"<PICK><SID...><END>"}` (or `{"output":"..."}`).

---

## Option 2: Minimal hello-world policy

Use `examples/policy_drivers/hello_policy.py` to test the policy driver interface
without an LLM. It picks the first available SID:

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

---

## Option 3: Custom policy (any LLM backend)

Write your own policy driver following the protocol in `examples/policy_drivers/README.md`.
Any script that reads argv[1] JSON and prints a selector block works:

```bash
export MACHINA_POLICY_CMD="python3 your_custom_policy.py"
export MACHINA_POLICY_TIMEOUT_MS=30000
```

Supported selector outputs: `<PICK><SID0001><END>`, `<NOOP><END>`, `<ASK_SUP><END>`.

---

## Option 4: Ollama (recommended for local development)

Use an adapter service that talks to Ollama and returns Machina selector output:

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
export MACHINA_POLICY_TIMEOUT_MS=60000
```

Model switching at runtime (chat mode): use `/use <model_name>` command.

---

## Chat System

The chat system uses separate drivers from the engine policy:

```bash
export MACHINA_CHAT_CMD="python3 policies/chat_driver.py"
export MACHINA_CHAT_BACKEND="oai_compat"    # or "anthropic"
export MACHINA_CHAT_TIMEOUT_MS=60000
```

Chat drivers share the same LLM env vars (`OAI_COMPAT_*`, `ANTHROPIC_*`) as the engine policy.

### Claude-specific chat behavior

- **Intent classification**: Uses `temperature=0.0` for deterministic JSON output (overrides `MACHINA_CHAT_TEMPERATURE`)
- **Retry logic**: Automatically retries once on JSON parse failure or empty response
- **Continue judgment**: Uses `temperature=0.0` for reliable yes/no decisions
- **Error defense**: Raises `RuntimeError` on empty API response (never returns empty string silently)
- **Field sanitization**: Strips non-standard message fields (`_ts`, etc.) — Anthropic API rejects unknown keys
- **Message merging**: Consecutive same-role messages merged (Anthropic requires alternating user/assistant)
- **HTTPS warning**: Logs warning if `ANTHROPIC_BASE_URL` is non-HTTPS (API key plaintext risk)

Additional env vars for chat:
- `MACHINA_CHAT_MAX_TOKENS` (default: 1024) — max generation tokens
- `MACHINA_CHAT_TEMPERATURE` (default: 0.7) — conversation temperature (overridden to 0.0 for intent/continue)

---

## Safety tips (production)

- Keep policy scripts **outside the core** (portable + auditable).
- Add a "tool gate" inside the policy:
  - never pick `shell_exec` unless the goal explicitly allows it
  - prefer `fs_read` over `shell_exec cat`
- Use a hard timeout: `MACHINA_POLICY_TIMEOUT_MS`.
- Treat the LLM as **untrusted input**: parsing must be strict.
