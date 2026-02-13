# Machina Trinity — 전체 테스트 카탈로그

> 23+ 도구, 5개 목표, 7개 실행 모드, 14개 C++ 단위 테스트 + 34개 Python E2E + 25개 풀 파이프라인, HTTP API 6개 엔드포인트, MCP 브릿지 8개
> 아래 **모든 항목**을 조합하면 256+ 테스트 시나리오가 나온다.

---

## A. 개별 도구 직접 테스트 (29개)

각 도구를 `run` 명령으로 **단독 실행**하는 테스트.

### A1. 파일 시스템

| # | 도구 | 테스트 | 명령 |
|---|------|--------|------|
| 1 | `AID.FILE.READ.v1` | 파일 읽기 | `{"goal_id":"goal.ERROR_SCAN.v1","inputs":{"input_path":"../README.md","pattern":".","max_rows":10},...}` (또는 직접 tool 호출) |
| 2 | | 없는 파일 읽기 | path를 없는 파일로 → 에러 처리 확인 |
| 3 | | 바이너리 파일 | 바이너리 파일 base64 인코딩 확인 |
| 4 | | max_bytes 제한 | 큰 파일에 max_bytes=100 → 잘림 확인 |
| 5 | `AID.FILE.WRITE.v1` | 파일 쓰기 | work/ 아래에 텍스트 파일 생성 |
| 6 | | 덮어쓰기 | overwrite=true/false 동작 확인 |
| 7 | | 디렉토리 생성 | mkdirs=true로 중첩 디렉토리 |
| 8 | | 샌드박스 탈출 시도 | `../../etc/passwd` 같은 경로 → 차단 확인 |

### A2. 셸 실행

| # | 도구 | 테스트 | 명령 |
|---|------|--------|------|
| 9 | `AID.SHELL.EXEC.v1` | 기본 명령 | `{"cmd":["ls","-la"]}` |
| 10 | | 타임아웃 | `{"cmd":["sleep","10"],"timeout_ms":1000}` → timed_out=true |
| 11 | | 출력 잘림 | 큰 출력 생성 → truncated=true |
| 12 | | 허용목록 외 명령 | allowlist에 없는 실행파일 → 차단 |
| 13 | | 파이프/리다이렉트 | `{"cmd":["bash","-c","ls | head -5"]}` |
| 14 | | 환경변수 격리 | 자식 프로세스에서 부모 환경변수 접근 불가 확인 |

### A3. 네트워크

| # | 도구 | 테스트 | 명령 |
|---|------|--------|------|
| 15 | `AID.NET.HTTP_GET.v1` | URL 가져오기 | `{"url":"http://example.com"}` |
| 16 | | 타임아웃 | 느린 서버에 timeout_ms=500 |
| 17 | | max_bytes | 큰 페이지에 max_bytes=1024 |
| 18 | | 잘못된 URL | `{"url":"not-a-url"}` → 에러 처리 |

### A4. GPU

| # | 도구 | 테스트 | 명령 |
|---|------|--------|------|
| 19 | `AID.GPU_SMOKE.v1` | GPU 존재 확인 | available=true, device_count, backend |
| 20 | `AID.GPU_METRICS.v1` | 상세 메트릭 | VRAM, 온도, 전력, 이름 |
| 21 | | GPU 없는 환경 | CUDA_VISIBLE_DEVICES="" → graceful degradation |

### A5. 프로세스 자기 진단

| # | 도구 | 테스트 | 명령 |
|---|------|--------|------|
| 22 | `AID.PROC.SELF_METRICS.v1` | 프로세스 상태 | pid, rss_kb, vmsize_kb, threads, open_fds |

### A6. 로그 분석 + 리포트

| # | 도구 | 테스트 | 명령 |
|---|------|--------|------|
| 23 | `AID.ERROR_SCAN.v1` | CSV/로그 에러 스캔 | input_path + pattern → matches 수 |
| 24 | | 빈 파일 | matches=0 확인 |
| 25 | | 대용량 파일 | max_rows 제한 동작 |
| 26 | `AID.REPORT_SUMMARY.v1` | 스캔 결과 요약 | DS0에 스캔 결과 있을 때 → DS2에 요약 |
| 27 | `AID.RUN.LOG.SUMMARY.v1` | 실행 로그 분석 | logs/*.jsonl → 이벤트 카운트, 도구 타이밍, 체인 링크 검증 |

### A7. NOOP / ASK_SUP

| # | 도구 | 테스트 | 명령 |
|---|------|--------|------|
| 28 | `AID.NOOP.v1` | 아무것도 안 함 | 정상 리턴 확인 |
| 29 | `AID.ASK_SUP.v1` | 사용자에게 질문 | DS1에 질문 저장 |

---

## B. 메모리 시스템 테스트 (기억 + 소환)

### B1. 텍스트 메모리 (JSONL 기반)

| # | 시나리오 | 도구 조합 | 검증 |
|---|---------|-----------|------|
| 30 | **기억 저장** | `AID.MEMORY.APPEND.v1` | stream="notes", text="OOM 23건 발견" → work/memory/notes.jsonl에 기록 |
| 31 | **여러 개 저장** | APPEND × 5 | 5줄 연속 저장 → 순서 보존 |
| 32 | **다른 스트림** | APPEND | stream="todo" vs stream="notes" → 별도 파일 |
| 33 | **JSON 이벤트** | APPEND | event={"type":"alert","level":3} → JSON 객체 저장 |
| 34 | **단순 검색** | `AID.MEMORY.SEARCH.v1` | stream="notes", contains="OOM" → 해당 줄 반환 |
| 35 | **검색 limit** | SEARCH | limit=3 → 최대 3건 |
| 36 | **없는 키워드** | SEARCH | contains="존재안함" → count=0 |
| 37 | **하이브리드 소환** | `AID.MEMORY.QUERY.v1` | query="메모리 부족 관련 기록", mode="hybrid" → BM25+임베딩 점수 |
| 38 | **BM25 전용** | QUERY | mode="bm25" → score_bm25만 유효 |
| 39 | **임베딩 전용** | QUERY | mode="embed" → score_embed만 유효 (E5 필요) |
| 40 | **VecDB 모드** | QUERY | mode="vecdb" → VECDB에서 검색 |
| 41 | **자동 모드** | QUERY | mode="auto" → 최적 모드 자동 선택 |
| 42 | **top_k** | QUERY | top_k=1 vs top_k=10 → 결과 수 차이 |
| 43 | **로테이션 후 검색** | APPEND 수백 건 후 QUERY | 로테이션된 파일에서도 검색 |

### B2. 벡터 DB (임베딩 기반 의미 검색)

| # | 시나리오 | 도구 조합 | 검증 |
|---|---------|-----------|------|
| 44 | **임베딩 생성** | `AID.EMBED.TEXT.v1` | text="서버 모니터링" → 384차원 float 벡터 |
| 45 | **차원 지정** | EMBED | dim=128 → 128차원 |
| 46 | **정규화** | EMBED | normalize=true → L2 norm = 1.0 |
| 47 | **hash 프로바이더** | EMBED | MACHINA_EMBED_PROVIDER=hash → 결정적 벡터 |
| 48 | **외부 프로바이더** | EMBED | E5-small 모델 → 의미 있는 벡터 |
| 49 | **벡터 저장** | `AID.VECDB.UPSERT.v1` | stream="knowledge", text="GPU는 병렬 연산에 강하다" |
| 50 | **메타데이터 첨부** | UPSERT | meta={"source":"manual","tag":"gpu"} |
| 51 | **의미 검색** | `AID.VECDB.QUERY.v1` | query="그래픽카드 성능" → GPU 관련 결과가 상위 |
| 52 | **유사도 순위** | QUERY | 여러 문서 upsert 후 → score 높은 순 정렬 확인 |
| 53 | **top_k 제한** | QUERY | top_k=3 → 3건만 반환 |
| 54 | **빈 DB 검색** | QUERY | upsert 전에 query → 빈 결과 또는 graceful 에러 |

### B3. 메모리 통합 시나리오 (기억 → 소환 파이프라인)

| # | 시나리오 | 흐름 | 검증 |
|---|---------|------|------|
| 55 | **기억→텍스트소환** | APPEND("서버 OOM 23건") → SEARCH("OOM") | 저장한 내용 그대로 찾기 |
| 56 | **기억→의미소환** | APPEND("Java 힙 메모리 부족") → QUERY("메모리 관련 이슈") | 키워드 다르지만 의미로 찾기 |
| 57 | **기억→벡터소환** | UPSERT("nginx 504 timeout") → VECDB.QUERY("웹서버 응답 지연") | 임베딩 유사도로 찾기 |
| 58 | **대량 기억 후 소환** | APPEND × 100 → QUERY | 대량 데이터에서 정확도 유지 |
| 59 | **크로스 스트림** | stream A에 저장 → stream B에서 검색 → 못 찾음 | 스트림 격리 확인 |

---

## C. Genesis — 도구 생성 (자기 진화)

| # | 시나리오 | 도구 조합 | 검증 |
|---|---------|-----------|------|
| 60 | **소스 작성** | `AID.GENESIS.WRITE_FILE.v1` | .cpp 파일 → toolpacks/runtime_genesis/src/ 에 저장 |
| 61 | **덮어쓰기 방지** | WRITE | overwrite=false → 이미 있으면 에러 |
| 62 | **샌드박스 탈출** | WRITE | relative_path="../../evil.cpp" → 차단 |
| 63 | **컴파일** | `AID.GENESIS.COMPILE_SHARED.v1` | .cpp → .so (toolpacks/runtime_plugins/) |
| 64 | **컴파일 에러** | COMPILE | 문법 에러 있는 코드 → 에러 메시지 DS7에 저장 |
| 65 | **컴파일 재시도** | COMPILE × 3 | MACHINA_GENESIS_COMPILE_RETRIES=3 → 최대 3번 재시도 |
| 66 | **플러그인 로드** | `AID.GENESIS.LOAD_PLUGIN.v1` | .so → dlopen → 새 도구 등록 |
| 67 | **로드 후 즉시 사용** | LOAD → 새 AID로 run | 런타임에 등록된 도구가 바로 동작 |
| 68 | **전체 파이프라인** | WRITE → COMPILE → LOAD → USE | hello_tool.cpp 작성 → 컴파일 → 로드 → 실행 |
| 69 | **Genesis 데모** | `run_request.genesis_demo_hello.json` | 템플릿에서 hello_tool 자동 생성 |
| 70 | **자동 스텁** | `run_request.missing_tool_autostub.json` | 없는 도구 호출 시 자동으로 스텁 생성+컴파일+로드 |
| 71 | **자동 스텁 환경변수** | MACHINA_GENESIS_AUTOTRIGGER=1, AUTOSTUB=1 | 환경변수로 자동 생성 켜기 |
| 72 | **Policy codegen** | `run_request.genesis_policy_codegen.json` | LLM이 코드 생성 → Genesis가 컴파일 (POLICY_ONLY 모드) |

---

## D. 목표 실행 (Goal = 도구 묶음)

### D1. 등록된 목표

| # | 목표 | 도구 체인 | 완료 조건 | 테스트 |
|---|------|-----------|-----------|--------|
| 73 | `goal.ERROR_SCAN.v1` | ERROR_SCAN → REPORT_SUMMARY | DS2 슬롯 채워짐 | `run examples/run_request.error_scan.json` |
| 74 | `goal.GPU_SMOKE.v1` | GPU_SMOKE → NOOP | DS0 슬롯 채워짐 | `run examples/run_request.gpu_smoke.json` |
| 75 | `goal.GPU_METRICS.v1` | GPU_METRICS → NOOP | DS0 슬롯 채워짐 | `run examples/run_request.gpu_metrics.json` |
| 76 | `goal.GENESIS_DEMO_HELLO.v1` | WRITE → COMPILE → LOAD | (프로그래매틱) | `run examples/run_request.genesis_demo_hello.json` |
| 77 | `goal.DEMO.MISSING_TOOL.v1` | MISSING → autostub 트리거 | DS0 | `run examples/run_request.missing_tool_autostub.json` |

### D2. 목표 실행 메커니즘

| # | 시나리오 | 검증 |
|---|---------|------|
| 78 | **스텝 예산** | max_steps=64 초과 시 breaker 발동 |
| 79 | **루프 가드** | 같은 menu+state 3번 반복 → loop_guard_triggered |
| 80 | **잘못된 PICK** | max_invalid_picks=8 초과 시 breaker |
| 81 | **NOOP 종료** | 셀렉터가 NOOP 반환 → 정상 종료 |
| 82 | **ASK_SUP 종료** | 셀렉터가 ASK_SUP → DS1에 질문 저장 후 종료 |
| 83 | **도구 에러 처리** | 도구가 TOOL_ERROR 반환 → rollback + 로그 |
| 84 | **플러그인 자동 리로드** | 실행 중 runtime_plugins/에 새 .so → 다음 스텝에서 자동 로드 |

---

## E. 셀렉터 (도구 선택 전략)

| # | 모드 | 환경변수 | 테스트 |
|---|------|---------|--------|
| 85 | **HEURISTIC** | MACHINA_SELECTOR=HEURISTIC | 결정적 규칙 기반 선택 |
| 86 | **GPU_CENTROID** | MACHINA_SELECTOR=GPU_CENTROID | 임베딩 코사인 유사도 기반 선택 |
| 87 | **GPU centroid 캐시** | GPU_CENTROID 반복 호출 | 128엔트리 캐시 히트 확인 |

### 제어 모드 (ControlMode)

| # | 모드 | 의미 | 테스트 |
|---|------|------|--------|
| 88 | `FALLBACK_ONLY` | 휴리스틱만 사용 | LLM 없이 동작 확인 |
| 89 | `POLICY_ONLY` | LLM만 사용 | LLM 응답으로만 도구 선택 |
| 90 | `BLENDED` | LLM 우선, 실패 시 휴리스틱 | LLM 잘못된 응답 → fallback |
| 91 | `SHADOW_POLICY` | 휴리스틱 실행 + LLM 로깅만 | 둘 다 로그에 기록, 휴리스틱 결과 사용 |

### LLM 정책 드라이버

| # | 드라이버 | 테스트 |
|---|---------|--------|
| 92 | `examples/policy_drivers/hello_policy.py` | 첫 번째 SID를 선택하는 최소 정책 드라이버 |
| 93 | `examples/policy_drivers/llm_http_policy.py` | 외부 LLM HTTP 엔드포인트 연결 → PICK 응답 |
| 94 | `policies/chat_driver.py` | 대화형 LLM 라우팅/응답 드라이버 (chat plane) |
| 95 | `ControlMode=FALLBACK_ONLY` | LLM 없이 휴리스틱으로 안전 동작 검증 |
| 96 | `ControlMode=POLICY_ONLY` | 외부 정책 응답만으로 도구 선택 검증 |
| 97 | **서킷 브레이커** | LLM 5번 연속 실패 → 30초 자동 fallback |
| 98 | **서킷 자동 복구** | 쿨다운 후 LLM 재시도 |
| 99 | **입력 패치** | LLM이 `<INP64>` 반환 → inputs에 merge |
| 100 | **SID 검증** | LLM이 없는 SID 반환 → invalid_pick 처리 |

---

## F. 트랜잭션 + 감사 로그 + 무결성

| # | 시나리오 | 검증 |
|---|---------|------|
| 101 | **커밋** | Tx.commit() → 메인 DSState에 반영 |
| 102 | **롤백** | 도구 에러 → Tx.rollback() → DSState 변경 없음 |
| 103 | **슬롯 격리** | DS0~DS7 독립적 읽기/쓰기 |
| 104 | **해시 체인** | 로그 JSONL의 chain_prev → SHA-256 연결 검증 |
| 105 | **체인 위변조 감지** | 로그 한 줄 수정 → RUN.LOG.SUMMARY에서 chain_link_errors>0 |
| 106 | **상태 다이제스트** | digest() = SHA-256 (crypto), digest_fast() = FNV-1a (빠른 비교) |
| 107 | **패치 JSON** | tx.patch_json() → 어떤 슬롯이 변경됐는지 기록 |

---

## G. 리플레이 (재현성 검증)

| # | 시나리오 | 명령 | 검증 |
|---|---------|------|------|
| 108 | **구조적 리플레이** | `machina_cli replay logs/run_*.jsonl` | menu_built, selector_chosen 이벤트 존재 확인 |
| 109 | **엄격 리플레이** | `machina_cli replay_strict request.json log.jsonl` | 동일 입력 → 동일 결과 (결정적 도구) |
| 110 | **비결정적 도구** | SHELL.EXEC 리플레이 | replay_inputs 펜스로 결과 비교 |
| 111 | **최신 로그** | `scripts/replay_latest.sh` | 가장 최근 실행 재현 |
| 112 | **엄격 최신** | `scripts/replay_strict_latest.sh` | 최근 error_scan 엄격 재현 |
| 112-1 | **엄격 통합(성공/실패)** | `python3 -m unittest tests/test_replay_strict_integration.py` | run→replay_strict 성공 + 깨진 `tx_patch` 실패 검증 |

---

## H. 큐 + Autopilot + Serve (비동기 실행)

### H1. 큐 시스템

| # | 시나리오 | 검증 |
|---|---------|------|
| 113 | **인큐** | `AID.QUEUE.ENQUEUE.v1` → work/queue/inbox/에 JSON 파일 |
| 114 | **큐 디렉토리** | inbox → processing → done/failed/dlq 라이프사이클 |
| 115 | **서명 검증** | `scripts/curl_enqueue_signed.sh` → HMAC-SHA256 서명 |
| 116 | **재시도** | failed → retry/ → 대기 후 inbox로 복귀 |

### H2. Autopilot (폴링 실행)

| # | 시나리오 | 명령 | 검증 |
|---|---------|------|------|
| 117 | **단일 잡** | `machina_cli autopilot --once` | inbox에서 1건 실행 후 종료 |
| 118 | **연속 모드** | `machina_cli autopilot` | 폴링하며 inbox 감시 |
| 119 | **멀티 워커** | `autopilot --workers 4` | 병렬 처리 |

### H3. HTTP 서버 (Serve)

| # | 엔드포인트 | 메서드 | 테스트 |
|---|-----------|--------|--------|
| 120 | `/health` | GET | 서버 상태 확인 |
| 121 | `/stats` | GET | jobs_processed, jobs_ok, jobs_fail 카운터 |
| 122 | `/enqueue` | POST | JSON 작업 제출 → inbox |
| 123 | `/run_sync` | POST | 동기 실행 (결과 대기) |
| 124 | `/shutdown` | POST | 서버 종료 |
| 125 | **인증** | HMAC-SHA256 | MACHINA_API_TOKEN 없으면 enqueue 거부 |
| 126 | **rate limit** | /enqueue, /run_sync | MACHINA_API_RPM, MACHINA_API_ENQUEUE_RPM |
| 127 | **WAL** | serve | Write-Ahead Log으로 crash recovery |

---

## I. CTS (호환성 테스트 스위트)

| # | 시나리오 | 명령 | 검증 |
|---|---------|------|------|
| 128 | **툴팩 검증** | `machina_cli cts toolpacks/tier0/manifest.json goalpacks/error_scan/manifest.json` | 스키마/태그/AID 무결성 |
| 129 | **골팩 검증** | CTS | required_tools가 실제 존재하는지 |
| 130 | **스크립트** | `scripts/cts_check.sh` | 빌드+CTS 원스텝 |

---

## J. 단위 테스트 (14개 스위트)

| # | 테스트 | 파일 | 검증 대상 |
|---|--------|------|-----------|
| 131 | `test_cpq` | tests/test_cpq.cpp | ConcurrentPriorityQueue — 멀티스레드 우선순위 큐 |
| 132 | `test_wal` | tests/test_wal.cpp | Write-Ahead Log — 쓰기/복구/체크포인트 |
| 133 | `test_tx` | tests/test_tx.cpp | 트랜잭션 커밋/롤백/패치 |
| 133-1 | `test_tx_patch_apply` | tests/test_tx_patch_apply.cpp | `tool_ok.tx_patch` 적용/거부(오입력) 검증 |
| 134 | `test_memory` | tests/test_memory.cpp | MEMORY.APPEND/SEARCH 기본 동작 |
| 135 | `test_memory_query` | tests/test_memory_query.cpp | BM25 + 하이브리드 검색 |
| 136 | `test_toolhost` | tests/test_toolhost.cpp | 플러그인 .so 로드 + 도구 실행 (격리 프로세스) |
| 137 | `test_goal_registry` | tests/test_goal_registry.cpp | GoalRegistry 로드/완료 판정 |
| 138 | `test_input_safety` | tests/test_input_safety.cpp | safe_merge_patch — _system/_queue/_meta 키 차단 |
| 139 | `test_sandbox` | tests/test_sandbox.cpp | seccomp-BPF 시스콜 필터링 (x86_64 + aarch64) |
| 140 | `test_lease` | tests/test_lease.cpp | 퍼미션 리스 발급/검증/소비/만료/GC |
| 141 | `test_wal_rotation` | tests/test_wal_rotation.cpp | WAL 세그먼트 로테이션 (크기/시간), 보존 제한 |
| 142 | `test_config` | tests/test_config.cpp | 프로파일 감지, 기본값 적용, no-override |
| 143 | `test_plugin_hash` | tests/test_plugin_hash.cpp | SHA-256 파일 해시, 해시 핀닝, 불일치 거부 |

```bash
# 전체 실행 (14/14 expected)
cd build && ctest --output-on-failure
```

## J-2. Python E2E 테스트 (34개, 13 그룹)

```bash
# 공개 패키지 기준 전체 카탈로그 실행
bash scripts/run_test_catalog.sh
```

| # | 그룹 | 테스트 수 | 검증 대상 |
|---|------|-----------|-----------|
| 1 | Chat Intent | 8 | 인사/감정/잡담/컨텍스트 의도 분류 |
| 2 | Shell Command | 4 | GPU/메모리/디스크/프로세스 도구 호출 |
| 3 | Web Search | 4 | 검색 의도 + 영어 쿼리 강제 |
| 4 | Code Execution | 4 | 피보나치/계산/구구단/정렬 코드 생성 |
| 5 | Memory | 2 | 기억 저장 + 검색 |
| 6 | File Operations | 2 | 파일 읽기 + 쓰기 |
| 7 | Config | 2 | 백엔드/모델 변경 |
| 8 | URL Fetch | 1 | HTTP GET |
| 9 | Utility System | 1 | 유틸 목록 |
| 10 | Chat Response | 1 | 자연어 응답 생성 |
| 11 | Summary | 1 | 도구 결과 요약 |
| 12 | Continue Loop | 2 | Done/Action 연속 판단 |
| 13 | Auto-Memory | 2 | 개인정보 자동 감지 + 인사 스킵 |

---

## K. 보안 테스트

| # | 시나리오 | 검증 |
|---|---------|------|
| 144 | **셸 allowlist** | MACHINA_SHELL_ALLOWED_EXE에 없는 실행파일 → 차단 |
| 145 | **정책 스크립트 경로** | policies/ 외부 스크립트 → "policy script path not allowed" |
| 146 | **정책 실행파일** | allowed_exec_basenames 외 → "policy exe not allowed" |
| 147 | **rlimit CPU** | 프로세스 CPU 시간 초과 → 강제 종료 |
| 148 | **rlimit 메모리** | 가상 메모리 초과 → OOM |
| 149 | **rlimit 파일 크기** | 10MB 이상 파일 쓰기 → 차단 |
| 150 | **rlimit FD** | 64개 이상 FD 열기 → 에러 |
| 151 | **rlimit nproc** | fork bomb → 프로세스 수 제한 |
| 152 | **no_new_privs** | 권한 상승 방지 |
| 153 | **입력 위생** | _system, _queue, _meta 키 → safe_merge_patch에서 제거 |
| 154 | **Genesis 경로 제한** | toolpacks/runtime_genesis/src 외부 쓰기 → 차단 |
| 155 | **HMAC 서명** | 잘못된 서명 → HTTP 403 |
| 156 | **Capability 제한** | `_capabilities.blocked_tools` → 메뉴에서 제외 |

---

## L. 임베딩 시스템 테스트

| # | 시나리오 | 검증 |
|---|---------|------|
| 157 | **hash 프로바이더** | 외부 의존 없이 결정적 임베딩 |
| 158 | **cmd 프로바이더** | E5-small 외부 프로세스 호출 |
| 159 | **배치 임베딩** | embed_texts_batch() — 여러 텍스트 한 번에 |
| 160 | **fallback** | cmd 실패 시 hash로 자동 전환 |
| 161 | **L2 정규화** | 벡터 크기 = 1.0 |
| 162 | **타임아웃** | MACHINA_EMBED_TIMEOUT_MS 초과 → fallback |

---

## M. 통합 시나리오 (조합 테스트)

이것들이 **실제 사용 시나리오** — 여러 도구가 체인으로 엮인다.

| # | 시나리오 | 도구 체인 | 설명 |
|---|---------|-----------|------|
| 163 | **로그 분석 파이프라인** | ERROR_SCAN → REPORT_SUMMARY | 파일 스캔 → 요약 리포트 |
| 164 | **스캔+기억** | ERROR_SCAN → MEMORY.APPEND | 스캔 결과를 메모리에 저장 |
| 165 | **기억+소환+판단** | APPEND → QUERY → (LLM이 판단) | 과거 기록 기반 다음 행동 결정 |
| 166 | **파일 읽기+분석** | FILE.READ → ERROR_SCAN | 파일 내용 확인 → 에러 스캔 |
| 167 | **HTTP+분석** | HTTP_GET → FILE.WRITE → ERROR_SCAN | 웹 데이터 가져와서 분석 |
| 168 | **GPU 진단** | GPU_SMOKE → GPU_METRICS → MEMORY.APPEND | GPU 확인 → 상세 → 기록 |
| 169 | **자기 진단** | PROC.SELF_METRICS → MEMORY.APPEND | 프로세스 상태 → 기록 |
| 170 | **Genesis 전체 흐름** | WRITE → COMPILE → LOAD → 새 도구 실행 | 도구 생성부터 사용까지 |
| 171 | **Genesis + 메모리** | WRITE → COMPILE → LOAD → MEMORY.APPEND | 새 도구 만들고 결과 기억 |
| 172 | **벡터 지식 베이스** | UPSERT × N → VECDB.QUERY | 지식 축적 → 의미 검색 |
| 173 | **큐 파이프라인** | QUEUE.ENQUEUE → autopilot → done/ | 비동기 작업 제출 → 처리 |
| 174 | **로그→리플레이** | run → logs/ → replay_strict | 실행 → 로그 → 재현 |
| 175 | **셸→파일→스캔** | SHELL(curl) → FILE.WRITE → ERROR_SCAN | 데이터 수집→저장→분석 |
| 176 | **다중 목표 연속** | GPU_SMOKE → ERROR_SCAN → GENESIS | 진단 → 분석 → 자기 수정 |

---

## N. 환경변수 조합 테스트

| # | 변수 | 값 | 영향 |
|---|------|-----|------|
| 177 | `MACHINA_SELECTOR` | HEURISTIC / GPU_CENTROID | 셀렉터 백엔드 |
| 178 | `MACHINA_USE_GPU` | 0 / 1 | GPU 빌드 |
| 179 | `MACHINA_POLICY_CMD` | 각 policy 드라이버 | LLM 백엔드 전환 |
| 180 | `MACHINA_GENESIS_ENABLE` | 0 / 1 | Genesis 활성화 |
| 181 | `MACHINA_GENESIS_AUTOTRIGGER` | 0 / 1 | 없는 도구 자동 생성 |
| 182 | `MACHINA_GENESIS_AUTOSTUB` | 0 / 1 | 자동 스텁 |
| 183 | `MACHINA_GENESIS_COMPILE_RETRIES` | 0-5 | 컴파일 재시도 횟수 |
| 184 | `MACHINA_SHELL_ALLOWED_EXE` | "ls,cat,grep,..." | 셸 허용 명령 |
| 185 | `MACHINA_EMBED_PROVIDER` | hash / cmd | 임베딩 방식 |
| 186 | `MACHINA_POLICY_FAIL_THRESHOLD` | 1-10 | 서킷 브레이커 임계 |
| 187 | `MACHINA_POLICY_COOLDOWN_MS` | ms | 서킷 브레이커 쿨다운 |
| 188 | `MACHINA_API_TOKEN` | 문자열 | HTTP 인증 키 |
| 189 | `MACHINA_API_RPM` | 숫자 | API rate limit |

---

## O. 실전 테스트 스크립트 예시

### O1. 빠른 전체 점검 (5분)

```bash
source machina_env.sh
cd build && ctest --output-on-failure          # 단위 13개
cd ..
./build/machina_cli cts toolpacks/tier0/manifest.json goalpacks/error_scan/manifest.json
./build/machina_cli run examples/run_request.error_scan.json
./build/machina_cli run examples/run_request.gpu_smoke.json
./build/machina_cli run examples/run_request.gpu_metrics.json
scripts/replay_latest.sh
```

### O2. 메모리 왕복 테스트

```bash
# 기억 저장
cat <<'EOF' > /tmp/mem_write.json
{"goal_id":"goal.ERROR_SCAN.v1","inputs":{"input_path":"../README.md","pattern":"Trinity","max_rows":100},"candidate_tags":["tag.log","tag.error","tag.report"],"control_mode":"FALLBACK_ONLY"}
EOF
./build/machina_cli run /tmp/mem_write.json
# → DS0에 스캔 결과, DS2에 요약
# 이후 MEMORY.APPEND로 저장 → MEMORY.QUERY로 소환
```

### O3. Genesis 전체 흐름

```bash
export MACHINA_GENESIS_ENABLE=1
./build/machina_cli run examples/run_request.genesis_demo_hello.json
# → hello_tool.cpp 작성 → 컴파일 → .so 로드 → 실행
ls toolpacks/runtime_plugins/*.so   # 새 플러그인 확인
```

### O4. LLM 셀렉터 테스트

```bash
# LLM HTTP policy (example)
export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
./build/machina_cli run examples/run_request.error_scan.json --control_mode POLICY_ONLY

# fallback policy (deterministic)
export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
./build/machina_cli run examples/run_request.error_scan.json --control_mode BLENDED
```

### O5. HTTP 서버 테스트

```bash
./build/machina_cli serve --port 8090 &
curl http://localhost:8090/health
curl http://localhost:8090/stats
# 인증 설정 시:
export MACHINA_API_TOKEN="test-secret"
scripts/curl_enqueue_signed.sh http://localhost:8090 examples/run_request.error_scan.json
```

---

## 요약 카운트

| 카테고리 | 항목 수 |
|---------|---------|
| A. 개별 도구 | 29 |
| B. 메모리/소환 | 30 |
| C. Genesis (도구 생성) | 13 |
| D. 목표 실행 | 12 |
| E. 셀렉터/제어 모드 | 16 |
| F. 트랜잭션/감사/무결성 | 7 |
| G. 리플레이 | 6 |
| H. 큐/Autopilot/Serve | 15 |
| I. CTS | 3 |
| J. 단위 테스트 (C++) | 14 |
| J-2. E2E 테스트 (Python) | 34 |
| K. 보안 | 13 |
| L. 임베딩 | 6 |
| M. 통합 시나리오 | 14 |
| N. 환경변수 조합 | 13 |
| O. MCP 브릿지 | 8 |
| P. 풀 파이프라인 (Python) | 25 |
| **합계** | **258** |

---

## O. MCP 브릿지 (8개)

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | MCP 서버 연결 (stdio) | `mcp_servers.json` 로드 → connect → tool discovery |
| 2 | MCP 서버 연결 (SSE) | SSE transport connect + tool list |
| 3 | MCP 도구 호출 | `AID.MCP.WEB_SEARCH.WEBSEARCHPRO.v1` 실행 → 결과 반환 |
| 4 | MCP 타임아웃 | 120초 후 타임아웃 확인 |
| 5 | MCP enable/disable | config 파일 atomic 수정 + 연결/해제 |
| 6 | MCP add/remove server | 런타임 서버 추가/삭제 |
| 7 | MCP reload | `mcp_reload` → 기존 연결 해제 → 재연결 |
| 8 | MCP 권한 | safe prefix → ALLOW, 기타 → ASK |

## P. 풀 파이프라인 테스트 (25개)

공개 패키지에서는 `scripts/run_test_catalog.sh` + `scripts/run_guardrails.sh` 조합으로
Intent→Dispatch→Execution 핵심 경로를 검증한다.

| # | 카테고리 | 테스트 수 | 검증 |
|---|----------|----------|------|
| 1 | 인사/대화 | 5 | reply 타입 분류 |
| 2 | 코드 실행 | 4 | CODE.EXEC 디스패치 + 실행 |
| 3 | 셸 명령 | 3 | SHELL.EXEC 디스패치 + 실행 |
| 4 | 웹 검색 | 2 | WEB_SEARCH 디스패치 |
| 5 | 메모리 | 2 | MEMORY.APPEND/QUERY |
| 6 | 파일 작업 | 3 | FILE.WRITE/READ/LIST |
| 7 | URL 읽기 | 1 | HTTP_GET 디스패치 |
| 8 | 설정 변경 | 1 | config 타입 분류 |
| 9 | 복합/엣지 | 3 | 멀티스텝, 긴 코딩 요청 |
| 10 | Continue 판단 | 1 | 도구 결과 후 다음 행동 |
