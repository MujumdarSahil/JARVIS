"""Generic webhook receiver for Jarvis Flask app."""

from __future__ import annotations

import time
from typing import Any

from flask import jsonify, request

from utils.logger import get_logger

logger = get_logger(__name__)


class WebhookReceiver:
    def __init__(self, app, registry: Any, notifier: Any, config: dict[str, Any], db: Any = None) -> None:
        self.app = app
        self.registry = registry
        self.notifier = notifier
        self.config = config
        self.db = db
        self._secret = str((config.get("webhooks") or {}).get("secret") or "")

    def _auth_ok(self) -> bool:
        if not self._secret:
            return True
        return request.headers.get("X-Webhook-Secret", "") == self._secret

    def _log(self, payload: dict[str, Any]) -> None:
        if self.db is None or not getattr(self.db, "available", False):
            return
        try:
            self.db.insert("webhook_log", {"timestamp": time.time(), **payload})
        except Exception:
            pass

    def register_routes(self) -> None:
        @self.app.post("/webhook/jarvis")
        def webhook_jarvis():
            if not self._auth_ok():
                return jsonify({"error": "Unauthorized"}), 401
            body = request.get_json(silent=True) or {}
            source = str(body.get("source") or "").lower()
            event = str(body.get("event") or "").lower()
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            self._log({"source": source, "event": event, "data": data})
            if source == "github" and event in ("push", "pull_request"):
                msg = f"github {event}: {data}"
                out = self.registry.route(msg, [])
                return jsonify({"ok": True, "result": out})
            if source == "custom":
                msg = str(data.get("message") or "")
                out = self.registry.route(msg, [])
                return jsonify({"ok": True, "result": out})
            return jsonify({"ok": False, "error": "Unsupported source/event"}), 400

        @self.app.post("/webhook/notify")
        def webhook_notify():
            if not self._auth_ok():
                return jsonify({"error": "Unauthorized"}), 401
            body = request.get_json(silent=True) or {}
            title = str(body.get("title") or "Webhook")
            message = str(body.get("message") or "")
            self._log({"source": "notify", "event": "notify", "data": {"title": title, "message": message}})
            return jsonify(self.notifier.notify(title=title, message=message) if self.notifier else {"ok": False})


if __name__ == "__main__":
    print("WebhookReceiver module loaded.")
