# SELF-AWARE SYSTEM
"""Natural-language autonomous task/monitor agent."""

from __future__ import annotations

import re
from typing import Any

from core.agents.base_agent import BaseAgent


class AutonomousAgent(BaseAgent):
    def __init__(self, brain: Any, db: Any, config: dict[str, Any], scheduler: Any, monitor: Any, reporter: Any) -> None:
        super().__init__("autonomous", brain, db, config)
        self.scheduler = scheduler
        self.monitor = monitor
        self.reporter = reporter

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        action = str(task.get("action") or task.get("type") or "").lower()
        p = task.get("params") or {}
        if action == "schedule":
            name = str(p.get("name") or "scheduled_task")
            act = str(p.get("task_action") or "direct")
            params = p.get("params") if isinstance(p.get("params"), dict) else {}
            sch = str(p.get("schedule") or p.get("schedule_str") or "every hour")
            tid = self.scheduler.add_task(name, act, params, sch)
            return {"success": True, "result": {"task_id": tid}}
        if action == "monitor":
            mid = self.monitor.add_monitor(str(p.get("name") or "monitor"), str(p.get("condition_type") or "cpu_threshold"), p.get("condition_params") or {}, str(p.get("task_action") or "notify"), p.get("action_params") or {})
            return {"success": True, "result": {"monitor_id": mid}}
        if action == "list_tasks":
            return {"success": True, "result": self.scheduler.list_tasks()}
        if action == "list_monitors":
            return {"success": True, "result": self.monitor.list_monitors()}
        if action == "daily_summary":
            return {"success": True, "result": self.reporter.get_daily_summary()}
        if action == "morning_brief":
            return {"success": True, "result": self.reporter.generate_morning_brief()}
        if action == "cancel":
            name = str(p.get("name") or "")
            tasks = self.scheduler.list_tasks()
            for t in tasks:
                if str(t.get("name", "")).lower() == name.lower():
                    return {"success": self.scheduler.remove_task(str(t.get("task_id"))), "result": "task removed"}
            mons = self.monitor.list_monitors()
            for m in mons:
                if str(m.get("name", "")).lower() == name.lower():
                    self.monitor.remove_monitor(str(m.get("monitor_id")))
                    return {"success": True, "result": "monitor removed"}
            return {"success": False, "result": "not found"}
        return {"success": False, "result": f"Unknown action: {action}"}

    def parse_natural_language_schedule(self, text: str) -> dict[str, Any]:
        low = (text or "").lower()
        if re.search(r"every\s+hour", low):
            return {"schedule_str": "every 1 hour", "parsed_time": None, "recurring": True}
        m = re.search(r"every\s+(\d+)\s+minutes?", low)
        if m:
            return {"schedule_str": f"every {m.group(1)} minutes", "parsed_time": None, "recurring": True}
        m = re.search(r"every\s+weekday\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", low)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2) or 0)
            ap = m.group(3)
            if ap == "pm" and hh < 12:
                hh += 12
            return {"schedule_str": f"every monday at {hh:02d}:{mm:02d}", "parsed_time": f"{hh:02d}:{mm:02d}", "recurring": True}
        m = re.search(r"every\s+morning\s+at\s+(\d{1,2})(?::(\d{2}))?", low)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2) or 0)
            return {"schedule_str": f"every day at {hh:02d}:{mm:02d}", "parsed_time": f"{hh:02d}:{mm:02d}", "recurring": True}
        return {"schedule_str": "every 1 hour", "parsed_time": None, "recurring": True}

    def parse_natural_language_condition(self, text: str) -> dict[str, Any]:
        low = (text or "").lower()
        if "cpu" in low and ("high" in low or "too much" in low):
            return {"condition_type": "cpu_threshold", "condition_params": {"threshold": 90, "duration_minutes": 5}}
        if "file" in low and ("download" in low or "appears" in low):
            return {"condition_type": "file_created", "condition_params": {"path": "C:/Users/*/Downloads/*"}}
        m = re.search(r"at\s+(\d{1,2}):(\d{2})", low)
        if m:
            return {"condition_type": "time_reached", "condition_params": {"time": f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", "days": ["mon", "tue", "wed", "thu", "fri"]}}
        return {"condition_type": "keyword_in_clipboard", "condition_params": {"keyword": "meeting"}}


if __name__ == "__main__":
    print("AutonomousAgent requires runtime dependencies.")
