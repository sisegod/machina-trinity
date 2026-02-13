# GitHub 업로드 준비 감사 리포트 (2026-02-13)

## 1. 조사 목적
- 코드 흐름을 기준으로 업로드 필수 파일만 선별
- `README.md` / `README_new.md` 중복 정리 기준 수립
- 문서(`*.md`)와 루트 Python 모듈(`*.py`)의 실사용성 점검

## 2. 코드 흐름 핵심

### 2.1 C++ 실행 흐름
- 진입점: `runner/main.cpp`
- 디스패치: `run | chat | replay | replay_strict | cts | autopilot | serve | tool_exec`
- 핵심 런타임:
1. `runner/cmd_run.cpp`: goal 실행 루프, selector, tx/log, plugin reload
2. `runner/cmd_serve.cpp`: HTTP queue + worker + WAL/checkpoint + dedup
3. `runner/cmd_chat.cpp`: 대화 intent 파싱 및 액션 실행

### 2.2 Python 실행 흐름
- 주 진입점: `telegram_bot.py` (`if __name__ == "__main__"`)
- 메시지 처리: `telegram_bot_pulse.py` -> `policies/chat_driver.py` -> `machina_dispatch.execute_intent()`
- 실행/권한/도구 계층:
1. `machina_dispatch.py` (facade)
2. `machina_dispatch_exec.py` (실행 분기)
3. `machina_dispatch_registry.py` (AID/alias)
4. `machina_permissions.py` (권한 정책)

## 3. 루트 Python 파일 필요성 판정

### 3.1 업로드 필수(런타임 코어)
- `machina_config.py`
- `machina_shared.py`
- `machina_dispatch.py`
- `machina_dispatch_exec.py`
- `machina_dispatch_registry.py`
- `machina_permissions.py`
- `machina_tools.py`
- `machina_tools_fileops.py`
- `machina_learning.py`
- `machina_learning_memory.py`
- `machina_graph.py`
- `machina_graph_memory.py`
- `machina_gvu.py`
- `machina_gvu_tracker.py`
- `machina_mcp.py`
- `machina_mcp_connection.py`
- `telegram_bot.py`
- `telegram_bot_handlers.py`
- `telegram_bot_pulse.py`
- `telegram_commands.py`
- `telegram_commands_ext.py`
- `machina_reindex.py` (운영 점검용 CLI)

### 3.2 업로드 권장(테스트/거버넌스 보조)
- `machina_brain_orchestrator.py`
- `machina_evolution_governor.py`
- `machina_evolution_policy.py`

판정 근거:
- 위 3개는 현재 프로덕션 주 경로에는 직접 연결이 약하지만, 테스트(`tests/test_evolution_governance.py`)와 정책 실험 경로에서 사용됨.
- 따라서 삭제보다 "핵심 런타임 비필수, 저장소 유지 권장"이 안전.

## 4. 문서 유효성 판정

### 4.1 공개 저장소 기준 유지 권장
- `README.md`
- `docs/QUICKSTART.md`
- `docs/ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/SERVE_API.md`
- `docs/LLM_BACKENDS.md`
- `docs/POLICY_DRIVER.md`
- `docs/ROADMAP.md`
- `docs/ipc_schema.md`
- `MACHINA_LLM_SETUP_GUIDE.md`
- `MACHINA_TEST_CATALOG.md`

### 4.2 내부 기록 성격(공개 루트에서는 비필수)
- `docs/REPO_AUDIT_2026-02-13.md`
- `docs/PROJECT_DEEP_DIVE_2026-02-13.md`
- `docs/CLAUDE_TO_CODEX_SKILL_PLAN.md`

판정 근거:
- 날짜/세션 기반 내부 운영 기록이며, 외부 사용자 온보딩/사용에는 필수 아님.
- 공개 저장소에서는 핵심 가이드 대비 정보 밀도가 낮고 노이즈가 됨.

## 5. GitHub 업로드용 클린 구성 기준

### 5.1 포함
- 소스: `core/`, `runner/`, `toolhost/`, `tools/`, `machina_autonomic/`, `policies/`
- 런타임 Python: 루트 `machina_*.py`, `telegram_*.py`
- 설정/스키마/예제/테스트: `toolpacks/`, `goalpacks/`, `schemas/`, `examples/`, `tests/`, `scripts/`
- 메타: `.github/`, `CMakeLists.txt`, `.gitignore`, `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.secrets.env.example`, `mcp_servers.json`
- 문서: 4.1 유지 권장 목록

### 5.2 제외
- 실행 산출물: `build/`, `logs/`, `work/`, `__pycache__/`
- 로컬 환경 파일: `machina_env.sh`, `.env*`, `.secrets.env`
- 런타임 보고 덤프: `ops/*`
- README 중복본: `README_new.md`
- 내부 기록성 문서: 4.2 목록

## 6. 결론
- 루트 `*.py`는 대부분 실제 의존 경로에 연결되어 있어 대규모 삭제 대상 없음.
- 중복 README와 내부 기록성 문서를 분리하면 공개 저장소 가독성과 유지보수성이 크게 좋아짐.
- 본 기준으로 `github_ready/` 클린 디렉토리를 생성해 바로 업로드 가능한 상태로 구성하는 것이 적절함.
