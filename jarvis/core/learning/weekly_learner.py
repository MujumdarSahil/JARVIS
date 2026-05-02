from __future__ import annotations

from datetime import datetime
import threading
from typing import Any


class WeeklyLearner:
    def __init__(
        self,
        brain: Any,
        db: Any,
        usage_analyzer: Any,
        pattern_engine: Any,
        suggestion_engine: Any,
        lesson_store: Any,
        prompt_optimizer: Any,
        personal_kb: Any,
    ) -> None:
        self.brain = brain
        self.db = db
        self.usage_analyzer = usage_analyzer
        self.pattern_engine = pattern_engine
        self.suggestion_engine = suggestion_engine
        self.lesson_store = lesson_store
        self.prompt_optimizer = prompt_optimizer
        self.personal_kb = personal_kb
        self.scheduler = None

    def _save_report(self, report: dict[str, Any]) -> None:
        if getattr(self.db, "available", False):
            self.db.insert("weekly_reports", report)

    def run_weekly_study(self) -> dict[str, Any]:
        usage = self.usage_analyzer.analyze_usage(days=7)
        patterns = self.pattern_engine.find_patterns()
        failures = self.lesson_store.get_top_failures(10) if self.lesson_store else []
        prompt_update = self.prompt_optimizer.run_weekly_optimization() if self.prompt_optimizer else {}
        kb_enrich = {"success": False}
        if self.personal_kb is not None and getattr(self.db, "available", False):
            convs = self.db.find("conversations", {}, limit=500, sort=[("timestamp", -1)])
            kb_enrich = self.personal_kb.extract_from_conversation(convs[:100])

        reflection_prompt = (
            "Here is your usage data for this week: "
            f"{usage}. What should you do differently? What are you doing well? "
            "Return JSON with keys: improvements, strengths, focus_for_next_week."
        )
        raw_reflection = self.brain.chat([{"role": "user", "content": reflection_prompt}])

        report = {
            "created_at": datetime.now().timestamp(),
            "week_key": datetime.now().strftime("%Y-W%W"),
            "usage_report": usage,
            "patterns_found": patterns,
            "lessons_learned": failures,
            "prompt_improvement": prompt_update,
            "kb_enrichment": kb_enrich,
            "self_reflection_raw": raw_reflection,
        }
        self._save_report(report)

        tool_perf = usage.get("tool_performance", {})
        weak = [t for t, m in tool_perf.items() if float(m.get("success_rate", 1)) < 0.6]
        if weak:
            report["routing_adjustment_hint"] = f"Lower routing priority for unstable tools: {', '.join(weak[:5])}"

        return {
            "week_summary": f"{len(patterns)} patterns found, {len(failures)} key lessons identified.",
            "patterns_found": patterns,
            "lessons_learned": failures,
            "improvements_planned": prompt_update,
        }

    def _run_in_background(self) -> None:
        threading.Thread(target=self.run_weekly_study, daemon=True, name="jarvis-weekly-study").start()

    def get_weekly_report(self, week_offset: int = 0) -> dict[str, Any]:
        if not getattr(self.db, "available", False):
            return {}
        rows = self.db.find("weekly_reports", {}, limit=max(1, week_offset + 1), sort=[("created_at", -1)])
        if week_offset >= len(rows):
            return {}
        return dict(rows[week_offset])

    def get_improvement_trajectory(self) -> dict[str, Any]:
        if not getattr(self.db, "available", False):
            return {"weeks": [], "scores": [], "trend": "unknown"}
        rows = self.db.find("weekly_reports", {}, limit=12, sort=[("created_at", 1)])
        weeks = []
        scores = []
        for r in rows:
            weeks.append(str(r.get("week_key", "?")))
            usage = r.get("usage_report", {}) or {}
            perf = usage.get("tool_performance", {}) or {}
            vals = [float(v.get("success_rate", 0)) for v in perf.values() if isinstance(v, dict)]
            scores.append(round((sum(vals) / len(vals) * 100.0), 2) if vals else 0.0)
        trend = "stable"
        if len(scores) >= 2:
            trend = "up" if scores[-1] > scores[-2] else "down" if scores[-1] < scores[-2] else "stable"
        return {"weeks": weeks, "scores": scores, "trend": trend}

    def schedule_weekly_study(self) -> None:
        if self.scheduler is None:
            return
        try:
            self.scheduler.add_task(
                name="weekly_learning_loop",
                action="weekly_learning",
                params={},
                schedule_str="every monday at 07:00",
            )
        except Exception:
            pass


if __name__ == "__main__":
    print("WeeklyLearner module loaded.")
