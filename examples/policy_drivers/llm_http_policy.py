#!/usr/bin/env python3
"""LLM HTTP policy driver — forwards decisions to an external LLM endpoint.

Usage:
    export MACHINA_POLICY_ALLOWED_SCRIPT_ROOT="$(pwd)/examples/policy_drivers"
    export MACHINA_POLICY_CMD="python3 examples/policy_drivers/llm_http_policy.py"
    export MACHINA_POLICY_LLM_URL="http://127.0.0.1:9000/machina_policy"
    # Optional: export MACHINA_POLICY_LLM_AUTH="Bearer your-token"
"""
import json, os, sys, urllib.request

def main():
    if len(sys.argv) < 2:
        print("<NOOP><END>")
        return

    url = os.getenv("MACHINA_POLICY_LLM_URL")
    if not url:
        print("<NOOP><END>", file=sys.stderr)
        print("Error: MACHINA_POLICY_LLM_URL not set", file=sys.stderr)
        print("<NOOP><END>")
        return

    with open(sys.argv[1], "r") as f:
        payload = json.load(f)

    # Build HTTP request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    auth = os.getenv("MACHINA_POLICY_LLM_AUTH")
    if auth:
        req.add_header("Authorization", auth)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            # Expect {"machina_out": "<PICK><SID0007><END>"} or raw text
            out = body.get("machina_out", body.get("output", "<NOOP><END>"))
            print(out)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        print("<NOOP><END>")

if __name__ == "__main__":
    main()
