#!/usr/bin/env python3
"""Tests for MCP plan-step safety and sample arg generation."""

import importlib
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@contextmanager
def _isolated_modules(names: set[str]):
    snapshot = {n: sys.modules.get(n) for n in names}
    try:
        yield
    finally:
        for n, old in snapshot.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def import_handlers_with_stubs():
    if "telegram_bot_handlers" in sys.modules:
        return sys.modules["telegram_bot_handlers"]

    telegram = types.ModuleType("telegram")
    telegram.Update = type("Update", (), {})
    telegram.InlineKeyboardButton = type("InlineKeyboardButton", (), {})
    telegram.InlineKeyboardMarkup = type("InlineKeyboardMarkup", (), {})
    sys.modules["telegram"] = telegram

    telegram_ext = types.ModuleType("telegram.ext")
    telegram_ext.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})
    sys.modules["telegram.ext"] = telegram_ext

    return importlib.import_module("telegram_bot_handlers")


class MCPPlanSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._mods = {"telegram", "telegram.ext", "telegram_bot_handlers"}
        cls._iso = _isolated_modules(cls._mods)
        cls._iso.__enter__()
        cls.handlers = import_handlers_with_stubs()

    @classmethod
    def tearDownClass(cls):
        cls._iso.__exit__(None, None, None)

    def test_build_mcp_sample_args_prefers_search_and_url_shapes(self):
        schema = {
            "type": "object",
            "required": ["search_query", "url"],
            "properties": {
                "search_query": {"type": "string"},
                "url": {"type": "string"},
                "lang": {"type": "string"},
            },
        }
        args = self.handlers._build_mcp_sample_args(schema)
        self.assertIn("search_query", args)
        self.assertIn("url", args)
        self.assertEqual(args["search_query"], "latest technology news 2026")
        self.assertTrue(args["url"].startswith("https://"))

    def test_step_to_intent_coerces_top_level_mcp_query_into_args(self):
        step = {
            "tool": "mcp",
            "mcp_server": "web_search",
            "mcp_tool": "websearchprime",
            "query": "agentic ai 2026",
            "desc": "MCP web search",
        }
        intent = self.handlers._step_to_intent(step)
        self.assertEqual(intent.get("type"), "action")
        self.assertTrue(intent.get("actions"))
        action = intent["actions"][0]
        self.assertEqual(action.get("aid"), "AID.MCP.WEB_SEARCH.WEBSEARCHPRIME.v1")
        self.assertEqual(action.get("inputs", {}).get("query"), "agentic ai 2026")


if __name__ == "__main__":
    unittest.main()
