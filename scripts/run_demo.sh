#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cli=$(./scripts/build_fast.sh)

case "${1:-error_scan}" in
  error_scan)
    "$cli" run examples/run_request.error_scan.json
    ;;
  gpu_smoke)
    "$cli" run examples/run_request.gpu_smoke.json
    ;;
  genesis_policy_codegen)
    export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
    export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
    "$cli" run examples/run_request.genesis_demo_hello.json
    ;;
  genesis_hello)
    "$cli" run examples/run_request.genesis_demo_hello.json
    ;;
  missing_tool_autostub)
    export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
    export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
    export MACHINA_GENESIS_AUTOTRIGGER=1
    export MACHINA_GENESIS_AUTOSTUB=1
    "$cli" run examples/run_request.missing_tool_autostub.json
    ;;
  *)
    echo "usage: $0 {error_scan|gpu_smoke|genesis_hello|genesis_policy_codegen|missing_tool_autostub}" >&2
    exit 2
    ;;
esac
