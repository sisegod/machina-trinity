"""Machina Autonomic Engine — modular package.

Public API (backward-compatible):
    from machina_autonomic import AutonomicEngine, set_alert_callback
"""

from machina_autonomic._constants import (  # noqa: F401
    set_alert_callback,
    is_dev_explore,
    set_dev_explore,
    toggle_dev_explore,
    new_trace_id,
    new_span_id,
    set_trace_context,
)
from machina_autonomic._engine import AutonomicEngine  # noqa: F401

__all__ = [
    "AutonomicEngine", "set_alert_callback",
    "is_dev_explore", "set_dev_explore", "toggle_dev_explore",
    "new_trace_id", "new_span_id", "set_trace_context",
]
