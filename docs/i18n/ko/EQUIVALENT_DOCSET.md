# 한국어 등가 문서 세트 (영문 문서 전체 대응)

이 문서는 영어 원문 문서 19개 전부를 대상으로, 한국어 사용자 관점에서 동일한 운용/개발 결정을 내릴 수 있도록 구성한 등가 안내서입니다.

## 1. 적용 범위

| 원문 파일 | 한국어 등가 범위 |
|---|---|
| `README.md` | 아키텍처 개요, 실행 모드, 보안 철학, 문서 맵 |
| `CODE_OF_CONDUCT.md` | 커뮤니티 행동 규범과 신고/집행 원칙 |
| `CONTRIBUTING.md` | 기여 절차, PR 기준, 리뷰 기대치 |
| `SECURITY.md` | 취약점 신고, 비밀정보 처리, 운영 보안 수칙 |
| `GITHUB_UPLOAD_CHECKLIST.md` | 공개 업로드 전 정리/검증 절차 |
| `MACHINA_LLM_SETUP_GUIDE.md` | 임베딩/LLM/정책 드라이버 실전 연결 가이드 |
| `MACHINA_TEST_CATALOG.md` | 툴/목표/리플레이/보안/MCP 테스트 카탈로그 |
| `docs/QUICKSTART.md` | 10분 온보딩 동선 |
| `docs/ARCHITECTURE.md` | Trinity 구조와 실행 경로 |
| `docs/OPERATIONS.md` | 운영 프로필, 가드레일, 유지보수 절차 |
| `docs/SERVE_API.md` | serve HTTP API와 인증/관측 |
| `docs/LLM_BACKENDS.md` | 백엔드 연결 방식과 정책 드라이버 패턴 |
| `docs/POLICY_DRIVER.md` | POLICY_ONLY 외부 드라이버 계약 |
| `docs/LANGUAGE_STRATEGY_EN.md` | 언어 전략, BCP-47 분류, 확장 정책 |
| `docs/ROADMAP.md` | 릴리즈 상태와 향후 축 |
| `docs/ipc_schema.md` | IPC 데이터 계약 |
| `docs/GITHUB_PREP_AUDIT_2026-02-13.md` | 업로드 구성 감사 근거 |
| `examples/policy_drivers/README.md` | 정책 드라이버 예제 사용법 |
| `toolpacks/runtime_genesis/src/self_test_calc/README.md` | Genesis 런타임 플러그인 테스트 도구 설명 |

## 2. 운영자가 반드시 아는 핵심

### 2.1 안전 전제

- LLM은 실패할 수 있다는 가정을 전제로 설계한다.
- 도구 실행은 트랜잭션 기반이며 실패 시 롤백된다.
- 감사 로그는 해시 체인으로 유지되어 사후 추적이 가능하다.

### 2.2 공개/배포 전 필수 체크

```bash
scripts/run_guardrails.sh
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
cd build && ctest --output-on-failure
```

### 2.3 정책 드라이버 실행 조건

`examples/policy_drivers`를 사용하는 경우, 아래 설정이 필요하다.

```bash
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
```

## 3. 문서군별 등가 요약

### 3.1 시작/온보딩 문서군

대상: `README.md`, `docs/QUICKSTART.md`, `GITHUB_UPLOAD_CHECKLIST.md`

- 프로젝트 철학(안전 우선)과 빠른 실행 경로를 제공
- 신규 사용자는 `docs/QUICKSTART.md` 기준으로 실행
- 배포 직전에는 업로드 체크리스트로 불필요 산출물 제거

### 3.2 아키텍처/계약 문서군

대상: `docs/ARCHITECTURE.md`, `docs/ipc_schema.md`

- Trinity(Body/Driver/Memory) 분리 구조 이해
- runner, selector, policy, replay 경계 확인
- IPC 스키마를 기준으로 도구/이벤트 계약 일관성 확보

### 3.3 운영/보안 문서군

대상: `docs/OPERATIONS.md`, `SECURITY.md`, `docs/SERVE_API.md`

- 운영 프로필(`dev`/`prod`)과 보안 기본값 확인
- serve API 인증(Token/HMAC), 레이트 리밋, 관측 지표 확인
- 비밀정보는 저장소 외부(`~/.config/machina/.secrets.env`) 유지

### 3.4 LLM/정책/언어 전략 문서군

대상: `MACHINA_LLM_SETUP_GUIDE.md`, `docs/LLM_BACKENDS.md`, `docs/POLICY_DRIVER.md`, `docs/LANGUAGE_STRATEGY_EN.md`

- 엔진 정책 드라이버와 채팅 백엔드는 구분해서 구성
- 한국어 중심 텔레그램 경로를 유지하되 다국어 확장 준비
- 언어별 포크를 만들지 않고 단일 코드베이스 + 로케일 리소스로 확장

### 3.5 검증/품질 문서군

대상: `MACHINA_TEST_CATALOG.md`, `docs/ROADMAP.md`, `docs/GITHUB_PREP_AUDIT_2026-02-13.md`

- 테스트 카탈로그로 툴 단위부터 E2E/MCP까지 범위 확인
- 로드맵에서 현재 제공 기능과 다음 투자 축 확인
- 감사 리포트로 공개 저장소 포함/제외 기준 검증

### 3.6 예제/Genesis 문서군

대상: `examples/policy_drivers/README.md`, `toolpacks/runtime_genesis/src/self_test_calc/README.md`

- 정책 드라이버 출력 계약(`<PICK>...<END>`) 준수
- 런타임 생성 플러그인은 해시 검증/로딩 경계 안에서 운용

## 4. 한국어 운영 권장 프로필

```bash
export MACHINA_PROFILE=prod
export MACHINA_CHAT_BACKEND=oai_compat
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
```

- 텔레그램/운영봇은 현재 한국어(`ko-KR`) 중심으로 최적화됨
- 향후 `MACHINA_LANG` 기반 다국어 라우팅을 단계적으로 적용 예정

## 5. 다국어 확장 원칙

- 영어 원문은 기술 계약의 기준 문서
- 한국어/일본어/중국어 등가 문서는 사용자 온보딩/운영 접근성을 높이기 위한 동기화 문서
- 의미 충돌이 있으면 영어 원문 계약을 우선 적용

## 6. 유지보수 체크포인트

- 원문 문서 구조 변경 시 본 등가 문서도 동일 릴리즈에서 갱신
- 핵심 명령/환경변수 이름은 번역하지 않고 원문 그대로 유지
- 보안/권한/테스트 절차는 요약 없이 유지
