#!/usr/bin/env python3
"""
Generate MACHINA serve HMAC headers for a request body.

Signature scheme (v1):
canonical = ts + "\n" + nonce + "\n" + method + "\n" + path + "\n" + sha256(body) + "\n"
sig = hex(hmac_sha256(secret, canonical))

Usage:
  MACHINA_API_HMAC_SECRET=... ./machina_sign.py POST /enqueue < body.json
"""
import hashlib, hmac, os, sys, time, secrets

def die(msg: str, code: int = 2):
    print(msg, file=sys.stderr)
    sys.exit(code)

def main():
    if len(sys.argv) != 3:
        die("usage: machina_sign.py <METHOD> <PATH>")
    method = sys.argv[1].upper()
    path = sys.argv[2]
    secret = os.environ.get("MACHINA_API_HMAC_SECRET", "")
    if not secret:
        die("MACHINA_API_HMAC_SECRET is required")
    body = sys.stdin.buffer.read()
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"{ts}\n{nonce}\n{method}\n{path}\n{body_hash}\n".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    print(f"X-Machina-Ts: {ts}")
    print(f"X-Machina-Nonce: {nonce}")
    print(f"X-Machina-Signature: v1={sig}")

if __name__ == "__main__":
    main()
