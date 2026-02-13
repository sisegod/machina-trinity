# Quickstart (10 minutes)

Get Machina Trinity Legend running in under 10 minutes.

## 2-minute fast path (automated demo)

After the build is complete, run one command:

```bash
python3 examples/quickstart_demo.py
```

The demo script automatically:
1. verifies required C++ binaries
2. runs tool selection with `hello_policy`
3. executes a direct shell tool invocation
4. validates replay on generated run logs

Typical run time is about 2 minutes.

---

## Prerequisites

- Python 3.10+
- CMake 3.21+ and a C++20 compiler (`g++-11` or `clang-14`)
- Ollama (local LLM) or Anthropic API key (Claude)
- Telegram bot token (from @BotFather)

## Step 1: Build C++ runtime (2 min)

```bash
git clone <repo-url> machina_trinity_legend
cd machina_trinity_legend
./scripts/install_deps.sh
# manual alternative: see docs/DEPENDENCIES.md
./scripts/build_fast.sh
cd build && ctest --output-on-failure
```

## Step 2: Configure credentials (1 min)

```bash
mkdir -p ~/.config/machina
cp .secrets.env.example ~/.config/machina/.secrets.env
chmod 600 ~/.config/machina/.secrets.env
```

Edit `~/.config/machina/.secrets.env`:

```bash
TELEGRAM_BOT_TOKEN="your-bot-token-from-botfather"
TELEGRAM_CHAT_ID="your-telegram-user-id"

# Pick ONE backend:
# Option A: Local Ollama
OAI_COMPAT_BASE_URL="http://localhost:11434/v1"
OAI_COMPAT_MODEL="qwen3:14b-q8_0"

# Option B: Anthropic Claude
ANTHROPIC_API_KEY="<YOUR_ANTHROPIC_API_KEY>"
ANTHROPIC_MODEL="claude-sonnet-4-5-20250929"
MACHINA_CHAT_BACKEND="anthropic"
```

## Step 3: Validate environment and start the bot (1 min)

```bash
./scripts/doctor.sh
nohup ./scripts/run_bot_forever.sh >/tmp/machina_bot.launcher.out 2>&1 &
```

Check startup logs:

```bash
head -30 /tmp/machina_bot.log
```

Expected:
- includes startup lines such as `Application started`
- no `[ERROR]` lines

## Step 4: First conversation (1 min)

Send in Telegram:

- `/start`
- `hello`
- `what time is it?`
- `/status`

## Step 5: Try a policy driver (3 min)

Policy drivers connect external intelligence to the C++ runtime.

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
./build/machina_cli run examples/run_request.gpu_smoke.json
```

See `examples/policy_drivers/README.md` for additional driver patterns.

## Step 6: Verify memory/indexes (1 min)

```bash
python3 machina_reindex.py
python3 machina_reindex.py --stats
```

## What's next?

| Goal | Resource |
|------|----------|
| Understand architecture | `docs/ARCHITECTURE.md` |
| Configure operations | `docs/OPERATIONS.md` |
| Write custom policy | `docs/POLICY_DRIVER.md` |
| Switch LLM backends | `docs/LLM_BACKENDS.md` |
| Run autonomic engine | set `MACHINA_DEV_EXPLORE=1` in `.secrets.env` |
| Security model | `SECURITY.md` |

## Key environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MACHINA_PROFILE` | `dev` | `dev` or `prod` profile |
| `MACHINA_DEV_EXPLORE` | `0` | `1` enables aggressive autonomic timings |
| `MACHINA_MAX_CYCLES` | `100`/`30` | max Pulse loop cycles (dev/prod) |
| `MACHINA_PULSE_BUDGET_S` | `3600`/`600` | max seconds per turn (dev/prod) |
| `MACHINA_CHAT_BACKEND` | `oai_compat` | `oai_compat` or `anthropic` |
| `MACHINA_SELF_EVOLVE` | `0` | `1` enables autonomous source patching |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| terminated by other getUpdates | `pkill -f telegram_bot.py` then restart |
| Bot not responding | run `./scripts/doctor.sh` then inspect `/tmp/machina_bot.log` |
| MCP tools not loading | verify `mcp_servers.json` and dependencies |
| Ollama timeout | make sure `ollama serve` is running |
