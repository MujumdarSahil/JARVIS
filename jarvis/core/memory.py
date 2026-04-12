"""
Conversation memory: in-memory transcript with optional JSON persistence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class Memory:
    """
    Maintains chat messages as OpenAI-style dicts: {"role", "content"}.
    Trims to max_history and optionally persists to disk.
    """

    def __init__(self, config: dict[str, Any], base_dir: Path | None = None) -> None:
        mem_cfg = config.get("memory", {})
        self._max_history: int = int(mem_cfg.get("max_history", 20))
        self._persist: bool = bool(mem_cfg.get("persist", True))
        filename = mem_cfg.get("file", "jarvis_memory.json")
        root = base_dir if base_dir is not None else Path.cwd()
        self._path = root / filename
        self._messages: list[dict[str, str]] = []
        if self._persist:
            self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                self._messages = [m for m in data if self._valid_message(m)]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not load memory from %s: %s", self._path, e)
            self._messages = []

    @staticmethod
    def _valid_message(m: Any) -> bool:
        return (
            isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        )

    def _save(self) -> None:
        if not self._persist:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._messages, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("Failed to save memory to %s: %s", self._path, e)

    def _trim(self) -> None:
        if len(self._messages) > self._max_history:
            self._messages = self._messages[-self._max_history :]

    def add(self, role: str, content: str) -> None:
        """Append a message and enforce max_history."""
        if role not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
        self._messages.append({"role": role, "content": content})
        self._trim()
        self._save()

    def get_history(self) -> list[dict[str, str]]:
        """Return a copy of the full conversation history."""
        return list(self._messages)

    def clear(self) -> None:
        """Clear all messages and remove the persistence file if present."""
        self._messages.clear()
        if self._persist and self._path.is_file():
            try:
                self._path.unlink()
            except OSError as e:
                logger.warning("Could not delete memory file %s: %s", self._path, e)

    def get_context_window(self, n: int = 10) -> list[dict[str, str]]:
        """Return the last n messages (or fewer if history is shorter)."""
        if n <= 0:
            return []
        return list(self._messages[-n:])

    def drop_last(self) -> None:
        """Remove the most recent message if present (e.g. after a failed LLM call)."""
        if self._messages:
            self._messages.pop()
            self._save()
