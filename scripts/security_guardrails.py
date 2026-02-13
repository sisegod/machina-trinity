#!/usr/bin/env python3
"""Security guardrails for tracked config files.

Current checks:
- mcp_servers.json must not contain plaintext Bearer tokens
- mcp_servers.json must not contain plaintext *_API_KEY values
"""

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MCP_CONFIG = ROOT / "mcp_servers.json"

_ENV_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Z0-9_]+\}$")
_BEARER_PLACEHOLDER_RE = re.compile(r"^Bearer\s+\$\{[A-Z0-9_]+\}$")


def is_placeholder(value: str) -> bool:
    return bool(_ENV_PLACEHOLDER_RE.match(value))


def collect_security_issues(mcp_config_path: Path = MCP_CONFIG) -> list[str]:
    issues: list[str] = []
    if not mcp_config_path.exists():
        return issues

    with mcp_config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    servers = cfg.get("servers", cfg.get("mcpServers", {}))
    for name, scfg in servers.items():
        if not isinstance(scfg, dict):
            continue
        headers = scfg.get("headers", {})
        if isinstance(headers, dict):
            auth = headers.get("Authorization", "")
            if isinstance(auth, str) and auth.startswith("Bearer "):
                if not _BEARER_PLACEHOLDER_RE.match(auth):
                    issues.append(f"{name}: Authorization must be 'Bearer ${{ENV_KEY}}', got plaintext.")
        env_map = scfg.get("env", {})
        if isinstance(env_map, dict):
            for k, v in env_map.items():
                if not isinstance(v, str):
                    continue
                if k.upper().endswith("_API_KEY") and v and not is_placeholder(v):
                    issues.append(f"{name}: env.{k} should use placeholder '${{{k}}}', not plaintext.")
    return issues


def main() -> int:
    if not MCP_CONFIG.exists():
        print("OK: mcp_servers.json not found")
        return 0

    issues = collect_security_issues(MCP_CONFIG)

    if issues:
        print("Security guardrail violations:")
        for i in issues:
            print(f"- {i}")
        return 1

    print("OK: security guardrails passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
