"""Autonomic low-risk ASK auto-approval helpers."""

import os

SAFE_ASK_AIDS_DEFAULT = {
    "AID.NET.HTTP_GET.v1",
    "AID.ERROR_SCAN.v1",
    "AID.PROC.SELF_METRICS.v1",
    "AID.GPU_SMOKE.v1",
    "AID.GPU.METRICS.v1",
}

SQ_TOOL_TO_AID = {
    "http_get": "AID.NET.HTTP_GET.v1",
    "error_scan_csv": "AID.ERROR_SCAN.v1",
    "proc_self_metrics": "AID.PROC.SELF_METRICS.v1",
    "gpu_smoke": "AID.GPU_SMOKE.v1",
    "gpu_metrics": "AID.GPU.METRICS.v1",
}


def autonomic_auto_approve_enabled() -> bool:
    return os.getenv("MACHINA_AUTONOMIC_AUTO_APPROVE", "1").lower() in ("1", "true", "yes", "on")


def autonomic_auto_approve_aids() -> set:
    raw = os.getenv("MACHINA_AUTONOMIC_AUTO_APPROVE_AIDS", "")
    extra = {x.strip() for x in raw.split(",") if x.strip()}
    return set(SAFE_ASK_AIDS_DEFAULT) | extra


def is_autonomic_auto_approved_aid(aid: str) -> bool:
    if not autonomic_auto_approve_enabled():
        return False
    return aid in autonomic_auto_approve_aids()


def sq_auto_approved_tool(tool_key: str) -> bool:
    if not autonomic_auto_approve_enabled():
        return False
    aid = SQ_TOOL_TO_AID.get(tool_key, "")
    if not aid:
        return False
    return aid in autonomic_auto_approve_aids()
