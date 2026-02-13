# Quickstart (10 minutes)

Get Machina Trinity Legend running in under 10 minutes.

## 5분 핵심 루트 (자동 데모)

빌드 완료 후 한 줄로 Machina 체험:

```bash
python3 examples/quickstart_demo.py
```

이 스크립트가 자동으로:
1. C++ 바이너리 확인
2. hello_policy 드라이버로 도구 선택 실행
3. 셸 도구 직접 실행
4. 실행 로그 replay 검증

전체 과정 ~2분. 실패 시 해결 힌트 제공.

---

## Prerequisites

- Python 3.10+
- CMake 3.16+ and a C++20 compiler (g++-11 or clang-14)
- Ollama (for local LLM) or Anthropic API key (for Claude)
- Telegram bot token (from @BotFather)

## Step 1: Build C++ Runtime (2 min)

```bash
git clone <repo-url> machina_trinity_legend
cd machina_trinity_legend
./scripts/build_fast.sh          # or: mkdir build && cd build && cmake .. && make -j$(nproc)
cd build && ctest --output-on-failure   # 14/14 should pass
```

## Step 2: Configure Credentials (1 min)

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
# Option A: Local Ollama (free, needs GPU)
OAI_COMPAT_BASE_URL="http://localhost:11434/v1"
OAI_COMPAT_MODEL="qwen3:14b-q8_0"

# Option B: Anthropic Claude (paid, best quality)
ANTHROPIC_API_KEY="<YOUR_ANTHROPIC_API_KEY>"
ANTHROPIC_MODEL="claude-sonnet-4-5-20250929"
MACHINA_CHAT_BACKEND="anthropic"
```

## Step 3: Start the Bot (1 min)

```bash
source ~/.config/machina/.secrets.env
nohup python3 telegram_bot.py > /tmp/machina_bot.log 2>&1 &
```

Check startup:

```bash
head -30 /tmp/machina_bot.log
# Should see: "Application started", "MCP started: N server(s)"
# Should NOT see: [ERROR]
```

## Step 4: First Conversation (1 min)

Open Telegram, find your bot, send:

- `/start` -- initialize
- `hello` -- test greeting
- `what time is it?` -- test tool execution
- `/status` -- check system status

## Step 5: Try a Policy Driver (3 min)

Policy drivers connect external intelligence to the C++ runtime:

```bash
# Run with the hello policy (picks first available tool)
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
./build/machina_cli run examples/run_request.gpu_smoke.json
```

See `examples/policy_drivers/README.md` for more examples.

## Step 6: Verify Memory & Indexes (1 min)

```bash
python3 machina_reindex.py              # verify all memory streams
python3 machina_reindex.py --stats      # show line counts & sizes
```

## What's Next?

| Goal | Resource |
|------|----------|
| Understand architecture | `docs/ARCHITECTURE.md` |
| Configure operations | `docs/OPERATIONS.md` |
| Write custom policy | `docs/POLICY_DRIVER.md` |
| Switch LLM backends | `docs/LLM_BACKENDS.md` |
| Run autonomic engine | `MACHINA_DEV_EXPLORE=1` in `.secrets.env` |
| Check security | `SECURITY.md` |

## Environment Variables (Key Ones)

| Variable | Default | Description |
|----------|---------|-------------|
| `MACHINA_PROFILE` | `dev` | `dev` or `prod` (sets 9+ defaults) |
| `MACHINA_DEV_EXPLORE` | `0` | `1` = aggressive autonomic timings |
| `MACHINA_MAX_CYCLES` | `100` (dev) / `30` (prod) | Max Pulse loop cycles |
| `MACHINA_PULSE_BUDGET_S` | `3600` (dev) / `600` (prod) | Max seconds per conversation turn |
| `MACHINA_CHAT_BACKEND` | `oai_compat` | `oai_compat` or `anthropic` |
| `MACHINA_SELF_EVOLVE` | `0` | `1` = enable autonomous source patching |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Conflict: terminated by other getUpdates" | Kill all bot instances: `pkill -f telegram_bot.py` then restart |
| Bot doesn't respond | Check `/tmp/machina_bot.log` for errors |
| MCP tools not loading | Verify `mcp_servers.json` config, check `pip install` deps |
| Ollama timeout | Ensure Ollama is running: `ollama serve` |
