# SELF-AWARE SYSTEM
"""Single-thread proactive condition monitor."""

from __future__ import annotations

import glob
import hashlib
import threading
import time
from typing import Any

import psutil
import requests


class ProactiveMonitor:
    def __init__(self, db: Any, registry: Any, notifier: Any, shell_skill: Any, reporter: Any | None = None) -> None:
        self.db = db
        self.registry = registry
        self.notifier = notifier
        self.shell_skill = shell_skill
        self.reporter = reporter
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state: dict[str, Any] = {}
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        if not getattr(self.db, "available", False) or self.db.count("monitors", {}) > 0:
            return
        self.add_monitor("high_cpu", "cpu_threshold", {"threshold": 90, "duration_minutes": 5}, "notify", {})
        self.add_monitor("low_disk", "low_disk", {"threshold_pct_free": 10}, "notify", {})

    def add_monitor(self, name: str, condition_type: str, condition_params: dict, action: str, action_params: dict) -> str:
        mid = hashlib.md5(f"{name}:{time.time()}".encode()).hexdigest()[:12]
        row = {
            "monitor_id": mid,
            "name": name,
            "condition_type": condition_type,
            "condition_params": condition_params or {},
            "action": action,
            "action_params": action_params or {},
            "enabled": True,
            "last_checked": None,
            "triggered_count": 0,
        }
        if getattr(self.db, "available", False):
            self.db.insert("monitors", row)
        return mid

    def remove_monitor(self, monitor_id: str):
        if getattr(self.db, "available", False):
            self.db.delete("monitors", {"monitor_id": monitor_id})

    def list_monitors(self) -> list[dict]:
        if not getattr(self.db, "available", False):
            return []
        return self.db.find("monitors", {}, limit=200, sort=[("name", 1)])

    def _cond_met(self, mon: dict) -> bool:
        ctype = mon.get("condition_type")
        p = mon.get("condition_params") or {}
        key = str(mon.get("monitor_id"))
        if ctype == "cpu_threshold":
            cpu = psutil.cpu_percent(interval=None)
            threshold = float(p.get("threshold", 90))
            dur = int(p.get("duration_minutes", 5)) * 60
            if cpu > threshold:
                first = self._state.get(key) or time.time()
                self._state[key] = first
                return (time.time() - first) >= dur
            self._state.pop(key, None)
            return False
        if ctype == "file_created":
            path = str(p.get("path") or "")
            matches = glob.glob(path)
            prev = set(self._state.get(key, []))
            cur = set(matches)
            self._state[key] = list(cur)
            return len(cur - prev) > 0
        if ctype == "time_reached":
            now = time.strftime("%H:%M")
            days = [d.lower() for d in (p.get("days") or [])]
            wd = time.strftime("%a").lower()[:3]
            return now == str(p.get("time") or "") and (not days or wd in days)
        if ctype == "keyword_in_clipboard":
            txt = self.registry.clipboard_skill.read().get("result", "")
            return str(p.get("keyword") or "").lower() in str(txt).lower()
        if ctype == "url_changed":
            url = str(p.get("url") or "")
            if not url:
                return False
            try:
                body = requests.get(url, timeout=10).text
            except Exception:
                return False
            h = hashlib.sha1(body.encode("utf-8", "ignore")).hexdigest()
            old = self._state.get(key)
            self._state[key] = h
            return old is not None and old != h
        if ctype == "low_disk":
            free_pct = 100.0 - psutil.disk_usage("/").percent
            return free_pct < float(p.get("threshold_pct_free", 10))
        return False

    def _trigger_action(self, mon: dict) -> None:
        action = str(mon.get("action") or "")
        name = str(mon.get("name") or mon.get("monitor_id"))
        result = "triggered"
        if action == "notify" and self.notifier is not None:
            self.notifier.notify("JARVIS monitor", f"Monitor triggered: {name}", "normal")
        if getattr(self.db, "available", False):
            self.db.insert("monitor_logs", {"timestamp": time.time(), "monitor_id": mon.get("monitor_id"), "name": name, "result": result})
            self.db.update("monitors", {"monitor_id": mon.get("monitor_id")}, {"$inc": {"triggered_count": 1}})
        if self.reporter is not None:
            self.reporter.log_autonomous_action(name, "monitor", result, str(mon.get("monitor_id")))

    def _check_all_conditions(self) -> None:
        mons = self.list_monitors()
        for mon in mons:
            if not mon.get("enabled", True):
                continue
            met = self._cond_met(mon)
            if met:
                self._trigger_action(mon)
            if getattr(self.db, "available", False):
                self.db.update("monitors", {"monitor_id": mon.get("monitor_id")}, {"$set": {"last_checked": time.time()}})

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="jarvis-monitor")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(timeout=60.0):
            self._check_all_conditions()

    def stop(self) -> None:
        self._stop.set()


if __name__ == "__main__":
    print("ProactiveMonitor requires runtime dependencies.")
