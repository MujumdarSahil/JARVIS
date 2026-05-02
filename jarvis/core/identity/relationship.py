from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


class RelationshipTracker:
    def __init__(self, db: Any, jarvis_self: Any) -> None:
        self.db = db
        self.jarvis_self = jarvis_self
        self._json_path = Path(__file__).resolve().parents[2] / "relationship.json"
        self.relationship_data = self._defaults()
        self._load()

    def _defaults(self) -> dict[str, Any]:
        now = datetime.now().timestamp()
        return {
            "first_interaction": now,
            "total_sessions": 0,
            "total_messages": 0,
            "avg_session_length": 0.0,
            "longest_session_messages": 0,
            "trust_score": 0.0,
            "rapport_level": "new",
            "user_communication_style": "unknown",
            "preferred_response_length": "medium",
            "topics_of_interest": [],
            "last_interaction": now,
            "streak_days": 0,
            "milestone_messages": [100, 500, 1000],
            "milestones_hit": [],
        }

    def _load(self) -> None:
        if getattr(self.db, "available", False):
            row = self.db.find_one("relationship", {"id": "primary"})
            if row:
                self.relationship_data.update({k: v for k, v in row.items() if k in self.relationship_data or k == "milestones_hit"})
                return
        if self._json_path.exists():
            try:
                self.relationship_data.update(json.loads(self._json_path.read_text(encoding="utf-8")))
            except Exception:
                pass

    def _save(self) -> None:
        if getattr(self.db, "available", False):
            d = dict(self.relationship_data)
            d["id"] = "primary"
            self.db.update("relationship", {"id": "primary"}, d, upsert=True)
        try:
            self._json_path.write_text(json.dumps(self.relationship_data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def update_trust_score(self, interaction_quality: float) -> None:
        old = float(self.relationship_data.get("trust_score", 0.0))
        q = max(0.0, min(1.0, interaction_quality))
        new = old * 0.95 + q * 5.0
        self.relationship_data["trust_score"] = max(0.0, min(100.0, round(new, 2)))
        self.relationship_data["rapport_level"] = self.get_rapport_level()

    def detect_communication_style(self, recent_messages: list[str]) -> str:
        if len(recent_messages) < 20:
            return "unknown"
        avg_len = sum(len(m.split()) for m in recent_messages) / max(1, len(recent_messages))
        technical_tokens = sum(1 for m in recent_messages for w in ("python", "api", "debug", "json", "sql") if w in m.lower())
        if avg_len < 8:
            return "casual"
        if technical_tokens >= 8:
            return "technical"
        if avg_len > 20:
            return "formal"
        return "mixed"

    def get_rapport_level(self) -> str:
        score = float(self.relationship_data.get("trust_score", 0.0))
        if score < 25:
            return "new"
        if score < 50:
            return "familiar"
        if score < 75:
            return "trusted"
        return "partner"

    def record_interaction(self, session_messages: int, mood: str, success_rate: float) -> None:
        _ = mood
        self.relationship_data["total_sessions"] = int(self.relationship_data.get("total_sessions", 0)) + 1
        self.relationship_data["total_messages"] = int(self.relationship_data.get("total_messages", 0)) + max(0, int(session_messages))
        ts = int(self.relationship_data["total_sessions"])
        tm = int(self.relationship_data["total_messages"])
        self.relationship_data["avg_session_length"] = round(tm / max(1, ts), 2)
        self.relationship_data["longest_session_messages"] = max(int(self.relationship_data.get("longest_session_messages", 0)), int(session_messages))
        self.relationship_data["last_interaction"] = datetime.now().timestamp()
        self.update_trust_score(max(0.0, min(1.0, success_rate)))
        self._update_streak()
        self.jarvis_self.identity["relationship_duration_days"] = max(0, int((datetime.now().timestamp() - float(self.relationship_data.get("first_interaction", datetime.now().timestamp()))) / 86400))
        self._save()

    def _update_streak(self) -> None:
        last = float(self.relationship_data.get("last_interaction", 0))
        if last <= 0:
            self.relationship_data["streak_days"] = 1
            return
        last_day = datetime.fromtimestamp(last).date()
        today = datetime.now().date()
        if (today - last_day).days <= 1:
            self.relationship_data["streak_days"] = int(self.relationship_data.get("streak_days", 0)) + 1
        else:
            self.relationship_data["streak_days"] = 1

    def check_milestones(self) -> list[int]:
        milestones = list(self.relationship_data.get("milestone_messages", [100, 500, 1000]))
        total = int(self.relationship_data.get("total_messages", 0))
        hit = set(self.relationship_data.get("milestones_hit", []))
        new = [m for m in milestones if total >= m and m not in hit]
        if new:
            hit.update(new)
            self.relationship_data["milestones_hit"] = sorted(hit)
            self._save()
        return new

    def get_milestone_message(self, milestone: int) -> str:
        if milestone == 100:
            return "We've exchanged 100 messages, Sir. I'm beginning to understand your preferences."
        if milestone == 500:
            return "500 exchanges, Sir. I'd say we work well together."
        if milestone == 1000:
            return "A thousand conversations, Sir. I know you better than most."
        return f"We just crossed {milestone} messages, Sir."

    def get_relationship_summary(self) -> str:
        days = max(0, int((datetime.now().timestamp() - float(self.relationship_data.get("first_interaction", datetime.now().timestamp()))) / 86400))
        sessions = int(self.relationship_data.get("total_sessions", 0))
        return f"We've worked together for {days} days across {sessions} sessions. Trust level: {self.get_rapport_level()}."


if __name__ == "__main__":
    print("RelationshipTracker module loaded.")
