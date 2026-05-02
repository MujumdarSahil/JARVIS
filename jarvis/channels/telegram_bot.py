"""Telegram channel for Jarvis."""

from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class TelegramChannel:
    def __init__(self, token: str, registry: Any, brain: Any, memory: Any, notifier: Any = None, allowed_user_ids: list[int] | None = None) -> None:
        self.token = token
        self.registry = registry
        self.brain = brain
        self.memory = memory
        self.notifier = notifier
        self.allowed_user_ids = allowed_user_ids or []
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app: Any = None
        self._running = False
        self._voice_enabled = False

    def _authorized(self, user_id: int) -> bool:
        return bool(self.allowed_user_ids and user_id in self.allowed_user_ids)

    @staticmethod
    def _split_message(text: str, max_len: int = 4096) -> list[str]:
        words = text.split()
        chunks: list[str] = []
        cur = ""
        for w in words:
            if len(cur) + len(w) + 1 > max_len:
                chunks.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            chunks.append(cur)
        return chunks or [text[:max_len]]

    async def _handle_text(self, update, context) -> None:
        user = update.effective_user
        if user is None or not self._authorized(int(user.id)):
            await update.message.reply_text("Unauthorized")
            return
        text = (update.message.text or "").strip()
        if text.startswith("/"):
            cmd, _, args = text.partition(" ")
            if cmd == "/start":
                await update.message.reply_text("Welcome to Jarvis Telegram channel. Use /help to see commands.")
                return
            if cmd == "/help":
                cmds = ", ".join(self.registry.plugin_registry.get_all_commands()) if getattr(self.registry, "plugin_registry", None) else "none"
                await update.message.reply_text(f"Available plugin commands: {cmds}")
                return
            if cmd == "/voice":
                self._voice_enabled = not self._voice_enabled
                await update.message.reply_text(f"Voice mode: {'on' if self._voice_enabled else 'off'}")
                return
            if cmd == "/status":
                await update.message.reply_text(f"Provider: {self.brain.get_active_provider()}")
                return
            if cmd == "/image" and args:
                out = self.registry.route(f"analyze image {args}", self.memory.get_history())
                await update.message.reply_text(str(out.get("final_response") or out.get("response") or ""))
                return
            p_registry = getattr(self.registry, "plugin_registry", None)
            if p_registry is not None:
                result = p_registry.route(cmd, args, {"user_message": text, "session_id": "telegram", "conversation_history": self.memory.get_history(), "mood": "neutral"})
                if result is not None:
                    for chunk in self._split_message(str(result.get("response") or "")):
                        await update.message.reply_text(chunk)
                    return
        routed = self.registry.route(text, self.memory.get_history())
        reply = str(routed.get("final_response") or routed.get("response") or "")
        for chunk in self._split_message(reply):
            await update.message.reply_text(chunk)

    async def _handle_photo(self, update, context) -> None:
        user = update.effective_user
        if user is None or not self._authorized(int(user.id)):
            await update.message.reply_text("Unauthorized")
            return
        if not update.message.photo:
            return
        photo = update.message.photo[-1]
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        path = Path(tf.name)
        tf.close()
        try:
            file = await context.bot.get_file(photo.file_id)
            await file.download_to_drive(str(path))
            routed = self.registry.route(f"analyze image {path}", self.memory.get_history())
            await update.message.reply_text(str(routed.get("final_response") or routed.get("response") or ""))
        finally:
            path.unlink(missing_ok=True)

    async def _handle_voice(self, update, context) -> None:
        user = update.effective_user
        if user is None or not self._authorized(int(user.id)):
            await update.message.reply_text("Unauthorized")
            return
        await update.message.reply_text("Voice transcription endpoint is enabled, but local STT file mode is not configured in this channel yet.")

    async def send_notification(self, text: str) -> None:
        if not self._app:
            return
        for uid in self.allowed_user_ids:
            try:
                await self._app.bot.send_message(chat_id=uid, text=text)
            except Exception as e:
                logger.warning("Telegram notify failed for %s: %s", uid, e)

    def send_notification_sync(self, text: str) -> None:
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.send_notification(text), self._loop)
        except Exception as e:
            logger.warning("Telegram notify schedule failed: %s", e)

    def _run(self) -> None:
        from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        app = Application.builder().token(self.token).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))
        app.add_handler(MessageHandler(filters.PHOTO, self._handle_photo))
        app.add_handler(MessageHandler(filters.VOICE, self._handle_voice))
        app.add_handler(CommandHandler("start", self._handle_text))
        app.add_handler(CommandHandler("help", self._handle_text))
        app.add_handler(CommandHandler("voice", self._handle_text))
        app.add_handler(CommandHandler("image", self._handle_text))
        app.add_handler(CommandHandler("status", self._handle_text))
        self._app = app
        self._running = True
        app.run_polling(close_loop=False)

    def start(self) -> None:
        if self._running:
            return
        self._thread = threading.Thread(target=self._run, name="jarvis-telegram", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._loop and self._app:
            asyncio.run_coroutine_threadsafe(self._app.shutdown(), self._loop)


if __name__ == "__main__":
    print("TelegramChannel module loaded.")
