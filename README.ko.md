<div align="center">

# Machina Trinity

**Safety-Gated Autonomous Agent Runtime (C++ Core + Python Agent Layer)**

[![C++20](https://img.shields.io/badge/C%2B%2B-20-blue.svg)](https://isocpp.org/std/the-standard)
[![CMake](https://img.shields.io/badge/build-CMake%203.21+-064F8C.svg)](https://cmake.org)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

</div>

Machina Trinity는 "LLM이 도구를 자율 실행하더라도" 안전성과 추적 가능성을 유지하도록 설계된 에이전트 런타임입니다.

핵심은 다음 3가지입니다.

1. **C++ 안전 코어**: Tx/Rollback, 감사 로그, 샌드박스, WAL
2. **Python 오케스트레이션**: Telegram/Pulse 루프, Dispatch, Memory, MCP 브리지
3. **운영 가드레일**: 권한 정책, replay, 테스트/검증 스크립트

## Why This Project

일반적인 에이전트 스택은 편하지만, 실패/오작동/재현성에서 취약한 경우가 많습니다.
Machina는 아래 문제를 우선 해결합니다.

| 문제 | Machina 접근 |
|---|---|
| 잘못된 도구 선택 | 트랜잭션 실행 + 실패 시 롤백 |
| 원인 추적 어려움 | 해시 체인 기반 JSONL 감사 로그 |
| 위험한 명령 실행 | 권한 게이트 + 샌드박스 + 제한 정책 |
| 재현 어려움 | `replay`, `replay_strict` |
| 운영 중 장애 대응 | queue/WAL/checkpoint + worker 모델 |

## Architecture

```text
User/Trigger
  -> telegram_bot.py / machina_cli
     -> policies/chat_driver.py (intent)
        -> machina_dispatch.py
           -> machina_dispatch_exec.py
              -> Python tools / MCP / C++ toolhost
                 -> core Tx + log + state
```

### Runtime Entry Points
- `./build/machina_cli run <request.json>`: 단일 목표 실행
- `./build/machina_cli serve --workers N`: HTTP enqueue + 내장 worker
- `./build/machina_cli chat`: 인터랙티브 채팅 모드
- `python3 telegram_bot.py`: Telegram 기반 에이전트 실행
- `python3 machina_reindex.py --verify`: 메모리 스트림 정합성 점검

## Quick Start

### 1) Build
```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config libjson-c-dev

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

### 2) Verify
```bash
cd build && ctest --output-on-failure && cd ..
```

### 3) First Run
```bash
./build/machina_cli run examples/run_request.error_scan.json
```

### 4) Optional: Telegram Agent
```bash
cp .secrets.env.example .secrets.env
# TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, (선택) LLM 키 설정
python3 telegram_bot.py
```

## Security Model (Summary)

- Permission mode: `open | standard | locked | supervised`
- ASK/ALLOW/DENY 승인 플로우
- 경로/명령/네트워크 가드
- 선택적 `bwrap`, seccomp, plugin hash pinning
- 운영 모드 `MACHINA_PROFILE=prod` 권장

상세 운영 보안은 `docs/OPERATIONS.md`를 참고하세요.

## Repository Layout

```text
core/                 C++ 코어 엔진
runner/               machina_cli 실행 모드 구현
toolhost/             도구 호스트 바이너리
tools/tier0/          내장 도구 구현
machina_autonomic/    자율 루프 엔진
policies/             정책/LLM 드라이버
scripts/              빌드/운영/검증 스크립트
docs/                 운영/아키텍처 문서
examples/             실행 예제 요청 JSON
schemas/              manifest/log JSON schema
tests/                C++/Python 테스트
```

## Documentation Map

- 시작: `docs/QUICKSTART.md`
- 구조: `docs/ARCHITECTURE.md`
- 운영/보안: `docs/OPERATIONS.md`
- 서버 API: `docs/SERVE_API.md`
- LLM 백엔드 연결: `docs/LLM_BACKENDS.md`
- 정책 드라이버: `docs/POLICY_DRIVER.md`
- IPC 계약: `docs/ipc_schema.md`
- 향후 계획: `docs/ROADMAP.md`
- 임베딩 실전 가이드: `MACHINA_LLM_SETUP_GUIDE.md`
- 테스트 카탈로그: `MACHINA_TEST_CATALOG.md`

## Testing

빠른 가드레일 검사:

```bash
scripts/run_guardrails.sh
```

핵심 점검 스크립트:

```bash
python3 scripts/validate_aid_refs.py
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
```

## GitHub Upload Guidance

GitHub 공개 업로드 시에는 아래 산출물은 제외하세요.

- `build/`, `logs/`, `work/`, `__pycache__/`
- `.env*`, `.secrets.env`, `machina_env.sh`
- 내부 운영 기록성 문서(날짜 스냅샷 리포트)

상세 기준은 `GITHUB_UPLOAD_CHECKLIST.md`를 참고하세요.

## License

Apache-2.0 (`LICENSE`)
