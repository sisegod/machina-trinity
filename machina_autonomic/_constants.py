"""Machina Autonomic Engine — Configuration constants, timings, logging, alerts."""

import logging
import time

from machina_shared import (
    _jsonl_append,
    MEM_DIR,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
import os

HEARTBEAT_INTERVAL = 60  # seconds
AUDIT_LOG_FILE = MEM_DIR / "autonomic_audit.jsonl"
KNOWLEDGE_STREAM = "knowledge"

# Dev Exploration Mode — set MACHINA_DEV_EXPLORE=1 to enable aggressive self-improvement
# Runtime-mutable via set_dev_explore() / toggle_dev_explore()
_DEV_EXPLORE = os.getenv("MACHINA_DEV_EXPLORE", "") == "1"
_CLOUD_AUTO = os.getenv("MACHINA_CLOUD_AUTO", "") == "1"


def is_dev_explore() -> bool:
    """Check if DEV EXPLORE mode is active (runtime-safe)."""
    return _DEV_EXPLORE


def set_dev_explore(enabled: bool):
    """Set DEV EXPLORE mode at runtime (also updates env var)."""
    global _DEV_EXPLORE
    _DEV_EXPLORE = enabled
    os.environ["MACHINA_DEV_EXPLORE"] = "1" if enabled else ""


def toggle_dev_explore() -> bool:
    """Toggle DEV EXPLORE mode. Returns new state."""
    set_dev_explore(not _DEV_EXPLORE)
    return _DEV_EXPLORE

# Timing profiles: (idle_threshold, rate_limit) in seconds
_TIMINGS_NORMAL = {
    "heartbeat": 60,
    "l1_idle": 180, "l1_rate": 300,       # Reflect: 3min idle, 5min rate
    "l2_idle": 300, "l2_rate": 600,       # Test: 5min idle, 10min rate
    "l3_idle": 600, "l3_rate": 1800,      # Heal: 10min idle, 30min rate
    "l4_rate": 1800,                       # Hygiene: 30min
    "l5_idle": 900, "l5_rate": 1800,      # Curiosity: 15min idle, 30min rate
    "stasis_threshold": 6,                 # 6 identical hashes before stasis (was 3)
    "stasis_curiosity_rate": 1800,         # Even in stasis, try curiosity every 30min
    "curiosity_max_per_day": 10,           # Allow more exploration (was 3)
    "curiosity_cooldown": 1800,            # 30min cooldown (was 2hr)
    "report_min_interval": 0,
    "burst_idle": 1800,                    # Burst after 30min idle (was 2hr)
    "burst_rate": 3600,                    # At most 1 burst per hour
    "burst_max_sec": 3600,
    "burst_stall_limit": 5,
    "web_explore_rate": 1800,              # Web explore every 30min (was 1hr)
}
_TIMINGS_DEV = {
    "heartbeat": 30,
    "l1_idle": 60, "l1_rate": 300,
    "l2_idle": 120, "l2_rate": 600,
    "l3_idle": 180, "l3_rate": 600,
    "l4_rate": 1800,
    "l5_idle": 180, "l5_rate": 600,
    "stasis_threshold": 5,
    "stasis_curiosity_rate": 600,
    "curiosity_max_per_day": 20,
    "curiosity_cooldown": 600,
    "report_min_interval": 300,
    "burst_idle": 180,
    "burst_rate": 600,
    "burst_max_sec": 3600,
    "burst_stall_limit": 5,
    "web_explore_rate": 900,
}

logger = logging.getLogger("autonomic")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(message)s", datefmt="%H:%M:%S"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Configuration constants — Qwen3 14B has 32K context, be generous
WEB_MAX_RESULTS = 5
WEB_DEEP_READ_URLS = 3
WEB_PAGE_CONTENT_LEN = 8000
WEB_SUMMARY_TOKENS = 800
REFLECT_EXPERIENCE_WINDOW = 100
REFLECT_RECENT_SLICE = 50
TOOL_TEST_BATCH_EMPTY = 10
TOOL_TEST_BATCH_MAX = 8
TOOL_TEST_BATCH_BENCH = 12
# Storage limits
STORE_SUMMARY_LEN = 2000
STORE_RESULT_LEN = 1000
STORE_CODE_LEN = 3000
STORE_DETAIL_LEN = 1000

# Alert callback — set by telegram_bot.py to enable Telegram notifications.
_alert_callback = None


def set_alert_callback(cb):
    """Register a callback for proactive Telegram alerts."""
    global _alert_callback
    _alert_callback = cb


def _send_alert(message: str):
    """Send proactive alert via callback (if registered). Non-blocking, never raises."""
    if _alert_callback:
        try:
            _alert_callback(message)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Alert callback failed: {type(e).__name__}: {e}")


import secrets as _secrets

# Trace context: lightweight span tracking for observability
_current_trace_id: str = ""
_current_span_id: str = ""


def new_trace_id() -> str:
    """Generate a new 16-char hex trace ID."""
    return _secrets.token_hex(8)


def new_span_id() -> str:
    """Generate a new 8-char hex span ID."""
    return _secrets.token_hex(4)


def set_trace_context(trace_id: str = "", span_id: str = ""):
    """Set current trace/span for automatic propagation to audit logs."""
    global _current_trace_id, _current_span_id
    _current_trace_id = trace_id
    _current_span_id = span_id


def _audit_log(level: str, event: str, detail: str = "",
               success: bool = True, duration_ms: int = 0,
               request_id: str = "",
               trace_id: str = "", span_id: str = "", parent_span_id: str = ""):
    """Write structured audit entry to autonomic_audit.jsonl.

    Supports OTel-compatible tracing: trace_id groups related spans,
    span_id identifies this operation, parent_span_id links to caller.
    If trace_id/span_id not provided, uses current trace context.
    """
    try:
        entry = {
            "ts_ms": int(time.time() * 1000),
            "level": level,
            "event": event,
            "detail": detail[:STORE_DETAIL_LEN],
            "success": success,
            "duration_ms": duration_ms,
        }
        if request_id:
            entry["request_id"] = request_id
        # Trace context — auto-fill from current context if not provided
        tid = trace_id or _current_trace_id
        sid = span_id or _current_span_id
        if tid:
            entry["trace_id"] = tid
        if sid:
            entry["span_id"] = sid
        if parent_span_id:
            entry["parent_span_id"] = parent_span_id
        _jsonl_append(AUDIT_LOG_FILE, entry)
    except Exception:
        pass  # audit logging must never break the engine
