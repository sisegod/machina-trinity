#!/usr/bin/env python3
"""Python guardrail regression tests for fast-path/AID/MCP env hardening."""

import os
import sys
import unittest
import types
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# policies/ is not a Python package; import by path.
POLICIES_DIR = ROOT / "policies"
if str(POLICIES_DIR) not in sys.path:
    sys.path.insert(0, str(POLICIES_DIR))

import chat_driver_util as cdu  # type: ignore
from machina_dispatch import execute_intent
from machina_mcp_connection import MCPServerConnection, _resolve_env_refs
from machina_dispatch_registry import resolve_alias


class FastPathRegressionTests(unittest.TestCase):
    def test_search_fast_path_uses_canonical_web_search_aid(self):
        intent = cdu.try_fast_path("최신 AI 뉴스 검색해줘")
        self.assertEqual(intent.get("type"), "action")
        self.assertTrue(intent.get("actions"))
        action = intent["actions"][0]
        self.assertEqual(action.get("kind"), "tool")
        self.assertEqual(action.get("aid"), "AID.NET.WEB_SEARCH.v1")
        self.assertIn("query", action.get("inputs", {}))

    def test_shell_fast_path_builds_executable_command(self):
        intent = cdu.try_fast_path("GPU 상태 보여줘")
        self.assertEqual(intent.get("type"), "action")
        action = intent["actions"][0]
        self.assertEqual(action.get("kind"), "tool")
        self.assertEqual(action.get("aid"), "AID.SHELL.EXEC.v1")
        self.assertEqual(action.get("inputs", {}).get("cmd"), "nvidia-smi")

    def test_cleanup_fast_path_builds_non_empty_shell_command(self):
        intent = cdu.try_fast_path("쓸데없는 파일들 있으면 다 지워줘")
        self.assertEqual(intent.get("type"), "action")
        action = intent["actions"][0]
        self.assertEqual(action.get("aid"), "AID.SHELL.EXEC.v1")
        cmd = action.get("inputs", {}).get("cmd", "")
        self.assertTrue(isinstance(cmd, str) and len(cmd.strip()) > 0)

    def test_file_read_fast_path_extracts_path(self):
        intent = cdu.try_fast_path("work/demo.txt 읽어줘")
        self.assertEqual(intent.get("type"), "action")
        action = intent["actions"][0]
        self.assertEqual(action.get("kind"), "tool")
        self.assertEqual(action.get("aid"), "AID.FILE.READ.v1")
        self.assertEqual(action.get("inputs", {}).get("path"), "work/demo.txt")

    def test_tool_aid_map_has_no_removed_legacy_aids(self):
        self.assertEqual(cdu._TOOL_AID_MAP["search"], "AID.NET.WEB_SEARCH.v1")
        self.assertEqual(cdu._TOOL_AID_MAP["web_search"], "AID.NET.WEB_SEARCH.v1")
        self.assertEqual(cdu._TOOL_AID_MAP["genesis"], "AID.GENESIS.WRITE_FILE.v1")

    def test_fast_path_intent_is_dispatch_executable(self):
        intent = cdu.try_fast_path("최신 AI 뉴스 검색해줘")
        with patch("machina_dispatch_exec.run_machina_tool", return_value="ok:web-search"):
            out = execute_intent(intent, "최신 AI 뉴스 검색해줘")
        self.assertIn("ok:web-search", out)

    def test_resolve_intent_fast_distill_path_normalizes_aid(self):
        fake_learning = types.ModuleType("machina_learning")
        fake_learning.lookup_distilled = lambda _text: ("search", 0.95)
        with patch.dict(sys.modules, {"machina_learning": fake_learning}):
            intent = cdu.resolve_intent_fast("아무말")
        self.assertEqual(intent.get("type"), "action")
        self.assertTrue(intent.get("actions"))
        action = intent["actions"][0]
        self.assertEqual(action.get("kind"), "tool")
        self.assertEqual(action.get("aid"), "AID.NET.WEB_SEARCH.v1")


class MCPEnvResolutionTests(unittest.TestCase):
    def test_resolve_env_refs_nested_structure(self):
        os.environ["Z_AI_API_KEY"] = "secret-token-123"
        payload = {
            "headers": {"Authorization": "Bearer ${Z_AI_API_KEY}"},
            "env": {"Z_AI_API_KEY": "${Z_AI_API_KEY}"},
            "args": ["-x", "${Z_AI_API_KEY}"],
        }
        resolved = _resolve_env_refs(payload)
        self.assertEqual(
            resolved["headers"]["Authorization"],
            "Bearer secret-token-123",
        )
        self.assertEqual(resolved["env"]["Z_AI_API_KEY"], "secret-token-123")
        self.assertEqual(resolved["args"][1], "secret-token-123")

    def test_mcp_connection_initialization_applies_env_refs(self):
        os.environ["Z_AI_API_KEY"] = "abcxyz"
        cfg = {
            "transport": "streamable_http",
            "url": "https://example.invalid/mcp",
            "headers": {"Authorization": "Bearer ${Z_AI_API_KEY}"},
            "env": {"Z_AI_API_KEY": "${Z_AI_API_KEY}"},
        }
        conn = MCPServerConnection("zai", cfg)
        self.assertEqual(
            conn.config["headers"]["Authorization"],
            "Bearer abcxyz",
        )
        self.assertEqual(conn.config["env"]["Z_AI_API_KEY"], "abcxyz")

    def test_mcp_argument_normalization_search_query_alias(self):
        conn = MCPServerConnection("web_search", {"transport": "streamable_http", "url": "https://example.invalid/mcp"})
        conn.tools = {
            "webSearchPrime": {
                "description": "",
                "inputSchema": {
                    "type": "object",
                    "required": ["search_query"],
                    "properties": {"search_query": {"type": "string"}},
                },
            }
        }
        out = conn._normalize_tool_arguments("webSearchPrime", {"query": "ai news"})
        self.assertEqual(out.get("search_query"), "ai news")

    def test_mcp_argument_normalization_url_alias(self):
        conn = MCPServerConnection("web_reader", {"transport": "streamable_http", "url": "https://example.invalid/mcp"})
        conn.tools = {
            "webReader": {
                "description": "",
                "inputSchema": {
                    "type": "object",
                    "required": ["url"],
                    "properties": {"url": {"type": "string"}},
                },
            }
        }
        out = conn._normalize_tool_arguments("webReader", {"link": "https://example.com"})
        self.assertEqual(out.get("url"), "https://example.com")


class AliasNormalizationTests(unittest.TestCase):
    def test_legacy_aid_normalization(self):
        self.assertEqual(resolve_alias("AID.GPU.SMOKE.v1"), "AID.GPU_SMOKE.v1")
        self.assertEqual(resolve_alias("AID.GPU.METRICS.v1"), "AID.GPU_METRICS.v1")
        self.assertEqual(resolve_alias("AID.NET.SEARCH.v1"), "AID.NET.WEB_SEARCH.v1")
        self.assertEqual(resolve_alias("AID.GENESIS.RUN.v1"), "AID.GENESIS.WRITE_FILE.v1")


if __name__ == "__main__":
    unittest.main()
