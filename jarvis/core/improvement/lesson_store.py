# SELF-AWARE SYSTEM
"""Failure-pattern lesson persistence."""

from __future__ import annotations

import re
import time
from typing import Any


class LessonStore:
    def __init__(self, db: Any) -> None:
        self.db = db
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        if not getattr(self.db, "available", False):
            return
        col = self.db.get_collection("lessons")
        if col is None:
            return
        try:
            col.create_index([("timestamp", 1)], expireAfterSeconds=90 * 24 * 3600)
            col.create_index([("user_message_pattern", "text"), ("hint", "text")])
        except Exception:
            pass

    def _top_keywords(self, text: str, top_n: int = 3) -> list[str]:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", (text or "").lower())
        freq: dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        return [x[0] for x in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:top_n]]

    def save_lesson(self, user_message: str, response: str, score: dict, improvement_hint: str) -> None:
        if float(score.get("overall_score") or 0.0) >= 6:
            return
        kws = self._top_keywords(user_message, 3)
        pattern = " ".join(kws) if kws else (user_message or "")[:50]
        if not getattr(self.db, "available", False):
            return
        existing = self.db.find("lessons", {"user_message_pattern": pattern, "resolved": False}, limit=1)
        if existing:
            row = existing[0]
            cnt = int(row.get("times_seen") or 1) + 1
            self.db.update("lessons", {"_id": row.get("_id")}, {"$set": {"times_seen": cnt, "timestamp": time.time(), "score": score, "hint": improvement_hint}})
            return
        self.db.insert(
            "lessons",
            {
                "user_message_pattern": pattern,
                "failed_response_summary": (response or "")[:200],
                "score": score,
                "hint": improvement_hint,
                "timestamp": time.time(),
                "times_seen": 1,
                "resolved": False,
            },
        )

    def get_relevant_lessons(self, user_message: str, limit: int = 3) -> list[dict[str, Any]]:
        if not getattr(self.db, "available", False):
            return []
        try:
            return self.db.text_search("lessons", user_message, limit=limit) or []
        except Exception:
            return []

    def mark_resolved(self, lesson_id: str) -> None:
        if not getattr(self.db, "available", False):
            return
        self.db.update("lessons", {"_id": lesson_id}, {"$set": {"resolved": True}})

    def get_top_failures(self, limit: int = 10) -> list[dict[str, Any]]:
        if not getattr(self.db, "available", False):
            return []
        rows = self.db.find("lessons", {"resolved": False}, limit=limit, sort=[("times_seen", -1)])
        return rows

    def get_lesson_summary(self) -> str:
        top = self.get_top_failures(5)
        if not top:
            return "No recurring failure patterns found."
        bits = [f"{x.get('hint', 'unknown issue')} ({int(x.get('times_seen') or 1)}x)" for x in top[:3]]
        return "Top issues: " + ", ".join(bits)


if __name__ == "__main__":
    print("LessonStore requires a db instance.")
