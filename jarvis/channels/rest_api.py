"""Standalone FastAPI server for Jarvis."""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from utils.logger import get_logger

logger = get_logger(__name__)


class ChatBody(BaseModel):
    message: str
    session_id: str = "default"


class PluginBody(BaseModel):
    command: str = ""
    args: str = ""


class NotifyBody(BaseModel):
    title: str
    message: str


class JarvisAPIServer:
    def __init__(self, registry: Any, brain: Any, memory: Any, plugin_registry: Any, config: dict[str, Any]) -> None:
        self.registry = registry
        self.brain = brain
        self.memory = memory
        self.plugin_registry = plugin_registry
        self.config = config
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None
        self._started_at = time.time()
        self.app = FastAPI(title="Jarvis API", version="1.0")
        self._setup()

    def _auth_guard(self, authorization: str | None) -> None:
        cfg = self.config.get("api_server") or {}
        token = str(cfg.get("auth_token") or "")
        if not token:
            logger.warning("[API] auth_token is empty; auth disabled for developer mode.")
            return
        header = authorization or ""
        if not header.startswith("Bearer ") or header.split(" ", 1)[1] != token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    def _setup(self) -> None:
        self.app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

        @self.app.post("/chat")
        def chat(body: ChatBody, authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            start = time.perf_counter()
            routed = self.registry.route(body.message, self.memory.get_history())
            reply = str(routed.get("final_response") or routed.get("response") or "")
            self.memory.add("user", body.message)
            self.memory.add("assistant", reply)
            return {
                "response": reply,
                "tool_used": str(routed.get("tool_used") or ""),
                "session_id": body.session_id,
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }

        @self.app.get("/status")
        def status(authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            return {"status": "ok", "provider": self.brain.get_active_provider(), "uptime": int(time.time() - self._started_at), "mood": "neutral", "version": "1.0"}

        @self.app.get("/history")
        def history(authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            return {"history": self.memory.get_history()}

        @self.app.delete("/history")
        def clear_history(authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            self.memory.clear()
            return {"ok": True}

        @self.app.post("/voice")
        def voice(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename or "voice.wav").suffix or ".wav") as tf:
                tf.write(file.file.read())
                path = tf.name
            transcript = "Voice endpoint received file, but file-based STT is not enabled in this build."
            routed = self.registry.route(transcript, self.memory.get_history())
            return {"transcript": transcript, "response": str(routed.get("final_response") or routed.get("response") or "")}

        @self.app.post("/image")
        def image(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename or "image.jpg").suffix or ".jpg") as tf:
                tf.write(file.file.read())
                path = tf.name
            routed = self.registry.route(f"analyze image {path}", self.memory.get_history())
            return {"description": str(routed.get("final_response") or routed.get("response") or "")}

        @self.app.get("/plugins")
        def plugins(authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            return {"plugins": self.plugin_registry.list_plugins() if self.plugin_registry else []}

        @self.app.post("/plugin/{name}")
        def plugin(name: str, body: PluginBody, authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            if not self.plugin_registry:
                raise HTTPException(status_code=404, detail="Plugin system disabled")
            out = self.plugin_registry.route(body.command or f"/{name}", body.args, {"session_id": "api", "user_message": body.args, "conversation_history": self.memory.get_history(), "mood": "neutral"})
            if out is None:
                raise HTTPException(status_code=404, detail="Plugin not found")
            return out

        @self.app.post("/notify")
        def notify(body: NotifyBody, authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            notifier = getattr(self.registry, "notifier", None)
            if notifier is None:
                return {"ok": False, "error": "Notifier unavailable"}
            return notifier.notify(body.title, body.message)

        @self.app.get("/metrics")
        def metrics(authorization: str | None = Header(default=None)):
            self._auth_guard(authorization)
            return {"tool_calls": self.registry.get_call_counts(), "uptime_s": int(time.time() - self._started_at)}

    def start(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        cfg = uvicorn.Config(self.app, host=host, port=port, log_level="info")
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="jarvis-api")
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


if __name__ == "__main__":
    print("JarvisAPIServer module loaded.")
