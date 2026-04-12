"""
Code generation and analysis via the shared Brain (no separate LLM client).
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Any

from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain

logger = get_logger(__name__)


def _ok(
    success: bool,
    result: str,
    language: str,
    error: str | None = None,
) -> dict[str, Any]:
    return {"success": success, "result": result, "language": language, "error": error}


class CodeAssistant:
    """Specialized prompts that delegate to ``Brain.chat``."""

    def __init__(self, brain: Brain) -> None:
        self._brain = brain

    def generate(self, description: str, language: str = "python") -> dict[str, Any]:
        try:
            user = (
                f"Task: {description}\n"
                f"Target language: {language}\n"
                "Return ONLY the source code with no markdown fences, no prose, and no explanations."
            )
            text = self._brain.chat(
                [
                    {
                        "role": "system",
                        "content": "You are an expert programmer. Output only raw code, never markdown.",
                    },
                    {"role": "user", "content": user},
                ]
            )
            return _ok(True, text, language, None)
        except Exception as e:
            logger.exception("CodeAssistant.generate failed: %s", e)
            return _ok(False, "", language, str(e))

    def explain(self, code: str) -> dict[str, Any]:
        try:
            user = f"Explain the following code in plain English:\n\n{code}"
            text = self._brain.chat(
                [
                    {"role": "system", "content": "You are a patient teacher. Be concise and accurate."},
                    {"role": "user", "content": user},
                ]
            )
            return _ok(True, text, "plain", None)
        except Exception as e:
            logger.exception("CodeAssistant.explain failed: %s", e)
            return _ok(False, "", "plain", str(e))

    def debug(self, code: str, error: str = "") -> dict[str, Any]:
        try:
            user = (
                "Find bugs in this code. Return ONLY the corrected full source code first, "
                "then a blank line, then a short explanation of what was wrong.\n\n"
                f"Code:\n{code}\n\nError or context:\n{error or '(none)'}\n"
                "Do not wrap the code in markdown fences."
            )
            text = self._brain.chat(
                [
                    {"role": "system", "content": "You are a debugging assistant. Follow the output format exactly."},
                    {"role": "user", "content": user},
                ]
            )
            return _ok(True, text, "python", None)
        except Exception as e:
            logger.exception("CodeAssistant.debug failed: %s", e)
            return _ok(False, "", "python", str(e))

    def review(self, code: str) -> dict[str, Any]:
        try:
            user = f"Perform a concise code review with actionable suggestions:\n\n{code}"
            text = self._brain.chat(
                [
                    {"role": "system", "content": "You are a senior engineer doing code review."},
                    {"role": "user", "content": user},
                ]
            )
            return _ok(True, text, "review", None)
        except Exception as e:
            logger.exception("CodeAssistant.review failed: %s", e)
            return _ok(False, "", "review", str(e))

    def convert(self, code: str, from_lang: str, to_lang: str) -> dict[str, Any]:
        try:
            user = (
                f"Convert the following {from_lang} code to {to_lang}. "
                "Return ONLY the converted code, no markdown fences.\n\n"
                f"{code}"
            )
            text = self._brain.chat(
                [
                    {"role": "system", "content": "You translate code between languages faithfully."},
                    {"role": "user", "content": user},
                ]
            )
            return _ok(True, text, to_lang, None)
        except Exception as e:
            logger.exception("CodeAssistant.convert failed: %s", e)
            return _ok(False, "", to_lang, str(e))

    def run_snippet(self, code: str, language: str = "python") -> dict[str, Any]:
        try:
            lang = (language or "python").strip().lower()
            if lang != "python":
                return _ok(False, "", lang, "execution not supported")
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=10,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            merged = "\n".join(x for x in (out, err) if x)
            ok = proc.returncode == 0
            return _ok(ok, merged or f"(exit {proc.returncode})", "python", None if ok else err or "non-zero exit")
        except subprocess.TimeoutExpired:
            return _ok(False, "", "python", "execution timed out (10s)")
        except Exception as e:
            logger.exception("CodeAssistant.run_snippet failed: %s", e)
            return _ok(False, "", "python", str(e))


if __name__ == "__main__":
    from core.brain import Brain

    ca = CodeAssistant(Brain())
    print(ca.run_snippet("print(2+2)"))
