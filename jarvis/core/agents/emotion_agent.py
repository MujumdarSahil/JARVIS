# SELF-AWARE SYSTEM
"""Emotion-aware preprocessor agent."""

from __future__ import annotations

import time
from typing import Any

from core.agents.base_agent import BaseAgent


class EmotionAgent(BaseAgent):
    def __init__(self, brain: Any, db: Any, config: dict[str, Any], sentiment_analyzer: Any, mood_tracker: Any, tone_adapter: Any) -> None:
        super().__init__("emotion", brain, db, config)
        self.sentiment_analyzer = sentiment_analyzer
        self.mood_tracker = mood_tracker
        self.tone_adapter = tone_adapter
        self._messages_since_checkin = 0

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        action = str(task.get("action") or task.get("type") or "").lower()
        if action == "analyze":
            text = str((task.get("params") or {}).get("text") or task.get("description") or "")
            return {"success": True, "result": self.sentiment_analyzer.analyze(text)}
        if action == "get_mood":
            return {
                "success": True,
                "result": {
                    "current": self.mood_tracker.get_current_mood(),
                    "history": self.mood_tracker.get_mood_history(10),
                    "summary": self.mood_tracker.get_mood_summary(),
                },
            }
        if action == "get_tone_instructions":
            mood = self.mood_tracker.get_current_mood()
            return {"success": True, "result": self.tone_adapter.get_system_prompt_addon(mood, {"sentiment": mood})}
        return {"success": False, "result": f"Unknown emotion action: {action}"}

    def process_message(self, user_message: str) -> dict[str, Any]:
        sentiment = self.sentiment_analyzer.analyze(user_message)
        prev = self.mood_tracker.get_current_mood()
        mood = self.mood_tracker.update(sentiment)
        transition = f"{prev}->{mood}"
        tone = self.tone_adapter.get_system_prompt_addon(mood, sentiment)
        self._messages_since_checkin += 1
        checkin = self.tone_adapter.should_check_in(mood, self._messages_since_checkin)
        if checkin:
            self._messages_since_checkin = 0

        if getattr(self.db, "available", False):
            self.db.insert(
                "emotion_logs",
                {
                    "timestamp": time.time(),
                    "message_preview": (user_message or "")[:160],
                    "sentiment": sentiment,
                    "mood": mood,
                    "mood_transition": transition,
                },
            )
        return {"mood": mood, "sentiment": sentiment, "tone_instructions": tone, "should_checkin": checkin, "mood_transition": transition}

    def get_enhanced_system_prompt(self, base_prompt: str, user_message: str) -> str:
        info = self.process_message(user_message)
        addon = info.get("tone_instructions") or ""
        if not addon:
            return base_prompt
        return f"{base_prompt}\n\nTone adaptation guidance:\n{addon}"


if __name__ == "__main__":
    print("EmotionAgent requires runtime dependencies from main.py")
