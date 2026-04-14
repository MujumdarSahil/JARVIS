"""
Coding specialist: generate, debug, explain, review, convert, run (Python).
"""

from __future__ import annotations

import subprocess
import sys
import time
from typing import TYPE_CHECKING, Any

from core.agents.base_agent import BaseAgent
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database

logger = get_logger(__name__)

_CODE_SYSTEM = (
    "You are an expert software engineer. Return ONLY code when asked to generate or fix. "
    "Be precise and production-quality."
)


class CoderAgent(BaseAgent):
    """Code generation, debugging, explanation, execution."""

    def __init__(
        self,
        name: str,
        brain: Brain,
        db: Database,
        config: dict[str, Any],
        code_assistant: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, brain, db, config, kwargs.get("event_bus"))
        self._code = code_assistant

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        return self._run_wrapped(task, lambda: self._execute_impl(task))

    def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        action = str(params.get("action") or "generate").lower()
        lang = str(params.get("language") or self.config.get("default_language") or "python")

        if action == "generate":
            desc = str(params.get("description") or task.get("description") or "")
            ctx = str(params.get("context") or "")
            return self.generate(desc, lang, ctx)
        if action == "debug":
            return self.debug(str(params.get("code") or ""), str(params.get("error") or ""), lang)
        if action == "explain":
            return self.explain_step_by_step(str(params.get("code") or ""))
        if action == "review":
            out = self._code.review(str(params.get("code") or ""))
            self._maybe_save_memory(out.get("result") or "", lang, "review")
            return {"success": bool(out.get("success")), "result": out.get("result") or out.get("error")}
        if action == "convert":
            out = self._code.convert(
                str(params.get("code") or ""),
                str(params.get("from_lang") or "python"),
                str(params.get("to_lang") or lang),
            )
            self._maybe_save_memory(out.get("result") or "", str(params.get("to_lang") or lang), "convert")
            return {"success": bool(out.get("success")), "result": out.get("result") or out.get("error")}
        if action == "run":
            return self.run_safely(str(params.get("code") or ""), int(self.config.get("execution_timeout") or 15))
        return self.generate(str(task.get("description") or ""), lang, "")

    def _maybe_save_memory(self, code: str, language: str, tag_extra: str) -> None:
        if not code.strip() or not self.db.available:
            return
        try:
            self.db.insert(
                "memories",
                {
                    "user_id": "default",
                    "content": code[:20000],
                    "tags": ["code", language, tag_extra],
                    "importance_score": 5,
                    "timestamp": time.time(),
                    "source": "code_agent",
                },
            )
        except Exception as e:
            logger.debug("save code memory: %s", e)

    def generate(self, description: str, language: str, context: str = "") -> dict[str, Any]:
        user = f"Task: {description}\nLanguage: {language}\nContext:\n{context}\nReturn ONLY raw code, no markdown."
        text = self.brain.chat(
            [
                {"role": "system", "content": _CODE_SYSTEM},
                {"role": "user", "content": user},
            ]
        )
        self._maybe_save_memory(text, language, "generate")
        return {"success": True, "result": text}

    def debug(self, code: str, error: str, language: str) -> dict[str, Any]:
        user = (
            f"Analyze the error, fix the code. Return ONLY the corrected full source first, "
            f"then a blank line, then a short explanation.\n\nCode:\n{code}\n\nError:\n{error}\n"
            f"Language: {language}"
        )
        text = self.brain.chat(
            [{"role": "system", "content": _CODE_SYSTEM}, {"role": "user", "content": user}]
        )
        self._maybe_save_memory(text, language, "debug")
        return {"success": True, "result": text}

    def run_safely(self, code: str, timeout: int = 15) -> dict[str, Any]:
        timeout = min(max(1, int(timeout)), 15)
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            merged = "\n".join(x for x in (out, err) if x)
            ok = proc.returncode == 0
            return {
                "success": ok,
                "result": merged or f"(exit {proc.returncode})",
                "stderr": err,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "result": "", "error": f"timeout after {timeout}s"}
        except Exception as e:
            return {"success": False, "result": "", "error": str(e)}

    def explain_step_by_step(self, code: str) -> dict[str, Any]:
        user = (
            "Explain this code line by line as a numbered list (1. 2. 3.). "
            "Each item should be one line of code or logical line and its meaning.\n\n"
            f"{code}"
        )
        text = self.brain.chat(
            [
                {"role": "system", "content": "You are a patient programming teacher."},
                {"role": "user", "content": user},
            ]
        )
        return {"success": True, "result": text}


if __name__ == "__main__":
    print("CoderAgent module OK")
