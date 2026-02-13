# serve API (운영)

`machina_cli serve --root <ROOT> --port 8080` 로 HTTP API를 제공합니다.

기본 포트: **8080** (코드 기본값). `--port`로 변경 가능.

## Threading Model

- **Per-connection threading**: 각 accepted 연결은 detached 스레드에서 처리
- **Slowloris defense**: 연결 당 10초 소켓 타임아웃 (`SO_RCVTIMEO`/`SO_SNDTIMEO`)
- **Worker cap**: `--workers` 옵션은 0-64 범위로 클램프

## Endpoints

### GET /health
- 인증 불필요
- 응답: `{"status":"ok"}` (200)
- 서버 liveness 확인용

### GET /stats
- 인증 필요 (Token 또는 HMAC)
- 응답: 큐 통계 JSON (`jobs_processed`, `jobs_ok`, `jobs_fail`, queue sizes)

### POST /shutdown
- 인증 필요 (Token 또는 HMAC)
- 동작: graceful server shutdown

### GET /metrics
- 인증 불필요
- Prometheus text exposition format (아래 Observability 섹션 참조)

### POST /enqueue
- Body: run_request JSON
- 동작: `<ROOT>/queue/inbox`에 파일로 적재 → autopilot이 처리
- **Idempotency**: `request_id` 필드가 있으면 dedup cache로 중복 방지 (TTL 기본 5분, `MACHINA_DEDUP_TTL_MS`)
- Dedup 엔트리는 WAL에 기록되어 재시작 후에도 복원

### POST /run_sync
- Body: run_request JSON
- 동작: 즉시 `run` 실행 후 결과 반환(운영에선 제한적으로 권장)

## Auth

### Token (옵션)
- env: `MACHINA_API_TOKEN`
- header: `Authorization: Bearer <token>`

### HMAC (옵션, 권장)
- env: `MACHINA_API_HMAC_SECRET`
- env: `MACHINA_API_HMAC_TTL_SEC` (default 60)

Headers:
- `X-Machina-Ts`: unix seconds(또는 ms)
- `X-Machina-Nonce`: 임의 문자열(재사용 금지)
- `X-Machina-Signature`: `v1=<hex>` 또는 `<hex>`

Canonical:
```
ts + "\n" + nonce + "\n" + method + "\n" + path + "\n" + sha256(body) + "\n"
```

#### Nonce Replay Protection
- TTL-based pruning: 5K 엔트리 초과 시 만료된 nonce 정리
- Hard cap: 10K 엔트리 초과 시 가장 오래된 nonce부터 제거
- nonce_cache는 `http_mu` 뮤텍스로 보호

### 예시 스크립트
- `scripts/machina_sign.py`
- `scripts/curl_enqueue_signed.sh`

## Rate limiting (옵션)
- `MACHINA_API_RPM` (global)
- `MACHINA_API_ENQUEUE_RPM`
- `MACHINA_API_RUNSYNC_RPM`

## Body limit (옵션)
- `MACHINA_API_MAX_BODY_BYTES` (default 2MB)

## Observability

### GET /metrics
Prometheus text exposition format (`text/plain; version=0.0.4`).

Counters:
- `machina_jobs_processed_total` — 총 처리 작업 수
- `machina_jobs_ok_total` — 성공 작업 수
- `machina_jobs_fail_total` — 실패 작업 수

Per-tool counters (label: `aid`):
- `machina_tool_ok_total{aid="AID.XXX"}` — 도구별 성공 횟수
- `machina_tool_fail_total{aid="AID.XXX"}` — 도구별 실패 횟수
- `machina_tool_duration_ms_total{aid="AID.XXX"}` — 도구별 누적 실행 시간 (ms)

Gauges:
- `machina_queue_inbox_size` — inbox 큐 크기
- `machina_queue_processing_size` — processing 큐 크기
- `machina_queue_retry_size` — retry 큐 크기
- `machina_queue_failed_size` — failed 큐 크기
- `machina_queue_dlq_size` — DLQ 크기
- `machina_memq_size` — 메모리 큐 크기
- `machina_workers_configured` — 워커 수

### 사용 예시

```bash
# Prometheus scrape config
- job_name: 'machina'
  static_configs:
    - targets: ['localhost:8080']
  metrics_path: /metrics

# 직접 확인
curl http://localhost:8080/metrics
```
