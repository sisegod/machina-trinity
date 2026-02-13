#!/usr/bin/env python3
"""Minimal Machina policy driver — picks the first available tool.

Usage:
    export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
    export MACHINA_POLICY_CMD="python3 examples/policy_drivers/hello_policy.py"
    ./build/machina_cli run examples/run_request.gpu_smoke.json
"""
import json, sys

def main():
    if len(sys.argv) < 2:
        print("<NOOP><END>")
        return

    with open(sys.argv[1], "r") as f:
        payload = json.load(f)

    menu = payload.get("menu", [])
    if not menu:
        print("<NOOP><END>")
        return

    # Pick the first tool in the menu
    sid = menu[0].get("sid", "SID0001")
    aid = menu[0].get("aid", "unknown")
    print(f"<PICK><{sid}><END>")  # Select first available tool

if __name__ == "__main__":
    main()
