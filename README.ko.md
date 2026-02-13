<div align="center">

# Machina Trinity

**안전 게이트 기반 자율 에이전트 런타임 (C++ 코어 + Python 에이전트 레이어)**

[![C++20](https://img.shields.io/badge/C%2B%2B-20-blue.svg)](https://isocpp.org/std/the-standard)
[![CMake](https://img.shields.io/badge/build-CMake%203.21+-064F8C.svg)](https://cmake.org)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

</div>

## 언어 문서

- 영어: `README.md`
- 한국어: `README.ko.md`
- 일본어: `README.ja.md`
- 중국어(간체): `README.zh-CN.md`
- 언어 전략: `docs/LANGUAGE_STRATEGY_EN.md`
- 전체 등가 문서 세트: `docs/i18n/README.md`

## 프로젝트 개요

Machina Trinity는 LLM이 실수할 것을 전제로 설계된 에이전트 런타임입니다.
목표는 자율성보다 먼저 안전성, 추적성, 복구성을 확보하는 것입니다.

핵심 축은 3가지입니다.

1. C++ 안전 코어: 트랜잭션 실행, 롤백, 감사 로그, WAL
2. Python 오케스트레이션: Telegram/Pulse 루프, dispatch, memory, MCP 브리지
3. 운영 가드레일: 권한 정책, 재현(replay), 검증 스크립트

## 왜 Machina인가

일반적인 에이전트 스택은 편리하지만 다음 문제가 자주 발생합니다.

- 잘못된 도구 실행 후 상태 오염
- 실패 원인 추적 어려움
- 위험 명령/경로 처리 취약
- 운영 환경에서의 재현 불가

Machina는 이를 다음 방식으로 줄입니다.

- 도구 실행을 트랜잭션으로 감싸고 실패 시 롤백
- JSONL + 해시 체인 기반 감사 로그 유지
- 권한 게이트/샌드박스/정책 제한 적용
- `replay`, `replay_strict`로 실행 경로 재현

## 빠른 시작

### 1) 빌드

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config libjson-c-dev

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

### 2) 테스트

```bash
cd build && ctest --output-on-failure && cd ..
```

### 3) 첫 실행

```bash
./build/machina_cli run examples/run_request.error_scan.json
```

### 4) Telegram 실행(선택)

```bash
cp .secrets.env.example .secrets.env
# TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, (선택) LLM 키 설정
python3 telegram_bot.py
```

## 현재 언어 상태와 계획

현재 Telegram/Pulse 경로는 한국어 프롬프트/키워드 맵을 중심으로 튜닝되어 있습니다.

- 현재: 한국어 우선(`ko-KR`) 운용
- 진행 중: 영어(`en`) 일반 사용자 흐름 고도화
- 예정: 일본어(`ja-JP`), 중국어(`zh-Hans-CN`/`zh-Hant-TW`) 포함 다국어 확장

상세 계획은 `docs/ROADMAP.md`와 `docs/LANGUAGE_STRATEGY_EN.md`를 참고하세요.

## 보안 요약

- 권한 모드: `open | standard | locked | supervised`
- ASK/ALLOW/DENY 승인 흐름
- 경로/명령/네트워크 가드
- 선택적 `bwrap`, seccomp, plugin hash pinning
- 운영 환경에서는 `MACHINA_PROFILE=prod` 권장

## 문서 맵

- 빠른 시작: `docs/QUICKSTART.md`
- 아키텍처: `docs/ARCHITECTURE.md`
- 운영/보안: `docs/OPERATIONS.md`
- 서버 API: `docs/SERVE_API.md`
- LLM 백엔드: `docs/LLM_BACKENDS.md`
- 정책 드라이버: `docs/POLICY_DRIVER.md`
- 로드맵: `docs/ROADMAP.md`
- 임베딩/LLM 세팅: `MACHINA_LLM_SETUP_GUIDE.md`
- 테스트 카탈로그: `MACHINA_TEST_CATALOG.md`

## 라이선스

Apache-2.0 (`LICENSE`)
