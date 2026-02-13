# serve API (operations)

`machina_cli serve --root <ROOT> --port 8080` exposes an HTTP API.

Default port is **8080**. Override with `--port`.

## Threading model

- Per-connection threading: each accepted socket is handled in a detached thread
- Slowloris defense: 10-second socket timeout per connection (`SO_RCVTIMEO`/`SO_SNDTIMEO`)
- Worker cap: `--workers` is clamped to the range `0..64`

## Endpoints

### GET /health
- No auth required
- Response: `{"status":"ok"}` (200)
- Liveness check

### GET /stats
- Auth required (Token or HMAC)
- Returns queue and job stats (`jobs_processed`, `jobs_ok`, `jobs_fail`, queue sizes)

### POST /shutdown
- Auth required (Token or HMAC)
- Triggers graceful server shutdown

### GET /metrics
- No auth required
- Prometheus text exposition format

### POST /enqueue
- Body: `run_request` JSON
- Behavior: writes request files to `<ROOT>/queue/inbox`, then autopilot processes them
- Idempotency: if `request_id` exists, dedup cache blocks duplicates
  - default TTL: 5 minutes (`MACHINA_DEDUP_TTL_MS`)
- Dedup entries are persisted via WAL and restored after restart

### POST /run_sync
- Body: `run_request` JSON
- Behavior: executes `run` immediately and returns the result
- Intended for controlled/limited operational use

## Auth

### Token (optional)
- Env: `MACHINA_API_TOKEN`
- Header: `Authorization: Bearer <token>`

### HMAC (optional, recommended)
- Env: `MACHINA_API_HMAC_SECRET`
- Env: `MACHINA_API_HMAC_TTL_SEC` (default `60`)

Required headers:
- `X-Machina-Ts`: unix seconds (or milliseconds)
- `X-Machina-Nonce`: random unique string
- `X-Machina-Signature`: `v1=<hex>` or `<hex>`

Canonical string:

```text
ts + "\n" + nonce + "\n" + method + "\n" + path + "\n" + sha256(body) + "\n"
```

### Nonce replay protection

- TTL-based pruning: expired nonces are cleaned once cache exceeds 5K entries
- Hard cap: oldest entries are evicted after 10K entries
- Nonce cache is protected by `http_mu`

### Example scripts

- `scripts/machina_sign.py`
- `scripts/curl_enqueue_signed.sh`

## Optional limits

### Rate limits

- `MACHINA_API_RPM` (global)
- `MACHINA_API_ENQUEUE_RPM`
- `MACHINA_API_RUNSYNC_RPM`

### Body size limit

- `MACHINA_API_MAX_BODY_BYTES` (default `2MB`)

## Observability

`GET /metrics` exposes Prometheus text format (`text/plain; version=0.0.4`).

Counters:
- `machina_jobs_processed_total`
- `machina_jobs_ok_total`
- `machina_jobs_fail_total`

Per-tool counters (`aid` label):
- `machina_tool_ok_total{aid="AID.XXX"}`
- `machina_tool_fail_total{aid="AID.XXX"}`
- `machina_tool_duration_ms_total{aid="AID.XXX"}`

Gauges:
- `machina_queue_inbox_size`
- `machina_queue_processing_size`
- `machina_queue_retry_size`
- `machina_queue_failed_size`
- `machina_queue_dlq_size`
- `machina_memq_size`
- `machina_workers_configured`

### Example usage

```bash
# Prometheus scrape config
- job_name: 'machina'
  static_configs:
    - targets: ['localhost:8080']
  metrics_path: /metrics

# Direct check
curl http://localhost:8080/metrics
```
