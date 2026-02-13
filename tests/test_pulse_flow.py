#!/usr/bin/env python3
"""Async flow tests for telegram_bot_pulse helper functions."""

import asyncio
import importlib
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_module(name: str, attrs: dict):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


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


def import_pulse_with_stubs():
    if "telegram_bot_pulse" in sys.modules:
        return sys.modules["telegram_bot_pulse"]

    # telegram stubs
    _stub_module("telegram", {"Update": type("Update", (), {})})
    _stub_module("telegram.constants", {"ChatAction": type("ChatAction", (), {"TYPING": "typing"})})
    _stub_module("telegram.ext", {"ContextTypes": type("ContextTypes", (), {"DEFAULT_TYPE": object})})

    # minimal runtime stubs needed by telegram_bot_pulse imports
    _stub_module(
        "machina_shared",
        {
            "_jsonl_append": lambda *a, **k: None,
            "MEM_DIR": ROOT / "work" / "memory",
            "get_active_backend": lambda: "oai_compat",
            "get_brain_label": lambda: "Ollama(test)",
            "is_auto_route_enabled": lambda: False,
        },
    )
    _stub_module(
        "machina_learning",
        {
            "experience_record": lambda *a, **k: None,
            "skill_search": lambda *a, **k: "",
            "wisdom_retrieve": lambda *a, **k: "",
            "memory_search_recent": lambda *a, **k: "",
        },
    )
    _stub_module("machina_dispatch", {"execute_intent": lambda *a, **k: ""})
    _stub_module(
        "policies.chat_driver",
        {
            "track_dialogue_state": lambda h, s=None: {"topic": "", "entities": [], "intent_chain": [], "turn_count": 0},
            "extract_entities": lambda t: {"files": [], "urls": [], "numbers": [], "names": []},
        },
    )
    _stub_module("policies.chat_driver_util", {"resolve_intent_fast": lambda t: {}})

    def _step_to_intent(step):
        tool = step.get("tool", "")
        if tool == "shell":
            return {
                "type": "action",
                "actions": [{"kind": "tool", "aid": "AID.SHELL.EXEC.v1", "inputs": {"cmd": step.get("cmd", "")}}],
            }
        return None

    _stub_module(
        "telegram_bot_handlers",
        {
            "_compute_complexity": lambda *a, **k: 0.0,
            "_detect_memorable_facts": lambda *a, **k: [],
            "_check_action_permissions": lambda *a, **k: [],
            "_is_multi_step_request": lambda *_: False,
            "_is_all_tools_request": lambda *_: False,
            "_build_all_tools_plan": lambda *_: [],
            "_step_to_intent": _step_to_intent,
            "_handle_blocked_code_approval": lambda *a, **k: "",
            "_coerce_response": lambda x: str(x) if x is not None else "",
            "_extract_embedded_action": lambda *_: (None, ""),
            "_unwrap_json_response": lambda x: x,
            "_validate_continuation_actions": lambda *_: True,
        },
    )

    return importlib.import_module("telegram_bot_pulse")


class _FakeMsg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text: str):
        self.sent.append(text)


class _FakeUpdate:
    def __init__(self):
        self.message = _FakeMsg()


class _FakeBot:
    def __init__(self):
        self.chunked = []

    async def send_chunked(self, _update, text: str):
        self.chunked.append(text)


class PulseFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._mods = {
            "telegram",
            "telegram.constants",
            "telegram.ext",
            "machina_shared",
            "machina_learning",
            "machina_dispatch",
            "policies.chat_driver",
            "policies.chat_driver_util",
            "telegram_bot_handlers",
            "telegram_bot_pulse",
        }
        cls._iso = _isolated_modules(cls._mods)
        cls._iso.__enter__()
        cls.pulse = import_pulse_with_stubs()

    @classmethod
    def tearDownClass(cls):
        cls._iso.__exit__(None, None, None)

    def test_advance_step_queue_happy_path(self):
        async def _run():
            step_queue = [{"tool": "shell", "cmd": "echo hi", "desc": "run shell"}]
            all_cycle_results = ["prev"]
            update = _FakeUpdate()
            bot = _FakeBot()
            next_intent, last_sent = await self.pulse._advance_step_queue(
                step_queue, all_cycle_results, "cycle-output", None, update, bot, 1
            )
            self.assertTrue(next_intent)
            self.assertEqual(next_intent["type"], "action")
            self.assertEqual(last_sent, "cycle-output")
            self.assertIn("cycle-output", bot.chunked)
            self.assertTrue(update.message.sent)

        asyncio.run(_run())

    def test_advance_step_queue_invalid_step(self):
        async def _run():
            step_queue = [{"tool": "unknown", "desc": "bad"}]
            update = _FakeUpdate()
            bot = _FakeBot()
            next_intent, last_sent = await self.pulse._advance_step_queue(
                step_queue, [], "x", "prev", update, bot, 1
            )
            self.assertIsNone(next_intent)
            self.assertEqual(last_sent, "prev")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
