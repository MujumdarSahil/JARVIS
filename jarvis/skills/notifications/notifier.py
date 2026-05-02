"""Desktop and web notification utilities."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Any

import schedule

try:
    from plyer import notification as plyer_notification

    PLYER_AVAILABLE = True
except Exception:
    plyer_notification = None
    PLYER_AVAILABLE = False

try:
    from win10toast import ToastNotifier

    WIN10TOAST_AVAILABLE = True
except Exception:
    ToastNotifier = None
    WIN10TOAST_AVAILABLE = False


class Notifier:
    """Send desktop/web notifications and manage reminders."""

    def __init__(self, config: dict[str, Any], db: Any = None, socketio: Any = None) -> None:
        self.config = dict(config or {})
        self.db = db
        self.socketio = socketio
        self.notifications_cfg = self.config.get("notifications") or {}
        self._reminders: dict[str, dict[str, Any]] = {}
        self._timer_refs: dict[str, threading.Timer] = {}
        self._scheduler_running = False
        self._toaster = ToastNotifier() if WIN10TOAST_AVAILABLE else None
        self._channel_callbacks: list[Any] = []

    def set_socketio(self, socketio: Any) -> None:
        self.socketio = socketio

    def register_channel_callback(self, callback: Any) -> None:
        if callback is not None:
            self._channel_callbacks.append(callback)

    def _log_notification(self, title: str, message: str, urgency: str) -> None:
        if self.db is None:
            return
        try:
            self.db.insert(
                "agent_logs",
                {
                    "agent_name": "notifier",
                    "task_id": uuid.uuid4().hex,
                    "task_description": title,
                    "timestamp": time.time(),
                    "type": "notification",
                    "success": True,
                    "result_summary": message[:1000],
                    "urgency": urgency,
                },
            )
        except Exception:
            pass

    def notify(self, title: str, message: str, urgency: str = "normal", icon: str | None = None) -> dict[str, Any]:
        try:
            result = {"success": True, "desktop_sent": False, "web_sent": False}
            if bool((self.notifications_cfg or {}).get("desktop", True)):
                try:
                    if PLYER_AVAILABLE:
                        plyer_notification.notify(title=title, message=message, app_icon=icon, timeout=8)
                        result["desktop_sent"] = True
                    elif WIN10TOAST_AVAILABLE and self._toaster is not None:
                        self._toaster.show_toast(title, message, icon_path=icon, duration=6, threaded=True)
                        result["desktop_sent"] = True
                except Exception:
                    pass

            if bool((self.notifications_cfg or {}).get("web_push", True)) and self.socketio is not None:
                try:
                    self.socketio.emit(
                        "push_notification",
                        {"title": title, "message": message, "urgency": urgency},
                    )
                    result["web_sent"] = True
                except Exception:
                    pass

            self._log_notification(title, message, urgency)
            for cb in list(self._channel_callbacks):
                try:
                    cb(f"{title}: {message}")
                except Exception:
                    pass
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def notify_task_complete(self, task_description: str, result_summary: str) -> dict[str, Any]:
        return self.notify("Task Complete", f"{task_description}: {result_summary}", urgency="normal")

    def notify_error(self, error_description: str) -> dict[str, Any]:
        return self.notify("JARVIS Error", error_description, urgency="urgent")

    def notify_reminder(self, reminder_text: str) -> dict[str, Any]:
        return self.notify("Reminder", reminder_text, urgency="normal")

    def schedule_reminder(self, text: str, delay_seconds: int) -> dict[str, Any]:
        try:
            reminder_id = uuid.uuid4().hex
            fire_time = time.time() + int(delay_seconds)
            self._reminders[reminder_id] = {"id": reminder_id, "text": text, "fire_time": fire_time, "type": "delay"}

            timer = threading.Timer(int(delay_seconds), lambda: self.notify_reminder(text))
            timer.daemon = True
            timer.start()
            self._timer_refs[reminder_id] = timer
            return {"success": True, "reminder_id": reminder_id, "fire_time": fire_time}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _ensure_schedule_runner(self) -> None:
        if self._scheduler_running:
            return
        self._scheduler_running = True

        def _loop() -> None:
            while True:
                try:
                    schedule.run_pending()
                    time.sleep(1)
                except Exception:
                    time.sleep(1)

        threading.Thread(target=_loop, daemon=True).start()

    def schedule_reminder_at(self, text: str, time_str: str) -> dict[str, Any]:
        try:
            reminder_id = uuid.uuid4().hex
            self._reminders[reminder_id] = {"id": reminder_id, "text": text, "fire_time_hhmm": time_str, "type": "time"}
            schedule.every().day.at(time_str).do(self.notify_reminder, text)
            self._ensure_schedule_runner()
            return {"success": True, "reminder_id": reminder_id, "time_str": time_str}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_pending_reminders(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for _, item in self._reminders.items():
            row = dict(item)
            if "fire_time" in row:
                row["fire_at"] = datetime.fromtimestamp(float(row["fire_time"])).isoformat()
            out.append(row)
        return out

    def cancel_reminder(self, reminder_id: str) -> bool:
        try:
            t = self._timer_refs.pop(reminder_id, None)
            if t is not None:
                t.cancel()
            return self._reminders.pop(reminder_id, None) is not None
        except Exception:
            return False


if __name__ == "__main__":
    n = Notifier({"notifications": {"enabled": True, "desktop": True, "web_push": False}})
    print(n.notify("Notifier Test", "Jarvis notification test"))
