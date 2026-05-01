# SELF-AWARE SYSTEM
"""Autonomous activity reporting."""

from __future__ import annotations

import time
from typing import Any


class ActivityReporter:
    def __init__(self, db: Any, notifier: Any | None = None) -> None:
        self.db = db
        self.notifier = notifier

    def log_autonomous_action(self, action: str, trigger: str, result: str, task_id: str | None = None) -> None:
        if not getattr(self.db, "available", False):
            pass
        else:
            self.db.insert(
                "autonomous_log",
                {"timestamp": time.time(), "action": action, "trigger": trigger, "result": result, "task_id": task_id},
            )
        try:
            if self.notifier is not None and getattr(self.notifier, "socketio", None) is not None:
                self.notifier.socketio.emit(
                    "autonomous_action",
                    {"action": action, "trigger": trigger, "result": result, "task_id": task_id, "timestamp": time.time()},
                )
        except Exception:
            pass

    def get_activity_feed(self, hours: int = 24) -> list[dict[str, Any]]:
        if not getattr(self.db, "available", False):
            return []
        since = time.time() - max(1, hours) * 3600
        return self.db.find("autonomous_log", {"timestamp": {"$gte": since}}, limit=200, sort=[("timestamp", 1)])

    def get_daily_summary(self) -> str:
        feed = self.get_activity_feed(24)
        if not feed:
            return "Today I did not run any autonomous actions."
        n_tasks = len([f for f in feed if "schedule" in str(f.get("action", ""))])
        n_mon = len([f for f in feed if "monitor" in str(f.get("action", "")) or "trigger" in str(f.get("trigger", ""))])
        return f"Today I ran {n_tasks} scheduled tasks, detected {n_mon} monitor events, and logged {len(feed)} autonomous actions."

    def generate_morning_brief(self, weather_enabled: bool = False) -> str:
        actions = self.get_activity_feed(12)
        pending = self.db.find("scheduled_tasks", {"enabled": True}, limit=20, sort=[("next_run", 1)]) if getattr(self.db, "available", False) else []
        important = self.db.find("memories", {"importance_score": {"$gte": 8}}, limit=5, sort=[("timestamp", -1)]) if getattr(self.db, "available", False) else []
        triggered = self.db.find("monitor_logs", {}, limit=5, sort=[("timestamp", -1)]) if getattr(self.db, "available", False) else []
        return (
            "Good morning. Overnight summary: "
            f"{len(actions)} autonomous actions, {len(triggered)} monitor triggers, "
            f"{len(pending)} scheduled tasks for today, and {len(important)} high-importance memories."
        )


if __name__ == "__main__":
    print("ActivityReporter requires db.")
