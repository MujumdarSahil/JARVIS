from __future__ import annotations

import threading
import time
import uuid
import logging
from typing import Any

import schedule

logger = logging.getLogger(__name__)


class TaskScheduler:
    def __init__(self, db: Any, registry: Any, notifier: Any, reporter: Any | None = None, config: dict[str, Any] | None = None) -> None:
        self.db = db
        self.registry = registry
        self.notifier = notifier
        self.reporter = reporter
        self.config = config or {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._jobs: dict[str, Any] = {}
        self._tasks_memory: list = []
        self._load_tasks()
        self.seed_default_tasks()

    def _parse_and_schedule(self, task_id: str, schedule_str: str, fn) -> None:
        s = (schedule_str or "").lower().strip()
        job = None
        if "every day at" in s:
            job = schedule.every().day.at(s.split("every day at", 1)[1].strip()).do(fn)
        elif "every monday at" in s:
            job = schedule.every().monday.at(s.split("every monday at", 1)[1].strip()).do(fn)
        elif "every " in s and " hour" in s:
            n = int("".join(ch for ch in s if ch.isdigit()) or "1")
            job = schedule.every(n).hours.do(fn)
        elif "every " in s and " minute" in s:
            n = int("".join(ch for ch in s if ch.isdigit()) or "1")
            job = schedule.every(n).minutes.do(fn)
        elif s == "every hour":
            job = schedule.every().hour.do(fn)
        if job is not None:
            self._jobs[task_id] = job

    def _load_tasks(self) -> None:
        if not getattr(self.db, "available", False):
            return
        rows = self.db.find("scheduled_tasks", {"enabled": True}, limit=200)
        for task in rows:
            tid = str(task.get("task_id"))
            self._parse_and_schedule(tid, str(task.get("cron_expression") or ""), lambda t=task: self._execute_task(t))

    def seed_default_tasks(self):
        existing_names = [t["name"] for t in self.list_tasks()]
        defaults = [
            {
                "name": "morning_brief",
                "action": "brief",
                "params": {},
                "schedule_str": f"every day at {self.config.get('brief_time', '08:00')}",
                "enabled": self.config.get("morning_brief", True)
            },
            {
                "name": "weekly_reflection",
                "action": "reflect",
                "params": {},
                "schedule_str": f"every {self.config.get('reflection_day', 'monday')} at 07:00",
                "enabled": self.config.get("weekly_reflection", True)
            },
            {
                "name": "memory_cleanup",
                "action": "cleanup_memories",
                "params": {"min_importance": 3, "older_than_days": 30},
                "schedule_str": "every day at 03:00",
                "enabled": True
            }
        ]
        for task in defaults:
            if task["name"] not in existing_names:
                self.add_task(task["name"], task["action"], task["params"], task["schedule_str"])
                logger.info(f"Seeded default task: {task['name']}")

    def add_task(self, name: str, action: str, params: dict, schedule_str: str) -> str:
        tid = uuid.uuid4().hex[:12]
        task = {
            "task_id": tid,
            "name": name,
            "cron_expression": schedule_str,
            "action": action,
            "params": params or {},
            "enabled": True,
            "last_run": None,
            "next_run": None,
            "run_count": 0,
        }
        self._parse_and_schedule(tid, schedule_str, lambda t=task: self._execute_task(t))
        if getattr(self.db, "available", False):
            self.db.insert("scheduled_tasks", task)
        else:
            self._tasks_memory.append(task)
        return tid

    def remove_task(self, task_id: str) -> bool:
        self._jobs.pop(task_id, None)
        self._tasks_memory = [t for t in self._tasks_memory if t.get("task_id") != task_id]
        if getattr(self.db, "available", False):
            return self.db.delete("scheduled_tasks", {"task_id": task_id}) > 0
        return True

    def pause_task(self, task_id: str) -> bool:
        for t in self._tasks_memory:
            if t.get("task_id") == task_id:
                t["enabled"] = False
        return bool(getattr(self.db, "available", False) and self.db.update("scheduled_tasks", {"task_id": task_id}, {"$set": {"enabled": False}}))

    def resume_task(self, task_id: str) -> bool:
        for t in self._tasks_memory:
            if t.get("task_id") == task_id:
                t["enabled"] = True
        return bool(getattr(self.db, "available", False) and self.db.update("scheduled_tasks", {"task_id": task_id}, {"$set": {"enabled": True}}))

    def list_tasks(self) -> list[dict[str, Any]]:
        if not getattr(self.db, "available", False):
            return self._tasks_memory
        return self.db.find("scheduled_tasks", {}, limit=200, sort=[("name", 1)])

    def run_task_now(self, task_id: str) -> dict[str, Any]:
        if not getattr(self.db, "available", False):
            for t in self._tasks_memory:
                if t.get("task_id") == task_id:
                    return self._execute_task(t)
            return {"success": False, "result": "task not found"}
        row = self.db.find("scheduled_tasks", {"task_id": task_id}, limit=1)
        if not row:
            return {"success": False, "result": "task not found"}
        return self._execute_task(row[0])

    def _execute_task(self, task: dict) -> dict[str, Any]:
        action = str(task.get("action") or "")
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        result: Any = {"ok": True}
        try:
            if action in ("self_reflection", "reflect") and getattr(self.registry, "self_improvement_agent", None):
                result = self.registry.self_improvement_agent.run_reflection(str(params.get("period") or "weekly"))
            elif action in ("morning_brief", "brief") and getattr(self.registry, "autonomy_reporter", None):
                result = self.registry.autonomy_reporter.generate_morning_brief()
            elif action in ("memory_cleanup", "cleanup_memories"):
                if getattr(self.db, "available", False):
                    cutoff = time.time() - 30 * 24 * 3600
                    deleted = self.db.delete("memories", {"importance_score": {"$lt": 3}, "timestamp": {"$lt": cutoff}})
                    result = {"deleted": deleted}
                else:
                    result = {"deleted": 0, "reason": "db unavailable"}
            else:
                result = {"skipped": f"unknown action {action}"}
        except Exception as exc:
            result = {"error": str(exc)}
        if getattr(self.db, "available", False):
            self.db.update("scheduled_tasks", {"task_id": task.get("task_id")}, {"$set": {"last_run": time.time()}, "$inc": {"run_count": 1}})
            self.db.insert("scheduled_task_runs", {"task_id": task.get("task_id"), "timestamp": time.time(), "result": result})
        if self.reporter is not None:
            self.reporter.log_autonomous_action(str(task.get("name")), "scheduler", str(result), str(task.get("task_id")))
        return {"success": True, "result": result}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="jarvis-scheduler")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(timeout=30.0):
            schedule.run_pending()

    def stop(self) -> None:
        self._stop.set()


if __name__ == "__main__":
    print("TaskScheduler requires runtime dependencies.")

