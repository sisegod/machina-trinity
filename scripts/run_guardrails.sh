#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m py_compile \
  policies/chat_driver_util.py \
  machina_dispatch_registry.py \
  machina_mcp_connection.py \
  machina_evolution_policy.py \
  machina_evolution_governor.py \
  machina_brain_orchestrator.py \
  scripts/validate_aid_refs.py \
  scripts/validate_docs_refs.py \
  scripts/security_guardrails.py \
  scripts/work_memory_maintenance.py \
  tests/test_python_guards.py \
  tests/test_guardrail_scripts.py \
  tests/test_pulse_guards.py \
  tests/test_pulse_flow.py \
  tests/test_ops_scripts.py \
  tests/test_evolution_governance.py

python3 -m unittest \
  tests/test_python_guards.py \
  tests/test_guardrail_scripts.py \
  tests/test_pulse_guards.py \
  tests/test_pulse_flow.py \
  tests/test_ops_scripts.py \
  tests/test_evolution_governance.py
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/validate_aid_refs.py
python3 scripts/validate_docs_refs.py
python3 scripts/security_guardrails.py
python3 scripts/work_memory_maintenance.py --min-size-mb 9999

echo "[guardrails] all checks passed"
