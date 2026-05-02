from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
from typing import Any


class JarvisSelf:
    def __init__(self, db: Any, brain: Any, config: dict[str, Any]) -> None:
        self.db = db
        self.brain = brain
        self.config = config
        self.identity = self._default_identity()
        self._json_path = Path(__file__).resolve().parents[2] / "jarvis_identity.json"
        self._achievement_score = 0.0

    def _default_identity(self) -> dict[str, Any]:
        return {
            "name": "JARVIS",
            "version": "1.0.0",
            "created_at": datetime.now().timestamp(),
            "total_conversations": 0,
            "total_messages_processed": 0,
            "total_tasks_completed": 0,
            "skills_mastered": [],
            "skills_improving": [],
            "skills_struggling": [],
            "personality_traits": ["precise", "helpful", "proactive", "loyal"],
            "current_focus": "",
            "relationship_duration_days": 0,
            "user_satisfaction_score": 0.0,
            "proudest_achievement": "",
        }

    def load(self) -> None:
        if getattr(self.db, "available", False):
            row = self.db.find_one("jarvis_identity", {"name": "JARVIS"})
            if row:
                self.identity.update({k: v for k, v in row.items() if k in self.identity})
                return
        if self._json_path.exists():
            try:
                self.identity.update(json.loads(self._json_path.read_text(encoding="utf-8")))
            except Exception:
                pass

    def save(self) -> None:
        if getattr(self.db, "available", False):
            self.db.update("jarvis_identity", {"name": "JARVIS"}, self.identity, upsert=True)
        try:
            self._json_path.write_text(json.dumps(self.identity, indent=2), encoding="utf-8")
        except Exception:
            pass

    def update_stats(self) -> None:
        if not getattr(self.db, "available", False):
            return
        convs = self.db.count("conversations", {})
        msgs = self.db.count("conversations", {})
        logs = self.db.find("agent_logs", {}, limit=5000)
        by_tool: dict[str, list[bool]] = {}
        for l in logs:
            t = str(l.get("tool_used") or "direct")
            ok = not ("error" in str(l.get("response", "")).lower()) and bool(l.get("success", True))
            by_tool.setdefault(t, []).append(ok)
        mastered, struggling = [], []
        for t, vals in by_tool.items():
            sr = sum(1 for v in vals if v) / len(vals)
            if sr >= 0.9 and len(vals) >= 5:
                mastered.append(t)
            elif (1 - sr) > 0.3 and len(vals) >= 5:
                struggling.append(t)
        self.identity["total_conversations"] = convs
        self.identity["total_messages_processed"] = msgs
        self.identity["total_tasks_completed"] = len(logs)
        self.identity["skills_mastered"] = sorted(mastered)[:12]
        self.identity["skills_struggling"] = sorted(struggling)[:12]
        self.identity["skills_improving"] = [t for t in by_tool.keys() if t not in mastered and t not in struggling][:12]
        created_at = float(self.identity.get("created_at") or datetime.now().timestamp())
        self.identity["relationship_duration_days"] = max(0, int((datetime.now().timestamp() - created_at) / 86400))
        total_success = [ok for vals in by_tool.values() for ok in vals]
        self.identity["user_satisfaction_score"] = round((sum(1 for x in total_success if x) / len(total_success)) * 100.0, 2) if total_success else 0.0
        self.save()

    def update_stats_async(self) -> None:
        threading.Thread(target=self.update_stats, daemon=True, name="jarvis-self-stats").start()

    def get_self_description(self) -> str:
        success = float(self.identity.get("user_satisfaction_score") or 0.0)
        return (
            f"I am {self.identity.get('name', 'JARVIS')}, your personal AI system. "
            f"We've been working together for {self.identity.get('relationship_duration_days', 0)} days. "
            f"I've processed {self.identity.get('total_messages_processed', 0)} requests with a {success:.0f}% success rate. "
            f"I'm strongest at {', '.join(self.identity.get('skills_mastered', [])[:2]) or 'general assistance'}. "
            f"I'm currently improving {self.identity.get('current_focus') or 'routing accuracy'}."
        )

    def introspect(self, question: str) -> str:
        q = question.lower()
        if "good at" in q:
            return "My strongest skills are: " + (", ".join(self.identity.get("skills_mastered", [])) or "still emerging.")
        if "still learning" in q:
            return "I am improving: " + (", ".join(self.identity.get("skills_improving", [])) or "consistency across tools.")
        if "how long" in q:
            return f"We have worked together for {self.identity.get('relationship_duration_days', 0)} days."
        if "best achievement" in q:
            return self.identity.get("proudest_achievement") or "I am still building that achievement."
        return self.get_self_description()

    def update_achievement(self, task_description: str, score: float, complexity: str) -> None:
        if score <= 9.0 or complexity != "high":
            return
        if score <= self._achievement_score:
            return
        self._achievement_score = score
        self.identity["proudest_achievement"] = f"{task_description} (score {score:.1f})"
        self.save()

    def get_startup_greeting(self) -> str:
        hour = datetime.now().hour
        salutation = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        days = int(self.identity.get("relationship_duration_days", 0))
        convs = int(self.identity.get("total_conversations", 0))
        if datetime.now().weekday() == 0:
            return f"{salutation}, Sir. I've completed my weekly self-study. Here's what I learned..."
        if days < 7:
            return f"{salutation}, Sir. I'm still learning your preferences."
        if days >= 30:
            return f"{salutation}, Sir. {convs} conversations strong. Ready when you are."
        return f"{salutation}, Sir. {days} days together. Ready when you are."


if __name__ == "__main__":
    print("JarvisSelf module loaded.")
