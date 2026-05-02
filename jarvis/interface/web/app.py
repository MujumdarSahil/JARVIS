"""
Flask + Socket.IO web server for Jarvis.

TODO: add auth (local network only for now).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, render_template, request, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO

from core.context import Context
from interface.web.api import create_api_blueprint
from utils.logger import get_logger

logger = get_logger(__name__)

_JARVIS_VERSION = "1.0"
_WEB_ROOT = Path(__file__).resolve().parent


class WebInterface:
    """Runs the HUD web app in a daemon thread alongside the CLI."""

    def __init__(
        self,
        brain: Any,
        memory: Any,
        registry: Any,
        speaker: Any = None,
        listener: Any = None,
        config: dict[str, Any] | None = None,
        context: Any = None,
        device_manager: Any = None,
        routine_manager: Any = None,
        notifier: Any = None,
        suggestion_engine: Any = None,
        jarvis_self: Any = None,
        relationship: Any = None,
        pattern_engine: Any = None,
        weekly_learner: Any = None,
    ) -> None:
        self.brain = brain
        self.memory = memory
        self.registry = registry
        self.speaker = speaker
        self.listener = listener
        self.config: dict[str, Any] = dict(config or {})
        self.context = context
        self.device_manager = device_manager
        self.routine_manager = routine_manager
        self.notifier = notifier
        self.suggestion_engine = suggestion_engine
        self.jarvis_self = jarvis_self
        self.relationship = relationship
        self.pattern_engine = pattern_engine
        self.weekly_learner = weekly_learner
        if self.context is None:
            base = _WEB_ROOT.parent.parent
            self.context = Context(self.config, base_dir=base)

        vc = self.config.get("voice") or self.config.get("yamlvoice") or {}
        self._voice_cfg: dict[str, Any] = dict(vc) if isinstance(vc, dict) else {}
        self._web_voice_output_enabled: bool = bool(self._voice_cfg.get("enabled"))

        self._started_at = time.perf_counter()
        self._thread: threading.Thread | None = None
        self._stats_thread: threading.Thread | None = None
        self._stop_stats = threading.Event()
        self._smarthome_thread: threading.Thread | None = None
        self._stop_smarthome = threading.Event()
        self._last_device_sig: str | None = None
        self._last_mood: str = "neutral"

        sh = self.config.get("smarthome") or {}
        self._smarthome_enabled = bool(isinstance(sh, dict) and sh.get("enabled"))
        ha_c = (sh.get("homeassistant") or {}) if isinstance(sh, dict) else {}
        self._smarthome_ha_ready = bool(
            str(ha_c.get("url") or "").strip() and str(ha_c.get("token") or "").strip()
        )
        self._smarthome_scan_interval = int(ha_c.get("scan_interval") or 10)

        self.app = Flask(
            __name__,
            template_folder=str(_WEB_ROOT / "templates"),
            static_folder=str(_WEB_ROOT / "static"),
        )
        self.app.config["SECRET_KEY"] = "jarvis-web-dev"  # noqa: S105 — local only; TODO: auth

        CORS(self.app, resources={r"/*": {"origins": "*"}})

        # Waitress (and most pure WSGI servers) cannot expose the TCP socket for WebSocket
        # upgrades. Use Engine.IO long-polling only — still works with Socket.IO client.
        self.socketio = SocketIO(
            self.app,
            cors_allowed_origins="*",
            async_mode="threading",
            allow_upgrades=False,
            transports=["polling"],
        )

        self.app.register_blueprint(create_api_blueprint(self))

        @self.app.route("/")
        def index():
            return render_template(
                "index.html",
                version=_JARVIS_VERSION,
                voice_config_enabled=bool(self._voice_cfg.get("enabled")),
                smarthome_enabled=self._smarthome_enabled,
                smarthome_ha_ready=self._smarthome_ha_ready,
            )

        @self.app.route("/dashboard")
        def dashboard():
            return render_template("dashboard.html", version=_JARVIS_VERSION)

        @self.app.route("/sw.js")
        def service_worker():
            web_dir = _WEB_ROOT
            return send_from_directory(str(web_dir), "sw.js", mimetype="application/javascript")

        @self.app.route("/manifest.json")
        def manifest():
            static_dir = _WEB_ROOT / "static"
            return send_from_directory(str(static_dir), "manifest.json", mimetype="application/manifest+json")

        self._register_socket_handlers()

    def _build_llm_messages(self) -> list[dict[str, str]]:
        system = self.context.get_system_prompt()
        ea = getattr(self.registry, "emotion_agent", None)
        if ea is not None:
            try:
                hist = self.memory.get_history()
                latest_user = next((m.get("content", "") for m in reversed(hist) if m.get("role") == "user"), "")
                system = self.context.get_enhanced_prompt(latest_user, ea)
            except Exception:
                pass
        return [{"role": "system", "content": system}, *self.memory.get_history()]

    def parse_system_info(self) -> dict[str, Any]:
        raw = self.registry.shell_skill.get_system_info()
        if not raw.get("success"):
            return {"error": raw.get("stderr") or "get_system_info failed"}
        out: dict[str, Any] = {}
        for line in (raw.get("stdout") or "").splitlines():
            if ": " not in line:
                continue
            key, val = line.split(": ", 1)
            key, val = key.strip(), val.strip()
            out[key] = val
        # Normalize numeric fields for the UI
        for k in ("cpu_percent", "ram_percent", "disk_percent"):
            if k in out:
                try:
                    out[k] = float(out[k])
                except ValueError:
                    pass
        if "uptime_seconds" in out:
            try:
                out["uptime_seconds"] = int(float(out["uptime_seconds"]))
            except ValueError:
                pass
        return out

    def get_active_skills_summary(self) -> list[dict[str, Any]]:
        smap = self.registry._skills_map()
        keys = [
            ("web_search", "search"),
            ("file_control", "files"),
            ("shell_control", "shell"),
            ("clipboard", "clipboard"),
            ("app_launcher", "apps"),
            ("code_assistant", "code"),
            ("smarthome", "smarthome"),
        ]
        rows: list[dict[str, Any]] = []
        for cfg_key, label in keys:
            en = bool(smap.get(cfg_key, True))
            rows.append({"id": cfg_key, "label": label, "enabled": en})
        return rows

    def get_status_payload(self) -> dict[str, Any]:
        models = self.brain._config.get("models") or {}
        primary = (models.get("primary") or "groq") if isinstance(models, dict) else "groq"
        prov = self.brain._get_provider_config(str(primary).lower()) if hasattr(self.brain, "_get_provider_config") else {}
        model_id = prov.get("model", "") if isinstance(prov, dict) else ""
        chain = self.brain.get_configured_providers()
        uptime_s = int(time.perf_counter() - self._started_at)
        return {
            "status": "ok",
            "provider": self.brain.get_active_provider(),
            "primary_configured": chain[0] if chain else primary,
            "model": model_id,
            "skills": self.get_active_skills_summary(),
            "voice_enabled": bool(self._voice_cfg.get("enabled")),
            "web_voice_output": self._web_voice_output_enabled,
            "uptime": uptime_s,
            "version": _JARVIS_VERSION,
        }

    def get_jarvis_status_event(self) -> dict[str, Any]:
        return {
            "provider": self.brain.get_active_provider(),
            "primary": (self.brain._config.get("models") or {}).get("primary"),
            "skills": self.get_active_skills_summary(),
            "voice": {
                "config_enabled": bool(self._voice_cfg.get("enabled")),
                "web_output_enabled": self._web_voice_output_enabled,
                "listener_ok": bool(self.listener and getattr(self.listener, "available", False)),
                "speaker_ok": bool(self.speaker and getattr(self.speaker, "is_ready", False)),
            },
        }

    def process_chat_message(self, user_line: str) -> tuple[str, str, str]:
        """Run router + brain, update memory. Returns (reply, tool_used, provider_name)."""
        start = time.perf_counter()
        self.memory.add("user", user_line)
        messages = self._build_llm_messages()
        try:
            routed = self.registry.route(user_line, messages)
            reply = (routed.get("final_response") or routed.get("response") or "").strip()
            tool_used = str(routed.get("tool_used") or "")
            action = str(routed.get("action") or "")
            if not reply:
                reply = (self.brain.chat(messages) or "").strip()
            self.memory.add("assistant", reply)
            try:
                ea = getattr(self.registry, "emotion_agent", None)
                if ea is not None:
                    mood = ea.mood_tracker.get_current_mood()
                    if mood != self._last_mood:
                        self._last_mood = mood
                        self.socketio.emit("mood_update", {"mood": mood, "sentiment": "", "timestamp": time.time()})
            except Exception:
                pass
        except Exception as e:
            logger.exception("Web chat route failed: %s", e)
            self.memory.drop_last()
            raise
        duration_ms = int((time.perf_counter() - start) * 1000)
        prov = self.brain.get_active_provider()
        tool_label = tool_used
        if action and action not in ("none", "brain.chat"):
            tool_label = f"{tool_used}.{action}" if tool_used else action
        return reply, tool_label, prov, duration_ms

    def _performance_broadcaster_loop(self) -> None:
        while not self._stop_stats.wait(timeout=300.0):
            try:
                sia = getattr(self.registry, "self_improvement_agent", None)
                if sia is None:
                    continue
                stats = sia.get_performance_stats()
                self.socketio.emit(
                    "performance_update",
                    {
                        "avg_score": stats.get("avg_score_all_time"),
                        "trend": stats.get("improvement_trend"),
                        "total_evaluated": stats.get("total_evaluated"),
                    },
                )
            except Exception as e:
                logger.debug("performance broadcast failed: %s", e)

    def _maybe_speak_web(self, text: str) -> None:
        if not self._web_voice_output_enabled or not self.speaker:
            return
        if not getattr(self.speaker, "is_ready", False):
            return

        def _run() -> None:
            try:
                self.speaker.speak_sync(text)
            except Exception as e:
                logger.warning("Web TTS failed: %s", e)

        threading.Thread(target=_run, daemon=True).start()

    def _register_socket_handlers(self) -> None:
        @self.socketio.on("connect")
        def on_connect():
            try:
                logger.info("Web client connected")
                self.socketio.emit("jarvis_status", self.get_jarvis_status_event(), to=request.sid)
            except Exception as e:
                logger.exception("connect handler: %s", e)

        @self.socketio.on("disconnect")
        def on_disconnect():
            try:
                logger.info("Web client disconnected")
            except Exception as e:
                logger.exception("disconnect handler: %s", e)

        @self.socketio.on("user_message")
        def on_user_message(data):
            try:
                payload = data if isinstance(data, dict) else {}
                message = (payload.get("message") or "").strip()
                session_id = str(payload.get("session_id") or "default")
                if not message:
                    self.socketio.emit(
                        "jarvis_response",
                        {
                            "response": "",
                            "tool_used": "",
                            "action": "",
                            "session_id": session_id,
                            "error": "empty message",
                        },
                        to=request.sid,
                    )
                    return
                reply, tool_used, _prov, duration_ms = self.process_chat_message(message)
                action = tool_used.split(".", 1)[-1] if "." in tool_used else ""
                self.socketio.emit(
                    "jarvis_response",
                    {
                        "response": reply,
                        "tool_used": tool_used,
                        "action": action,
                        "session_id": session_id,
                        "duration_ms": duration_ms,
                    },
                    to=request.sid,
                )
                self._maybe_speak_web(reply)
                if self.suggestion_engine is not None:
                    suggestions = self.suggestion_engine.get_suggestions({})
                    if suggestions:
                        s = suggestions[0]
                        if self.suggestion_engine.should_surface_suggestion(s):
                            self.suggestion_engine.mark_shown(s)
                            self.socketio.emit(
                                "proactive_suggestion",
                                {"suggestion": self.suggestion_engine.format_suggestion_for_web([s])[0]},
                                to=request.sid,
                            )
                if self.notifier is not None and bool((self.config.get("notifications") or {}).get("web_push", True)):
                    low = reply.lower()
                    urgency_words = ("error", "failed", "complete", "done", "finished")
                    if any(w in low for w in urgency_words):
                        self.notifier.notify(
                            title="JARVIS Update",
                            message=reply[:300],
                            urgency="urgent" if ("error" in low or "failed" in low) else "normal",
                        )
            except Exception as e:
                logger.exception("user_message handler: %s", e)
                try:
                    sid = "default"
                    if isinstance(data, dict):
                        sid = str(data.get("session_id") or "default")
                    self.socketio.emit(
                        "jarvis_response",
                        {
                            "response": f"I encountered an error: {e}",
                            "tool_used": "error",
                            "action": "error",
                            "session_id": sid,
                            "error": str(e),
                        },
                        to=request.sid,
                    )
                except Exception as e2:
                    logger.exception("emit error response failed: %s", e2)

        @self.socketio.on("set_reminder")
        def handle_reminder(data):
            try:
                payload = data if isinstance(data, dict) else {}
                if self.notifier is None:
                    self.socketio.emit("reminder_ack", {"ok": False, "error": "Notifier unavailable"}, to=request.sid)
                    return
                text = str(payload.get("text") or "Reminder")
                if "delay_seconds" in payload:
                    out = self.notifier.schedule_reminder(text, int(payload.get("delay_seconds") or 60))
                elif "time_str" in payload:
                    out = self.notifier.schedule_reminder_at(text, str(payload.get("time_str") or "14:30"))
                else:
                    out = {"success": False, "error": "Provide delay_seconds or time_str"}
                self.socketio.emit("reminder_ack", {"ok": bool(out.get("success")), "data": out}, to=request.sid)
            except Exception as e:
                self.socketio.emit("reminder_ack", {"ok": False, "error": str(e)}, to=request.sid)

        @self.socketio.on("voice_toggle")
        def on_voice_toggle(data):
            try:
                payload = data if isinstance(data, dict) else {}
                enabled = bool(payload.get("enabled"))
                if not self._voice_cfg.get("enabled"):
                    self.socketio.emit(
                        "voice_toggle_ack",
                        {"ok": False, "enabled": False, "reason": "voice disabled in config"},
                        to=request.sid,
                    )
                    return
                self._web_voice_output_enabled = enabled
                self.socketio.emit(
                    "voice_toggle_ack",
                    {"ok": True, "enabled": self._web_voice_output_enabled},
                    to=request.sid,
                )
                self.socketio.emit("jarvis_status", self.get_jarvis_status_event())
            except Exception as e:
                logger.exception("voice_toggle handler: %s", e)
                try:
                    self.socketio.emit(
                        "voice_toggle_ack",
                        {"ok": False, "error": str(e)},
                        to=request.sid,
                    )
                except Exception as e2:
                    logger.exception("voice_toggle_ack failed: %s", e2)

        @self.socketio.on("clear_memory")
        def on_clear_memory():
            try:
                self.memory.clear()
                self.socketio.emit("memory_cleared", {"ok": True}, to=request.sid)
            except Exception as e:
                logger.exception("clear_memory handler: %s", e)
                try:
                    self.socketio.emit(
                        "memory_cleared",
                        {"ok": False, "error": str(e)},
                        to=request.sid,
                    )
                except Exception as e2:
                    logger.exception("memory_cleared emit failed: %s", e2)

        @self.socketio.on("get_history")
        def on_get_history():
            try:
                self.socketio.emit(
                    "conversation_history",
                    {"history": self.memory.get_history()},
                    to=request.sid,
                )
            except Exception as e:
                logger.exception("get_history handler: %s", e)
                try:
                    self.socketio.emit(
                        "conversation_history",
                        {"history": [], "error": str(e)},
                        to=request.sid,
                    )
                except Exception as e2:
                    logger.exception("conversation_history emit failed: %s", e2)

        @self.socketio.on("system_stats")
        def on_system_stats():
            try:
                stats = self.parse_system_info()
                self.socketio.emit("system_stats", stats, to=request.sid)
            except Exception as e:
                logger.exception("system_stats handler: %s", e)
                try:
                    self.socketio.emit("system_stats", {"error": str(e)}, to=request.sid)
                except Exception as e2:
                    logger.exception("system_stats emit failed: %s", e2)

    def _stats_broadcaster_loop(self) -> None:
        while not self._stop_stats.wait(timeout=5.0):
            try:
                with self.app.app_context():
                    stats = self.parse_system_info()
                self.socketio.emit("system_stats", stats)
            except Exception as e:
                logger.warning("stats broadcast failed: %s", e)

    def _smarthome_poll_loop(self) -> None:
        import hashlib
        import json as _json

        while not self._stop_smarthome.wait(timeout=max(3, self._smarthome_scan_interval)):
            try:
                dm = self.device_manager
                ha = getattr(dm, "ha", None) if dm else None
                if not ha:
                    continue
                r = ha.get_states()
                if not r.get("success"):
                    continue
                states = r.get("data")
                if not isinstance(states, list):
                    continue
                reg = getattr(dm, "_registry", {}) or {}
                want = {e.get("entity_id") for e in (reg.get("entities") or []) if isinstance(e, dict)}
                if not want:
                    want = {
                        s.get("entity_id")
                        for s in states
                        if isinstance(s, dict)
                        and str(s.get("entity_id", "")).split(".")[0]
                        in ("light", "switch", "climate", "lock", "media_player", "sensor")
                    }
                snap: dict[str, Any] = {}
                for s in states:
                    if not isinstance(s, dict):
                        continue
                    eid = s.get("entity_id")
                    if eid in want:
                        snap[str(eid)] = {
                            "state": s.get("state"),
                            "attributes": s.get("attributes") if isinstance(s.get("attributes"), dict) else {},
                        }
                sig_src = _json.dumps(snap, sort_keys=True, default=str)
                sig = hashlib.sha256(sig_src.encode()).hexdigest()
                if sig != self._last_device_sig:
                    self._last_device_sig = sig
                    self.socketio.emit(
                        "device_update",
                        {
                            "states": snap,
                            "rooms": list((reg.get("by_room") or {}).keys()) if isinstance(reg, dict) else [],
                        },
                    )
            except Exception as e:
                logger.warning("smarthome poll failed: %s", e)

    def start(self, host: str = "0.0.0.0", port: int = 5000, debug: bool = False) -> None:
        self._stop_stats.clear()
        self._stop_smarthome.clear()
        self._last_device_sig = None

        def run_server() -> None:
            try:
                # Waitress avoids Werkzeug 3 + threaded SocketIO "write() before start_response" crashes.
                import waitress

                listen = f"{host}:{port}"
                logger.info("Web server (waitress) listening on %s", listen)
                # Flask-SocketIO replaces app.wsgi_app with middleware; serve the Flask app, not socketio.
                waitress.serve(
                    self.app,
                    listen=listen,
                    threads=6,
                )
            except ImportError:
                try:
                    self.socketio.run(
                        self.app,
                        host=host,
                        port=port,
                        debug=debug,
                        use_reloader=False,
                        allow_unsafe_werkzeug=True,
                    )
                except Exception as e:
                    logger.exception("Flask-SocketIO server exited: %s", e)
            except Exception as e:
                logger.exception("Web server exited: %s", e)

        self._thread = threading.Thread(target=run_server, name="jarvis-web", daemon=True)
        self._thread.start()
        self._stats_thread = threading.Thread(
            target=self._stats_broadcaster_loop,
            name="jarvis-web-stats",
            daemon=True,
        )
        self._stats_thread.start()
        threading.Thread(target=self._performance_broadcaster_loop, name="jarvis-web-performance", daemon=True).start()

        if self._smarthome_enabled and self.device_manager is not None:
            self._smarthome_thread = threading.Thread(
                target=self._smarthome_poll_loop,
                name="jarvis-smarthome-poll",
                daemon=True,
            )
            self._smarthome_thread.start()

        logger.info("Web interface thread started on %s:%s", host, port)

    def stop(self) -> None:
        self._stop_stats.set()
        self._stop_smarthome.set()
        # No public API to stop werkzeug from another thread cleanly; process exit tears down daemon thread.
        logger.info("Web interface stop requested (background stats halted).")
