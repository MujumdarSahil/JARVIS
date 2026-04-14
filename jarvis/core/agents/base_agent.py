"""
Base class for Jarvis sub-agents (shared Brain + Database).
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable

from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database

logger = get_logger(__name__)

EventBus = Callable[[dict[str, Any]], None] | None


class BaseAgent(ABC):
    """All specialized agents inherit from this."""

    def __init__(
        self,
        name: str,
        brain: Brain,
        db: Database,
        config: dict[str, Any],
        event_bus: EventBus = None,
    ) -> None:
        self.name = name
        self.brain = brain
        self.db = db
        self.config = dict(config or {})
        self.status: str = "idle"
        self.current_task: str = ""
        self.tasks_completed: int = 0
        self._event_bus: EventBus = event_bus

    @abstractmethod
    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        Task shape: {id, type, description, params, context}.
        Returns {success, result, agent, task_id, duration_ms}.
        """

    def _default_system_prompt(self) -> str:
        return (
            f"You are the Jarvis sub-agent '{self.name}'. "
            f"Follow instructions precisely; stay in role; be concise and accurate."
        )

    def think(self, prompt: str, system: str | None = None) -> str:
        sys_msg = system if system is not None else self._default_system_prompt()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ]
        return self.brain.chat(messages)

    def log_task(self, task: dict[str, Any], result: dict[str, Any]) -> None:
        """Persist agent run to ``agent_logs`` (MongoDB when available)."""
        try:
            summary = ""
            if isinstance(result.get("result"), str):
                summary = (result.get("result") or "")[:2000]
            elif result.get("result") is not None:
                summary = str(result.get("result"))[:2000]
            doc = {
                "agent_name": self.name,
                "task_id": str(task.get("id") or ""),
                "task_description": str(task.get("description") or ""),
                "started_at": result.get("started_at"),
                "finished_at": result.get("finished_at"),
                "duration_ms": int(result.get("duration_ms") or 0),
                "success": bool(result.get("success")),
                "result_summary": summary,
                "model_used": str(result.get("model_used") or self.brain.get_active_provider() or ""),
                "tokens_estimated": int(result.get("tokens_estimated") or 0),
                "timestamp": time.time(),
            }
            self.db.insert("agent_logs", doc)
        except Exception as e:
            logger.warning("log_task failed for %s: %s", self.name, e)

    def report_status(self, status: str, task: str = "") -> None:
        self.status = status
        self.current_task = task or ""
        if self._event_bus:
            try:
                self._event_bus(
                    {
                        "agent": self.name,
                        "status": self.status,
                        "current_task": self.current_task,
                    }
                )
            except Exception as e:
                logger.debug("event_bus emit failed: %s", e)

    def _run_wrapped(self, task: dict[str, Any], fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Set status, timing, tokens estimate, ensure idle on exit."""
        tid = str(task.get("id") or uuid.uuid4().hex)
        task = {**task, "id": tid}
        t0 = time.perf_counter()
        started = time.time()
        self.report_status("running", str(task.get("description") or ""))
        model_used = ""
        tokens_estimated = 0
        try:
            out = fn()
            if not isinstance(out, dict):
                out = {"success": False, "result": str(out)}
            out.setdefault("success", True)
            out.setdefault("result", "")
            out["agent"] = self.name
            out["task_id"] = tid
            dt_ms = int((time.perf_counter() - t0) * 1000)
            out["duration_ms"] = out.get("duration_ms", dt_ms)
            finished = time.time()
            model_used = self.brain.get_active_provider() or ""
            body = str(task.get("description") or "") + str(out.get("result") or "")
            tokens_estimated = self.brain.estimate_tokens(body)
            self.tasks_completed += 1
            self.report_status("idle", "")
            log_result = {
                **out,
                "started_at": started,
                "finished_at": finished,
                "model_used": model_used,
                "tokens_estimated": tokens_estimated,
            }
            self.log_task(task, log_result)
            return out
        except Exception as e:
            logger.exception("%s.execute failed: %s", self.name, e)
            finished = time.time()
            dt_ms = int((time.perf_counter() - t0) * 1000)
            model_used = self.brain.get_active_provider() or ""
            tokens_estimated = self.brain.estimate_tokens(str(e))
            err_out: dict[str, Any] = {
                "success": False,
                "result": str(e),
                "agent": self.name,
                "task_id": tid,
                "duration_ms": dt_ms,
            }
            self.report_status("error", str(task.get("description") or ""))
            self.log_task(
                task,
                {
                    **err_out,
                    "started_at": started,
                    "finished_at": finished,
                    "model_used": model_used,
                    "tokens_estimated": tokens_estimated,
                },
            )
            self.report_status("idle", "")
            return err_out


if __name__ == "__main__":
    print("BaseAgent module OK")
