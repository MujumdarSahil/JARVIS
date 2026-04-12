"""
Clipboard read/write using pyperclip.
"""

from __future__ import annotations

from typing import Any

import pyperclip

from utils.logger import get_logger

logger = get_logger(__name__)


def _ok(success: bool, result: str = "", error: str | None = None) -> dict[str, Any]:
    return {"success": success, "result": result, "error": error}


class ClipboardSkill:
    """Clipboard operations."""

    def read(self) -> dict[str, Any]:
        try:
            text = pyperclip.paste()
            if text is None:
                return _ok(True, "", None)
            return _ok(True, str(text), None)
        except Exception as e:
            logger.exception("clipboard.read failed: %s", e)
            return _ok(False, "", str(e))

    def write(self, text: str) -> dict[str, Any]:
        try:
            pyperclip.copy(text or "")
            return _ok(True, "Clipboard updated", None)
        except Exception as e:
            logger.exception("clipboard.write failed: %s", e)
            return _ok(False, "", str(e))

    def append(self, text: str) -> dict[str, Any]:
        try:
            cur = pyperclip.paste()
            cur_s = "" if cur is None else str(cur)
            pyperclip.copy(cur_s + (text or ""))
            return _ok(True, "Clipboard appended", None)
        except Exception as e:
            logger.exception("clipboard.append failed: %s", e)
            return _ok(False, "", str(e))

    def clear(self) -> dict[str, Any]:
        try:
            pyperclip.copy("")
            return _ok(True, "Clipboard cleared", None)
        except Exception as e:
            logger.exception("clipboard.clear failed: %s", e)
            return _ok(False, "", str(e))


if __name__ == "__main__":
    c = ClipboardSkill()
    print(c.write("test"))
    print(c.read())
