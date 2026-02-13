# Machina Trinity - Practical LLM and Embedding Setup Guide

Target hardware profile: RTX 3090 (24GB VRAM)
Estimated setup time: 1-2 hours (excluding model downloads)

## 0. Pre-check

```bash
cd /path/to/machina_trinity_legend

# Build and test runtime
mkdir -p build && cd build
cmake .. -DBUILD_TESTING=ON
make -j$(nproc)
ctest --output-on-failure
cd ..

# Python dependency for local embedding wrapper
pip install sentence-transformers
# optional: pip install onnxruntime-gpu
```

## 1. Embedding setup

### 1.1 Create embedding wrapper

```bash
mkdir -p tools/embed
cat > tools/embed/embed_e5.py << 'PYEOF'
#!/usr/bin/env python3
"""Machina embedding provider using intfloat/e5-small-v2.
stdin:  {"text":"...", "dim":384}
stdout: {"embedding":[...], "provider":"e5-small-v2"}
"""
import json
import sys
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("intfloat/e5-small-v2", device="cuda")
req = json.loads(sys.stdin.read())
text = req.get("text", "")
dim = req.get("dim", 384)

# e5 models work best with query prefix
vec = model.encode(f"query: {text}", normalize_embeddings=True).tolist()
if len(vec) > dim:
    vec = vec[:dim]

print(json.dumps({"embedding": vec, "provider": "e5-small-v2"}))
PYEOF
chmod +x tools/embed/embed_e5.py
```

### 1.2 Smoke test

```bash
echo '{"text":"error scan log analysis","dim":384}' | python3 tools/embed/embed_e5.py
```

### 1.3 Environment variables

```bash
export MACHINA_EMBED_PROVIDER=cmd
export MACHINA_EMBED_CMD="python3 tools/embed/embed_e5.py"
export MACHINA_EMBED_TIMEOUT_MS=30000
export MACHINA_GPU_DIM=384
```

## 2. LLM policy setup

Machina runtime policy selection uses an external driver command (`MACHINA_POLICY_CMD`).
In this package, the practical default is:

- `examples/policy_drivers/hello_policy.py` for deterministic local testing
- `examples/policy_drivers/llm_http_policy.py` for real LLM integration through your policy gateway

### 2.1 Option A: deterministic policy (no external LLM)

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

### 2.2 Option B: HTTP bridge policy (recommended)

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
# optional auth
# export MACHINA_POLICY_LLM_AUTH="Bearer <token>"
```

Expected HTTP response from your policy service:

```json
{"machina_out":"<PICK><SID0004><END>"}
```

## 3. Chat backend setup (separate from engine policy)

`policies/chat_driver.py` is used by Telegram/Pulse conversation mode.

### 3.1 Ollama chat backend

```bash
# install + pull model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:14b-q8_0

# chat backend env
export MACHINA_CHAT_CMD="python3 policies/chat_driver.py"
export MACHINA_CHAT_BACKEND=oai_compat
export OAI_COMPAT_BASE_URL="http://127.0.0.1:11434"
export OAI_COMPAT_MODEL="qwen3:14b-q8_0"
export OAI_COMPAT_TIMEOUT_SEC=60
```

### 3.2 Anthropic chat backend

```bash
export MACHINA_CHAT_CMD="python3 policies/chat_driver.py"
export MACHINA_CHAT_BACKEND=anthropic
export ANTHROPIC_API_KEY="<YOUR_ANTHROPIC_API_KEY>"
export ANTHROPIC_MODEL="claude-sonnet-4-5-20250929"
export ANTHROPIC_TIMEOUT_SEC=30
```

## 4. One-file environment bootstrap

Create `machina_env.sh` to avoid repeating exports:

```bash
cat > machina_env.sh << 'EOF_ENV'
#!/bin/bash
export MACHINA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Embedding
export MACHINA_EMBED_PROVIDER=cmd
export MACHINA_EMBED_CMD="python3 $MACHINA_ROOT/tools/embed/embed_e5.py"
export MACHINA_EMBED_TIMEOUT_MS=10000
export MACHINA_GPU_DIM=384

# Engine policy
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$MACHINA_ROOT/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 $MACHINA_ROOT/examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
export MACHINA_POLICY_LLM_AUTH=""

# Chat driver/backend
export MACHINA_CHAT_CMD="python3 $MACHINA_ROOT/policies/chat_driver.py"
export MACHINA_CHAT_BACKEND=oai_compat
export OAI_COMPAT_BASE_URL="http://127.0.0.1:11434"
export OAI_COMPAT_MODEL="qwen3:14b-q8_0"
export OAI_COMPAT_TIMEOUT_SEC=60
export OAI_COMPAT_MAX_TOKENS=256

# Selector and control defaults
export MACHINA_SELECTOR=GPU_CENTROID

echo "[machina] chat model=$OAI_COMPAT_MODEL @ $OAI_COMPAT_BASE_URL"
echo "[machina] policy endpoint=$MACHINA_POLICY_LLM_URL"
echo "[machina] embed provider=$MACHINA_EMBED_PROVIDER dim=$MACHINA_GPU_DIM"
EOF_ENV

chmod +x machina_env.sh
source machina_env.sh
```

## 5. Runtime checks

### 5.1 Fallback-only run (no external policy required)

```bash
./build/machina_cli run examples/run_request.error_scan.json
```

### 5.2 Blended run (policy + fallback)

```bash
cat > /tmp/machina_blended.json << 'JSON'
{
  "goal_id": "goal.ERROR_SCAN.v1",
  "inputs": {
    "input_path": "examples/test.csv",
    "pattern": "ERROR",
    "max_rows": 1000000
  },
  "candidate_tags": ["tag.log", "tag.error", "tag.report"],
  "control_mode": "BLENDED"
}
JSON

./build/machina_cli run /tmp/machina_blended.json
```

### 5.3 Policy-only run

```bash
cat > /tmp/machina_policy_only.json << 'JSON'
{
  "goal_id": "goal.ERROR_SCAN.v1",
  "inputs": {
    "input_path": "examples/test.csv",
    "pattern": "ERROR",
    "max_rows": 1000000
  },
  "candidate_tags": ["tag.log", "tag.error", "tag.report"],
  "control_mode": "POLICY_ONLY"
}
JSON

./build/machina_cli run /tmp/machina_policy_only.json
```

### 5.4 Direct policy driver test

```bash
cat > /tmp/policy_payload.json << 'JSON'
{"goal_id":"goal.ERROR_SCAN.v1","menu":[{"sid":"SID0004","aid":"AID.ERROR_SCAN.v1","name":"error_scan"}]}
JSON

MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers" \
MACHINA_POLICY_LLM_URL=http://127.0.0.1:9000/machina_policy \
python3 examples/policy_drivers/llm_http_policy.py /tmp/policy_payload.json
```

Expected output:

```text
<PICK><SID0004><END>
```

## 6. Troubleshooting

### External policy not responding

```bash
# verify policy endpoint
curl -s http://127.0.0.1:9000/health || true

# verify driver path allowlist
echo "$MACHINA_POLICY_ALLOWED_SCRIPT_ROOT"
```

### Chat backend timeout

```bash
# Ollama health
curl -s http://127.0.0.1:11434/v1/models | head

# verify chat backend vars
env | rg 'MACHINA_CHAT_BACKEND|OAI_COMPAT_|ANTHROPIC_'
```

### Embedding falls back to hash mode

```bash
rg "provider" logs/*.jsonl | tail -5
# "provider":"hash" means external embedding provider not attached
```

### GPU memory pressure

```bash
nvidia-smi
```

Use a smaller model if needed.

## 7. Key environment reference

### Embedding

| Variable | Default | Description |
|----------|---------|-------------|
| `MACHINA_EMBED_PROVIDER` | `hash` | `hash` or `cmd` |
| `MACHINA_EMBED_CMD` | (none) | external embedding command |
| `MACHINA_EMBED_TIMEOUT_MS` | `5000` | provider timeout |
| `MACHINA_EMBED_STDOUT_MAX` | `2097152` | max stdout bytes |
| `MACHINA_GPU_DIM` | `128` | vector dimension |

### Engine policy

| Variable | Default | Description |
|----------|---------|-------------|
| `MACHINA_POLICY_CMD` | (none) | policy driver command |
| `MACHINA_POLICY_ALLOWED_SCRIPT_ROOT` | `<repo>/policies` | allowed script root |
| `MACHINA_POLICY_TIMEOUT_MS` | `2500` | call timeout |
| `MACHINA_POLICY_FAIL_THRESHOLD` | `5` | circuit breaker threshold |
| `MACHINA_POLICY_COOLDOWN_MS` | `30000` | breaker cooldown |

### Chat backends

| Variable | Default | Description |
|----------|---------|-------------|
| `MACHINA_CHAT_BACKEND` | `oai_compat` | `oai_compat` or `anthropic` |
| `MACHINA_CHAT_MAX_TOKENS` | `4096` | max generation tokens |
| `MACHINA_CHAT_TEMPERATURE` | `0.7` | chat generation temperature |
| `OAI_COMPAT_BASE_URL` | `http://127.0.0.1:11434` | OAI-compatible endpoint |
| `OAI_COMPAT_MODEL` | `qwen3:14b-q8_0` | chat model name |
| `ANTHROPIC_API_KEY` | (required for Anthropic) | API key |
| `ANTHROPIC_MODEL` | `claude-opus-4-6` | Anthropic model |

## 8. Minimal cheat sheet

```bash
# terminal 1
ollama serve

# terminal 2
cd /path/to/machina_trinity_legend
source machina_env.sh
./build/machina_cli run examples/run_request.error_scan.json
```
