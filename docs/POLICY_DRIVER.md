# Policy Driver (External Selector) — Snapshot 6 Crunch

Snapshot 6에서 **자가 개선(Genesis) 루프를 실사용 가능한 형태로 완성하기 위해**
Selector를 외부 프로세스로 분리해 붙일 수 있는 **Policy Driver** 경로를 추가했습니다.

핵심은:
- 런타임에서 외부 LLM/룰엔진을 호출해 **선택 결과**를 받아오고
- 그 선택 결과 안에 **Inputs Patch**를 실어 보내서(=행동+지시를 하나로)
- Genesis ToolPack(WriteFile/Compile/Load)을 통해 **새 툴을 생성→컴파일→로드**할 수 있게 하는 것입니다.

---

## 1) 환경 변수

- `MACHINA_POLICY_CMD`
  - 예: `python3 examples/policy_drivers/hello_policy.py`
  - runner가 `POLICY_ONLY` 선택을 할 때, 아래 JSON payload 파일 경로를 argv[1]로 넘겨 실행합니다.

### (운영용 Hardening 옵션)

아래 옵션들은 `ExternalProcessSelector`에서 **timeout / allowlist / rlimit(샌드박스-lite)** 를 적용합니다.

- Timeout / 출력 제한
  - `MACHINA_POLICY_TIMEOUT_MS` (DEV: 60000, PROD: 30000; profile 미설정 시 코드 기본값 2500)
  - `MACHINA_POLICY_STDOUT_MAX` (기본 65536)

- Allowlist
  - `MACHINA_POLICY_ALLOWED_EXE` (기본: `python3,python,bash,sh,node`)
  - `MACHINA_POLICY_ALLOWED_SCRIPT_ROOT` (기본: `<repo_root>/policies`, 예시 드라이버 사용 시 `<repo_root>/examples/policy_drivers`로 설정)
  - `MACHINA_POLICY_ALLOW_UNSAFE=1` (allowlist 강제 해제, 운영에서는 비추천)

- Resource limits (best-effort)
  - `MACHINA_POLICY_RLIMIT_CPU_SEC` (기본 2)
  - `MACHINA_POLICY_RLIMIT_AS_MB` (기본 768)
  - `MACHINA_POLICY_RLIMIT_FSIZE_MB` (기본 10)
  - `MACHINA_POLICY_RLIMIT_NOFILE` (기본 64)
  - `MACHINA_POLICY_RLIMIT_NPROC` (기본 32)

---

## 2) Policy Driver 호출 규약

### 입력 (argv[1])

Policy Driver는 argv[1]로 전달된 파일(JSON)을 읽습니다.

payload 필드:
- `goal_digest`: runner가 만드는 `goal_id|menu_digest|FLAGS:...` 문자열
- `state_digest`: 현재 DSState digest
- `control_mode`: `POLICY_ONLY` 등
- `inputs`: 현재 step의 입력(JSON object)
  - 예: `inputs.cmd`, `inputs.url`, `inputs.path` 등
- `menu`: 후보 툴 목록
  - `sid`: 메뉴 내 선택 ID (예: `SID0007`)
  - `aid`: AID (예: `AID.GENESIS.WRITE_FILE.v1`)
  - `name`, `tags`

### 출력 (stdout)

Policy Driver는 아래 형식 중 하나를 stdout으로 출력합니다.

- Pick:
  - `<PICK><SID0007><END>`
- Pick + Inputs Patch:
  - `<PICK><SID0007><INP>{...}</INP><END>`
  - `<PICK><SID0007><INP64>BASE64(JSON_OBJECT)</INP64><END>`
- Ask human:
  - `<ASK_SUP><END>`
- Stop:
  - `<NOOP><END>`

`INP/INP64`는 **JSON object** 여야 하며, runner의 `inputs`에 **shallow-merge(키 단위 overwrite)** 됩니다.

---

## 3) 구현 포인트

### (a) Selector: ExternalProcessSelector
- `core/src/selector_external.cpp`
- `ControlMode::POLICY_ONLY`일 때만 외부 프로세스를 호출합니다.
- `ControlMode::FALLBACK_ONLY`는 내부 selector(heuristic/gpu 등)로 위임합니다.

### (b) Inputs Patch
- `core/src/selector.cpp`의 `parse_selector_output()`가 `<INP>`/`<INP64>`를 파싱
- `runner/main.cpp`가 patch를 `inputs`에 merge 하고, `inputs_patched` 이벤트를 로그에 남김
- `replay_strict`는 `inputs_patched`를 읽어 동일한 inputs 흐름을 재현

### (c) `tool_ok.tx_patch` Contract (Replay 중요)
- `tool_ok` 이벤트의 `tx_patch`는 **배열(JSON array)** 이어야 합니다.
- 각 원소는 아래 형태의 object:
  - `{"op":"add"|"replace","path":"/slots/<0..7>","value":{Artifact...}}`
  - `{"op":"remove","path":"/slots/<0..7>"}`
- `value`는 `Artifact` JSON 스키마(`type`, `provenance`, `content_json`, `size_bytes`)를 따릅니다.
- `replay_strict`는 비결정론 툴(`deterministic=false`)을 재실행하지 않고 `tx_patch`를 DSState에 적용해 상태를 재구성합니다.
- `op`/`path`/`value` 형식이 맞지 않으면 `REPLAY_STRICT FAIL`로 중단됩니다.

---

## 4) 데모

### Genesis + Policy Codegen 데모

```bash
./scripts/build_fast.sh

# 외부 정책 드라이버 연결
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"

# policy-only로 genesis 부트스트랩(Write → Compile → Load → runtime tool)
./scripts/run_demo.sh genesis_policy_codegen
```

이 데모는 외부 policy가 다음을 수행합니다.
1) runtime_genesis/src 하위에 C++ 툴 소스 생성
2) `.so`로 컴파일
3) runtime_plugins로 로드
4) 로드된 런타임 툴(AID.RUNTIME.POLICY_ECHO.v1)을 실행하여 DS0를 생성

---

## 5) 안전/운영 메모

- 외부 프로세스 호출은 로컬 환경에서만 사용을 권장합니다.
- 신뢰 경계(Trust boundary)를 명확히 하려면, `policy_cmd`를 allowlist + sandbox로 감싸는 형태가 바람직합니다.
- patch는 shallow merge만 수행하므로, 복잡한 JSON diff/merge가 필요하면 별도 정책을 추가하세요.


---

## 6) 24/7 운영 토폴로지 (권장)

**목표:** goal 1회 실행 후 멈추는 구조가 아니라, **항상-온 루프**로 돌려서 policy가 계속 다음 작업을 생성하도록.

### 구성 A: 파일 큐 + Autopilot (가장 단순/튼튼)

1) 워커 실행

```bash
./scripts/build_fast.sh
./build/machina_cli autopilot work/queue
```

2) Policy가 다음 작업을 생성
- policy 출력에서 `AID.QUEUE.ENQUEUE.v1`를 선택하고, `request_json`에 다음 run_request를 넣어 enqueue
- autopilot이 `inbox/`를 감지해 자동 실행

### 구성 B: 로컬 HTTP Adapter + Autopilot (외부 오케스트레이터 연동)

```bash
./build/machina_cli serve --host 127.0.0.1 --port 8080 --queue work/queue
./build/machina_cli autopilot work/queue
```

- 외부 오케스트레이터(또는 상위 LLM)가 `POST /enqueue`로 run_request를 던지면 autopilot이 처리

---

## 7) 네트워크 제한 (HTTP_GET)

`AID.NET.HTTP_GET.v1`는 운영에서 무제한 웹 호출을 막기 위해, 아래를 지원합니다.

- `MACHINA_HTTP_ALLOWED_HOSTS`
  - 비어 있으면(기본) 허용
  - 설정하면 allowlist 강제
  - 형식: `example.com,api.openai.com,*.github.com,*`


---

## 8) LLM 연결 템플릿

코드베이스에 **HTTP 브리지형 policy** 템플릿을 추가했습니다.

- `examples/policy_drivers/llm_http_policy.py`
  - `MACHINA_POLICY_LLM_URL`로 payload를 POST
  - 응답으로 `<PICK>...<END>` 텍스트(또는 JSON의 `machina_out`)를 받아 그대로 stdout 출력

예시:

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
# (옵션) export MACHINA_POLICY_LLM_AUTH="Bearer ..."
```

이렇게 하면 runner는 **진짜 두뇌(외부 LLM 서버)**를 붙인 채로 `POLICY_ONLY` 모드에서 메뉴 선택을 수행합니다.


---

## Circuit Breaker (운영 안정성)

외부 Policy가 반복적으로 죽거나(timeout/exit!=0/출력불량) 이상 동작하면 전체 러너가 불안정해질 수 있습니다.
`ExternalProcessSelector`는 아래 설정으로 **서킷 브레이커**를 제공합니다.

- `MACHINA_POLICY_FAIL_THRESHOLD` (default: 5)
  - 연속 실패 횟수가 이 값 이상이면 잠시 정책을 비활성화합니다.
- `MACHINA_POLICY_COOLDOWN_MS` (default: 30000)
  - 비활성화 기간(ms). 기간 동안 fallback selector로만 동작합니다.

실패로 간주하는 경우:
- 정책 프로세스 시작 실패
- timeout
- exit_code != 0
- 출력이 비어있음
- 출력이 selector contract를 만족하지 못함(INVALID)

비활성화 중에는 `ControlMode::FALLBACK_ONLY`로 fallback selector를 호출합니다.

---

## Templates

### Engine Policy Drivers (tool selection)

Engine policy drivers connect via `MACHINA_POLICY_CMD` external process protocol.
Legacy template scripts (policy_*.py) were removed in v6.3. Custom drivers should follow
the stdin/stdout JSON protocol documented above.

### Chat System Drivers (interactive mode)

- `chat_driver.py` + `chat_driver_util.py` – 3-phase Pulse pipeline (intent → execute → continue) + DST + entity memory
- `chat_llm.py` – Ollama/Anthropic/OAI API call layer
- `chat_intent_map.py` – Intent classification + normalization + adaptive prompts + MCP routing

Chat config:
- `MACHINA_CHAT_CMD` : chat driver script path (e.g., `python3 policies/chat_driver.py`)
- `MACHINA_CHAT_BACKEND` : `oai_compat` or `anthropic`
- `MACHINA_CHAT_TIMEOUT_MS` : timeout (default: 60000)

See `docs/LLM_BACKENDS.md`.
