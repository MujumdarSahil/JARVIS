# SELF-AWARE SYSTEM
"""Deterministic mood state machine."""

from __future__ import annotations

import time
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


class MoodTracker:
    valid_moods = {"neutral", "engaged", "frustrated", "excited", "impatient", "confused", "satisfied"}

    def __init__(self, db: Any) -> None:
        self.db = db
        self.current_mood = "neutral"
        self.history: list[dict[str, Any]] = []
        self._load_last_mood()

    def _load_last_mood(self) -> None:
        if not getattr(self.db, "available", False):
            return
        row = self.db.find_one("user_profile", {"user_id": "default"})
        if isinstance(row, dict):
            mh = row.get("mood_history") or []
            if isinstance(mh, list) and mh:
                last = mh[-1]
                if isinstance(last, dict) and str(last.get("mood")) in self.valid_moods:
                    self.current_mood = str(last.get("mood"))
                    self.history = [x for x in mh if isinstance(x, dict)][-50:]

    def update(self, sentiment_result: dict[str, Any]) -> str:
        prev = self.current_mood
        sentiment = str((sentiment_result or {}).get("sentiment") or "neutral")
        intense = str((sentiment_result or {}).get("intensity") or "low")

        if prev == "neutral":
            nxt = {"frustrated": "frustrated", "excited": "excited", "confused": "confused", "positive": "engaged", "urgent": "impatient"}.get(sentiment, "neutral")
        elif prev == "frustrated":
            nxt = "impatient" if sentiment in {"frustrated", "negative", "urgent"} else ("neutral" if sentiment in {"positive", "excited"} else "frustrated")
        elif prev == "impatient":
            nxt = "neutral" if sentiment in {"positive", "excited"} else ("frustrated" if sentiment in {"confused", "negative"} else "impatient")
        elif prev == "excited":
            nxt = "satisfied" if sentiment in {"positive", "excited"} else ("neutral" if sentiment in {"neutral", "urgent"} else "confused")
        elif prev == "confused":
            nxt = "engaged" if sentiment in {"positive", "excited"} else ("frustrated" if sentiment in {"frustrated", "negative"} else "confused")
        elif prev == "engaged":
            nxt = "satisfied" if sentiment == "positive" else ("frustrated" if sentiment in {"negative", "frustrated"} else "engaged")
        else:  # satisfied
            nxt = "engaged" if sentiment in {"positive", "excited"} else ("confused" if sentiment == "confused" else "neutral")

        if intense == "high" and sentiment == "urgent":
            nxt = "impatient"

        old_mood = prev
        self.current_mood = nxt
        logger.debug(f"Mood transition: {old_mood} -> {self.current_mood} (signal: {sentiment_result['sentiment']})")
        self.save_mood(nxt)
        return nxt

    def get_current_mood(self) -> str:
        return self.current_mood

    def get_mood_history(self, n: int = 10) -> list[dict[str, Any]]:
        return self.history[-max(1, n) :]

    def save_mood(self, mood: str) -> None:
        if mood not in self.valid_moods:
            mood = "neutral"
        row = {"mood": mood, "timestamp": time.time()}
        self.history.append(row)
        self.history = self.history[-200:]
        if getattr(self.db, "available", False):
            self.db.update("user_profile", {"user_id": "default"}, {"$set": {"user_id": "default"}, "$push": {"mood_history": row}}, upsert=True)

    def get_mood_summary(self) -> str:
        last3 = self.history[-3:]
        if len(last3) < 3:
            return f"Current mood is {self.current_mood}."
        moods = [x.get("mood", "neutral") for x in last3]
        if len(set(moods)) == 1:
            return f"User has been {moods[-1]} for the last 3 messages."
        return f"Mood recently shifted from {moods[0]} to {moods[-1]}."


if __name__ == "__main__":
    class _Dummy:
        available = False

    mt = MoodTracker(_Dummy())
    print(mt.update({"sentiment": "frustrated", "intensity": "high"}))
