"""
Conversation memory: in-memory transcript, JSON persistence, optional MongoDB.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from core.db import db
from utils.logger import get_logger

logger = get_logger(__name__)


class Memory:
    """
    Maintains chat messages as OpenAI-style dicts: {"role", "content"}.
    Trims to max_history; persists to JSON; mirrors to MongoDB when available.
    """

    def __init__(
        self,
        config: dict[str, Any],
        base_dir: Path | None = None,
        memory_agent: Any | None = None,
    ) -> None:
        mem_cfg = config.get("memory", {})
        self._max_history: int = int(mem_cfg.get("max_history", 20))
        self._persist: bool = bool(mem_cfg.get("persist", True))
        filename = mem_cfg.get("file", "jarvis_memory.json")
        root = base_dir if base_dir is not None else Path.cwd()
        self._path = root / filename
        self._messages: list[dict[str, str]] = []
        self.session_id: str = str(uuid.uuid4())
        self._memory_agent = memory_agent

        dbc = config.get("database") or {}
        self._mongo_enabled: bool = bool(isinstance(dbc, dict) and dbc.get("enabled", True) and db.available)
        self._storage_backend: str = "mongodb" if self._mongo_enabled else "json"

        agents_cfg = config.get("agents") or {}
        mem_ag_cfg = agents_cfg.get("memory") if isinstance(agents_cfg, dict) else {}
        self._extraction_interval: int = int(
            (mem_ag_cfg or {}).get("extraction_interval") or 10
        )
        self._message_count_since_extract: int = 0

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

    def _mongo_insert_message(self, role: str, content: str, message_index: int) -> None:
        if not self._mongo_enabled:
            return
        try:
            db.insert(
                "conversations",
                {
                    "session_id": self.session_id,
                    "role": role,
                    "content": content,
                    "timestamp": time.time(),
                    "message_index": message_index,
                },
            )
        except Exception as e:
            logger.warning("MongoDB conversation insert failed: %s", e)

    def add(self, role: str, content: str) -> None:
        """Append a message and enforce max_history."""
        if role not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
        self._messages.append({"role": role, "content": content})
        idx = len(self._messages) - 1
        self._mongo_insert_message(role, content, idx)
        self._trim()
        self._save()

        self._message_count_since_extract += 1
        if (
            self._memory_agent is not None
            and self._extraction_interval > 0
            and self._message_count_since_extract >= self._extraction_interval
        ):
            self._message_count_since_extract = 0
            try:
                self._memory_agent.run_periodic_extraction(self.get_history())
            except Exception as e:
                logger.warning("Periodic memory extraction failed: %s", e)

    def get_history(self) -> list[dict[str, str]]:
        """Return a copy of the full conversation history."""
        return list(self._messages)

    def clear(self) -> None:
        """Clear all messages and remove the persistence file if present."""
        self._messages.clear()
        self.session_id = str(uuid.uuid4())
        self._message_count_since_extract = 0
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

    def load_from_db(self, session_id: str) -> bool:
        """Load a past session from MongoDB into in-memory history."""
        if not db.available or not (session_id or "").strip():
            return False
        try:
            rows = db.find(
                "conversations",
                {"session_id": session_id},
                limit=500,
                sort=[("message_index", 1)],
            )
            loaded: list[dict[str, str]] = []
            for r in rows:
                role = r.get("role")
                content = r.get("content")
                if role in ("user", "assistant") and isinstance(content, str):
                    loaded.append({"role": role, "content": content})
            if not loaded:
                return False
            self._messages = loaded
            self._trim()
            self.session_id = session_id
            self._save()
            return True
        except Exception as e:
            logger.warning("load_from_db failed: %s", e)
            return False

    def search_history(self, query: str) -> list:
        """MongoDB text search across stored conversations."""
        if not db.available or not (query or "").strip():
            return []
        try:
            return db.text_search("conversations", query, limit=20)
        except Exception as e:
            logger.warning("search_history failed: %s", e)
            return []

    def get_sessions(self) -> list:
        """Past session ids with preview and last timestamp."""
        if not db.available:
            return []
        try:
            col = db.get_collection("conversations")
            if col is None:
                return []
            pipeline = [
                {"$group": {"_id": "$session_id", "last_ts": {"$max": "$timestamp"}, "first": {"$first": "$$ROOT"}}},
                {"$sort": {"last_ts": -1}},
                {"$limit": 50},
            ]
            out = []
            for doc in col.aggregate(pipeline):
                sid = doc.get("_id")
                root = doc.get("first") or {}
                preview = (root.get("content") or "")[:120]
                out.append(
                    {
                        "session_id": sid,
                        "preview": preview,
                        "timestamp": doc.get("last_ts"),
                    }
                )
            return out
        except Exception as e:
            logger.warning("get_sessions failed: %s", e)
            return []

    def get_stats(self) -> dict[str, Any]:
        """Storage and volume summary."""
        total_messages = len(self._messages)
        total_sessions = 0
        oldest = None
        try:
            if db.available:
                col = db.get_collection("conversations")
                if col is not None:
                    total_sessions = len(col.distinct("session_id"))
                    doc = col.find_one(sort=[("timestamp", 1)])
                    if doc:
                        oldest = doc.get("timestamp")
        except Exception as e:
            logger.debug("get_stats mongo: %s", e)
        return {
            "total_messages": total_messages,
            "total_sessions": total_sessions,
            "oldest_message": oldest,
            "storage_backend": self._storage_backend,
            "session_id": self.session_id,
        }
