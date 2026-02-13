# Machina Trinity — LLM + 임베딩 실전 세팅 가이드

> **대상 하드웨어:** RTX 3090 (24GB VRAM)
> **소요 시간:** 약 1~2시간 (다운로드 시간 제외)


## 0. 사전 준비

```bash
# 현재 위치 확인
cd /path/to/machina_trinity_legend

# 빌드 확인 (이미 되어있으면 스킵)
mkdir -p build && cd build
cmake .. -DBUILD_TESTING=ON
make -j$(nproc)
ctest --output-on-failure
cd ..

# Python 의존성
pip install sentence-transformers   # 임베딩용
# (옵션) pip install onnxruntime-gpu  # 나중에 네이티브 최적화할 때
```


## 1. 임베딩 모델 설치 + 연동

### 1-1. 임베딩 래퍼 스크립트 생성

```bash
mkdir -p tools/embed
cat > tools/embed/embed_e5.py << 'PYEOF'
#!/usr/bin/env python3
"""Machina embedding provider — intfloat/e5-small-v2
stdin:  {"text":"...", "dim":384}
stdout: {"embedding":[...], "provider":"e5-small-v2"}
"""
import json, sys

# 모델은 첫 호출 시 자동 다운로드 (~130MB), 이후 캐시
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("intfloat/e5-small-v2", device="cuda")

req = json.loads(sys.stdin.read())
text = req.get("text", "")
dim = req.get("dim", 384)

# e5 모델은 "query: " 프리픽스를 붙여야 성능이 나옴
vec = model.encode(f"query: {text}", normalize_embeddings=True).tolist()
if len(vec) > dim:
    vec = vec[:dim]

print(json.dumps({"embedding": vec, "provider": "e5-small-v2"}))
PYEOF
chmod +x tools/embed/embed_e5.py
```

### 1-2. 동작 확인

```bash
echo '{"text":"error scan log analysis","dim":384}' | python3 tools/embed/embed_e5.py
# → {"embedding": [0.023, -0.045, ...], "provider": "e5-small-v2"}
# 첫 실행은 모델 다운로드 때문에 느림. 두 번째부터 ~0.1초
```

### 1-3. 환경변수 설정

```bash
export MACHINA_EMBED_PROVIDER=cmd
export MACHINA_EMBED_CMD="python3 tools/embed/embed_e5.py"
export MACHINA_EMBED_TIMEOUT_MS=30000      # 첫 로딩 느리므로 30초 (PyTorch 초기화)
export MACHINA_GPU_DIM=384                 # e5-small 차원
```

### 1-4. (선택) 임베딩 서버 모드 — 매번 모델 로딩 안 하기

위 방식은 매 호출마다 Python + 모델 로딩이 발생한다.
서버 모드로 바꾸면 모델을 한 번만 로딩하고 재사용:

```bash
cat > tools/embed/embed_server.py << 'PYEOF'
#!/usr/bin/env python3
"""상주형 임베딩 서버 — NDJSON stdin/stdout"""
import json, sys
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("intfloat/e5-small-v2", device="cuda")
print("ready", file=sys.stderr, flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        break
    try:
        req = json.loads(line)
        text = req.get("text", "")
        dim = req.get("dim", 384)
        vec = model.encode(f"query: {text}", normalize_embeddings=True).tolist()[:dim]
        print(json.dumps({"embedding": vec, "provider": "e5-small-v2"}), flush=True)
    except Exception as e:
        print(json.dumps({"embedding": [], "provider": "e5-error", "error": str(e)}), flush=True)
PYEOF
chmod +x tools/embed/embed_server.py
```

서버 모드는 현재 `embedding_provider.cpp`의 cmd 모드가 매번 fork하는 구조이므로,
아직 직접 연결은 안 된다. **당장은 1-1의 기본 방식으로 충분하다.**
나중에 `MACHINA_EMBED_PROVIDER=server` 모드를 C++ 쪽에 추가하면 된다.


## 2. LLM 설치 + 연동

3가지 옵션이 있다. **추천 순서대로** 설명.

---

### 옵션 A: Ollama (가장 쉬움, 5분)

```bash
# 1. Ollama 설치
curl -fsSL https://ollama.com/install.sh | sh

# 2. 모델 다운로드 (Llama 3.1 8B — ~4.7GB)
ollama pull llama3.1:8b

# 3. 서버 시작 (기본 포트 11434)
ollama serve &
# 또는 systemd로 자동 시작: systemctl start ollama

# 4. 동작 확인
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.1:8b",
    "messages": [{"role":"user","content":"say hello"}],
    "temperature": 0,
    "max_tokens": 50
  }'
```

**Machina 연결:**

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
```

`llm_http_policy.py`는 Machina 정책 payload를 받아 `{"machina_out":"<PICK><SID...><END>"}`를 반환하는 HTTP 엔드포인트를 호출한다.

**다른 모델 옵션:**

```bash
ollama pull qwen2.5:7b          # 한국어 강함, ~4.4GB
ollama pull mistral:7b           # 빠름, ~4.1GB
ollama pull llama3.1:70b-q4_0   # 70B 4bit, ~40GB (VRAM 부족하면 CPU 오프로드)
ollama pull deepseek-r1:8b      # 추론 강함
```

모델 변경은 **policy gateway 쪽 설정**(또는 gateway가 읽는 환경변수)에서 조정한다.

---

### 옵션 B: llama.cpp 직접 빌드 (성능 최적화, 30분)

```bash
# 1. 빌드
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON
cmake --build build --config Release -j$(nproc)

# 2. 모델 다운로드 (Hugging Face에서 GGUF)
# Llama 3.1 8B Q4_K_M (~4.7GB)
wget https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf

# Qwen2.5 7B Q4_K_M (~4.4GB, 한국어 강함)
# wget https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf

# 3. 서버 시작
./build/bin/llama-server \
  -m Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf \
  -ngl 99 \
  -c 8192 \
  --host 0.0.0.0 \
  --port 8080
# -ngl 99: 모든 레이어 GPU 오프로드 (3090이면 8B 모델 전부 올라감)
# -c 8192: 컨텍스트 윈도우

# 4. 동작 확인
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local",
    "messages": [{"role":"user","content":"say hello"}],
    "temperature": 0
  }'
```

**Machina 연결:**

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
```

---

### 옵션 C: Claude API (가장 똑똑함, API 키 필요)

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="https://your-policy-gateway.example/machina_policy"
export MACHINA_POLICY_LLM_AUTH="Bearer <YOUR_ANTHROPIC_API_KEY>"
```

비용: 약 $0.003/호출 (Sonnet). 테스트 시에만 쓰고 프로덕션은 로컬 LLM 추천.


## 3. VRAM 배분 (RTX 3090 기준)

```
┌──────────────────────────────────────────────┐
│  RTX 3090 — 24GB VRAM                        │
├──────────────────────────────────────────────┤
│  OS + CUDA 오버헤드        ~1.5 GB           │
│  e5-small-v2 (임베딩)      ~0.5 GB           │
│  Llama 3.1 8B Q4_K_M       ~6.5 GB           │
│  KV Cache (8K context)     ~2.0 GB           │
│  ─────────────────────────────────            │
│  합계                      ~10.5 GB          │
│  남는 VRAM                 ~13.5 GB  ✅       │
└──────────────────────────────────────────────┘
```

더 큰 모델 쓰고 싶으면:

| 모델 | VRAM | 남는 것 | 가능? |
|------|------|---------|-------|
| Llama 3.1 8B Q4 | ~6.5GB | ~13.5GB | ✅ 여유 |
| Qwen2.5 14B Q4 | ~9GB | ~11GB | ✅ 가능 |
| Llama 3.1 70B Q4 | ~40GB | ❌ | CPU 오프로드 필요 |
| Mixtral 8x7B Q4 | ~26GB | ❌ | CPU 오프로드 필요 |


## 4. 통합 실행

### 4-1. 환경변수 한방 세팅 (매번 치기 귀찮으니 파일로)

```bash
cat > machina_env.sh << 'EOF'
#!/bin/bash
# ── Machina Trinity 환경변수 ──

# 프로젝트 루트
export MACHINA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 임베딩 ──
export MACHINA_EMBED_PROVIDER=cmd
export MACHINA_EMBED_CMD="python3 $MACHINA_ROOT/tools/embed/embed_e5.py"
export MACHINA_EMBED_TIMEOUT_MS=10000
export MACHINA_GPU_DIM=384

# ── LLM Policy (외부 정책 엔드포인트 연동) ──
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$MACHINA_ROOT/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 $MACHINA_ROOT/examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
export MACHINA_POLICY_LLM_AUTH=""

# ── Chat 설정 (Ollama/llama.cpp 직접 호출용) ──
export OAI_COMPAT_BASE_URL="http://127.0.0.1:11434"
export OAI_COMPAT_MODEL="qwen3:14b-q8_0"   # or llama3.1:8b
export OAI_COMPAT_TIMEOUT_SEC=60
export OAI_COMPAT_MAX_TOKENS=256

export MACHINA_CHAT_CMD="python3 $MACHINA_ROOT/policies/chat_driver.py"
export MACHINA_CHAT_BACKEND=oai_compat
export MACHINA_CHAT_TIMEOUT_MS=60000

# ── 컨트롤 모드 ──
# FALLBACK_ONLY : 휴리스틱만 (LLM 안 씀)
# POLICY_ONLY   : LLM만
# BLENDED       : LLM 우선, 실패 시 휴리스틱 (추천)
# SHADOW_POLICY : 휴리스틱 실행, LLM은 로그만

# ── 셀렉터 백엔드 ──
export MACHINA_SELECTOR=GPU_CENTROID    # 임베딩 기반 (hash fallback 있음)

# ── Genesis (자기진화, 필요할 때만) ──
# export MACHINA_GENESIS_ENABLE=1

# ── 서버 (serve 모드용) ──
# export MACHINA_SERVE_PORT=9090
# export MACHINA_SERVE_HOST=127.0.0.1
# export MACHINA_API_TOKEN=your-secret-token

echo "[machina] env loaded: CHAT_LLM=$OAI_COMPAT_MODEL @ $OAI_COMPAT_BASE_URL"
echo "[machina] policy endpoint=$MACHINA_POLICY_LLM_URL"
echo "[machina] embed=$MACHINA_EMBED_PROVIDER dim=$MACHINA_GPU_DIM"
EOF

chmod +x machina_env.sh
```

**사용:**

```bash
source machina_env.sh
```

### 4-2. 첫 실행 — FALLBACK_ONLY (LLM 없이)

```bash
source machina_env.sh
./build/machina_cli run examples/run_request.error_scan.json
# → 휴리스틱이 도구 선택, 정상 완료 확인
```

### 4-3. LLM 연동 실행 — BLENDED

```bash
# Ollama 서버가 돌고 있는지 확인
curl -s http://localhost:11434/v1/models | head -5

# control_mode를 BLENDED로 바꿔서 실행
cat > /tmp/test_blended.json << 'JSON'
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

./build/machina_cli run /tmp/test_blended.json
```

성공하면 로그에 이런 게 보인다:

```
[policy] external cmd returned: <PICK><SID0004><END>
[step 1] selected SID=0004 (AID.ERROR_SCAN.v1) via policy
```

### 4-4. POLICY_ONLY (LLM만)

```bash
cat > /tmp/test_policy_only.json << 'JSON'
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

./build/machina_cli run /tmp/test_policy_only.json
```

### 4-5. GPU Smoke 테스트

```bash
cat > /tmp/test_gpu.json << 'JSON'
{
  "goal_id": "goal.GPU_SMOKE.v1",
  "inputs": {},
  "candidate_tags": ["tag.gpu", "tag.smoke"],
  "control_mode": "BLENDED"
}
JSON

./build/machina_cli run /tmp/test_gpu.json
```


## 5. 트러블슈팅

### LLM이 응답 안 함

```bash
# 1. Ollama 서버 상태 확인
curl http://localhost:11434/v1/models

# 2. 직접 호출 테스트
echo '{"model":"llama3.1:8b","messages":[{"role":"user","content":"hello"}],"max_tokens":50}' | \
  curl -s -X POST http://localhost:11434/v1/chat/completions \
    -H "Content-Type: application/json" -d @-

# 3. policy 스크립트 단독 테스트 (payload 파일 인자 전달)
cat > /tmp/policy_payload.json << 'JSON'
{"goal_id":"goal.ERROR_SCAN.v1","menu":[{"sid":"SID0004","aid":"AID.ERROR_SCAN.v1","name":"error_scan"}]}
JSON
MACHINA_POLICY_LLM_URL=http://127.0.0.1:9000/machina_policy \
  MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers" \
  python3 examples/policy_drivers/llm_http_policy.py /tmp/policy_payload.json
# → <PICK><SID0004><END> 같은 출력이 나와야 함
```

### 임베딩이 hash fallback으로 빠짐

```bash
# 로그에서 확인
grep "provider" logs/*.jsonl | tail -5
# "provider":"hash" → 임베딩 모델 안 붙음
# "provider":"e5-small-v2" → 정상

# 직접 테스트
echo '{"text":"test","dim":384}' | python3 tools/embed/embed_e5.py
```

### VRAM 부족

```bash
# 현재 VRAM 사용량
nvidia-smi

# Ollama 모델 언로드
ollama stop llama3.1:8b

# 더 작은 모델로 변경
ollama pull llama3.2:3b
export OAI_COMPAT_MODEL="llama3.2:3b"
```

### Circuit Breaker 발동 (LLM 연속 실패)

```bash
# 기본: 5회 연속 실패 시 30초 쿨다운
# 조정:
export MACHINA_POLICY_FAIL_THRESHOLD=10    # 더 관대하게
export MACHINA_POLICY_COOLDOWN_MS=10000    # 10초로 줄이기
export MACHINA_POLICY_TIMEOUT_MS=5000      # 타임아웃 늘리기
```


## 6. 전체 env 변수 레퍼런스

### 임베딩

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MACHINA_EMBED_PROVIDER` | `hash` | `hash` / `cmd` |
| `MACHINA_EMBED_CMD` | (없음) | 임베딩 실행 명령 |
| `MACHINA_EMBED_TIMEOUT_MS` | `5000` | 타임아웃 |
| `MACHINA_EMBED_STDOUT_MAX` | `2097152` | stdout 최대 바이트 |
| `MACHINA_GPU_DIM` | `128` | 벡터 차원 (e5=384) |

### LLM Policy

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MACHINA_POLICY_CMD` | (없음) | policy 스크립트 경로 |
| `MACHINA_POLICY_TIMEOUT_MS` | `2500` | 호출 타임아웃 |
| `MACHINA_POLICY_FAIL_THRESHOLD` | `5` | 서킷 브레이커 임계값 |
| `MACHINA_POLICY_COOLDOWN_MS` | `30000` | 브레이커 쿨다운 |
| `MACHINA_POLICY_ALLOW_UNSAFE` | `0` | 경로 검증 비활성화 |

### Ollama / llama.cpp (OAI 호환)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OAI_COMPAT_BASE_URL` | `http://127.0.0.1:8080` | API 엔드포인트 |
| `OAI_COMPAT_MODEL` | `local` | 모델명 |
| `OAI_COMPAT_API_KEY` | (없음) | 인증키 (옵션) |
| `OAI_COMPAT_TIMEOUT_SEC` | `30` | 타임아웃 (초) |
| `OAI_COMPAT_MAX_TOKENS` | `256` | 최대 생성 토큰 |

### Anthropic API

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | (필수) | API 키 |
| `ANTHROPIC_MODEL` | `claude-opus-4-6` | 모델명 (또는 `claude-sonnet-4-5-20250929`) |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | 엔드포인트 |
| `ANTHROPIC_TIMEOUT_SEC` | `30` | 타임아웃 |
| `ANTHROPIC_MAX_TOKENS` | `256` | 최대 생성 토큰 |

### 셀렉터 / 컨트롤

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MACHINA_SELECTOR` | `HEURISTIC` | `HEURISTIC` / `GPU_CENTROID` |
| `MACHINA_USE_GPU` | `0` | CUDA 빌드 시 GPU 사용 |
| `MACHINA_GENESIS_ENABLE` | `0` | Genesis 자기진화 활성화 |
| `MACHINA_TOOLHOST_POOL_SIZE` | `2` | OOP 플러그인 세션 풀 |


## 7. 빠른 시작 치트시트

```bash
# === 원라이너: Ollama + e5 + Machina 전부 켜기 ===

# 터미널 1: Ollama 서버
ollama serve

# 터미널 2: Machina
cd /path/to/machina_trinity_legend
source machina_env.sh
./build/machina_cli run /tmp/test_blended.json
```
