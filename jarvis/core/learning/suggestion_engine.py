from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


class SuggestionEngine:
    def __init__(self, pattern_engine: Any, personal_kb: Any, db: Any, brain: Any, config: dict[str, Any] | None = None) -> None:
        self.pattern_engine = pattern_engine
        self.personal_kb = personal_kb
        self.db = db
        self.brain = brain
        cfg = dict(config or {})
        self.max_suggestions_per_session = int(cfg.get("max_suggestions_per_session", 2))
        self.cooldown_minutes = int(cfg.get("suggestion_cooldown_minutes", 30))
        self._shown_this_session = 0
        self._last_suggestion_at: datetime | None = None
        self._last_suggestion: dict[str, Any] | None = None
        self._stats = {"total_shown": 0, "accepted": 0, "dismissed": 0}

    def _mk(self, suggestion: str, trigger: str, action: str, priority: int = 3, expires_min: int = 30) -> dict[str, Any]:
        sid = f"sug-{int(datetime.now().timestamp())}-{abs(hash(suggestion)) % 10000}"
        return {
            "suggestion_id": sid,
            "suggestion": suggestion,
            "trigger": trigger,
            "action": action,
            "priority": max(1, min(5, priority)),
            "expires_at": (datetime.now() + timedelta(minutes=expires_min)).timestamp(),
        }

    def should_surface_suggestion(self, suggestion: dict) -> bool:
        if not suggestion:
            return False
        if self._shown_this_session >= self.max_suggestions_per_session:
            return False
        if self._last_suggestion_at is not None:
            delta = datetime.now() - self._last_suggestion_at
            if delta.total_seconds() < self.cooldown_minutes * 60:
                return False
        if float(suggestion.get("expires_at") or 0) < datetime.now().timestamp():
            return False
        return True

    def get_suggestions(self, context: dict[str, Any]) -> list[dict[str, Any]]:
        now = datetime.now()
        out: list[dict[str, Any]] = []
        patterns = self.pattern_engine.get_actionable_patterns()
        for p in patterns:
            desc = str(p.get("description", "")).lower()
            conf = float(p.get("confidence") or 0)
            if now.hour in range(8, 11) and ("active around 09" in desc or "morning" in desc):
                out.append(self._mk("You usually check markets around this time - want today's summary?", "Time-based usage pattern", "finance.market_summary", 5))
            if "code" in desc:
                out.append(self._mk("Want me to preload a focused coding workspace checklist?", "Coding preference pattern", "direct.coding_focus", 3))
            if conf > 0.85 and "search responses" in desc:
                out.append(self._mk("Need a quick curated brief of top updates right now?", "Search satisfaction pattern", "search.web_search", 2))

        if self.personal_kb is not None:
            goals = self.personal_kb.get("goals")
            for g in goals[:5]:
                dl = str(g.get("deadline") or "").strip()
                if dl and any(x in dl.lower() for x in ("week", "monday", "soon", "2026")):
                    out.append(self._mk(f"Goal check: '{g.get('goal', 'your goal')}' - want a progress update?", "Goal with approaching deadline", "kb.goal_review", 4))
                    break

        if context.get("unread_emails", 0) and int(context.get("unread_emails", 0)) > 10:
            n = int(context.get("unread_emails", 0))
            out.append(self._mk(f"You have {n} unread emails. Want a quick summary?", "Unread inbox threshold", "gmail.read_inbox", 4))

        if context.get("pending_tasks", 0) and int(context.get("pending_tasks", 0)) > 0:
            out.append(self._mk("You have pending scheduled tasks. Want me to show them?", "Pending scheduled tasks", "autonomy.list_tasks", 3))

        if context.get("rain_expected") is True:
            out.append(self._mk("Rain is expected today - consider carrying an umbrella.", "Weather forecast alert", "weather.daily", 2))

        budget_ratio = float(context.get("budget_ratio", 0))
        month_progress = float(context.get("month_progress", 0))
        if month_progress >= 0.8 and budget_ratio <= 0.7:
            out.append(self._mk("You're on track with budget this month. Want a spending summary?", "Budget progress trigger", "finance.budget_status", 2))

        out.sort(key=lambda s: int(s.get("priority", 3)), reverse=True)
        return out

    def format_suggestion_for_cli(self, suggestion: dict[str, Any]) -> str:
        return f"\n[cyan]💡 {suggestion.get('suggestion', '')}[/cyan]"

    def format_suggestion_for_web(self, suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cards = []
        for s in suggestions:
            cards.append({
                "id": s.get("suggestion_id"),
                "title": "Proactive Suggestion",
                "text": s.get("suggestion"),
                "trigger": s.get("trigger"),
                "action": s.get("action"),
                "expires_at": s.get("expires_at"),
                "priority": s.get("priority"),
            })
        return cards

    def mark_shown(self, suggestion: dict[str, Any]) -> None:
        self._shown_this_session += 1
        self._last_suggestion_at = datetime.now()
        self._last_suggestion = suggestion
        self._stats["total_shown"] += 1

    def mark_accepted(self, suggestion_id: str) -> None:
        _ = suggestion_id
        self._stats["accepted"] += 1

    def mark_dismissed(self, suggestion_id: str) -> None:
        _ = suggestion_id
        self._stats["dismissed"] += 1

    def get_suggestion_stats(self) -> dict[str, Any]:
        shown = self._stats["total_shown"]
        accepted = self._stats["accepted"]
        return {
            **self._stats,
            "acceptance_rate": round((accepted / shown), 3) if shown else 0.0,
        }

    def get_last_suggestion(self) -> dict[str, Any] | None:
        return self._last_suggestion


if __name__ == "__main__":
    print("SuggestionEngine module loaded.")
