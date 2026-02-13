#!/usr/bin/env python3
"""Pulse/handler guard tests without requiring python-telegram-bot runtime."""

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


class PulseGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._mods = {"telegram", "telegram.ext", "telegram_bot_handlers"}
        cls._iso = _isolated_modules(cls._mods)
        cls._iso.__enter__()
        cls.handlers = import_handlers_with_stubs()

    @classmethod
    def tearDownClass(cls):
        cls._iso.__exit__(None, None, None)

    def test_multi_step_detection(self):
        self.assertTrue(self.handlers._is_multi_step_request("모든 도구를 순서대로 실행해줘"))
        self.assertFalse(self.handlers._is_multi_step_request("안녕"))

    def test_all_tools_detection(self):
        self.assertTrue(self.handlers._is_all_tools_request("모든 도구 다 사용해봐"))
        self.assertFalse(self.handlers._is_all_tools_request("파일 읽어줘"))

    def test_validate_continuation_actions(self):
        valid_shell = [{"aid": "AID.SHELL.EXEC.v1", "inputs": {"cmd": "echo hi"}}]
        invalid_shell = [{"aid": "AID.SHELL.EXEC.v1", "inputs": {}}]
        invalid_shell_lower = [{"aid": "aid.shell.exec.v1", "inputs": {"cmd": "   "}}]
        invalid_shell_list = [{"aid": "AID.SHELL.EXEC.v1", "inputs": {"cmd": ["", "  "]}}]
        valid_code = [{"aid": "AID.CODE.EXEC.v1", "inputs": {"code": "print(1)"}}]
        invalid_code = [{"aid": "AID.CODE.EXEC.v1", "inputs": {}}]
        self.assertTrue(self.handlers._validate_continuation_actions(valid_shell))
        self.assertTrue(self.handlers._validate_continuation_actions(valid_code))
        self.assertFalse(self.handlers._validate_continuation_actions(invalid_shell))
        self.assertFalse(self.handlers._validate_continuation_actions(invalid_shell_lower))
        self.assertFalse(self.handlers._validate_continuation_actions(invalid_shell_list))
        self.assertFalse(self.handlers._validate_continuation_actions(invalid_code))


if __name__ == "__main__":
    unittest.main()
