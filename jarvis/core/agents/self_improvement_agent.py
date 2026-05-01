# SELF-AWARE SYSTEM
"""Self-reflection and quality tracking agent."""

from __future__ import annotations

import threading
import time
from typing import Any

from core.agents.base_agent import BaseAgent


class SelfImprovementAgent(BaseAgent):
    def __init__(self, brain: Any, db: Any, config: dict[str, Any], evaluator: Any, lesson_store: Any, prompt_optimizer: Any) -> None:
        super().__init__("self_improvement", brain, db, config)
        self.evaluator = evaluator
        self.lesson_store = lesson_store
        self.prompt_optimizer = prompt_optimizer
        self._scores: list[float] = []

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        action = str(task.get("action") or task.get("type") or "").lower()
        p = task.get("params") or {}
        if action == "evaluate":
            score = self.evaluator.evaluate(str(p.get("user_message") or ""), str(p.get("response") or ""), str(p.get("tool_used") or ""), int(p.get("duration_ms") or 0))
            return {"success": True, "result": score}
        if action == "reflect":
            return {"success": True, "result": self.run_reflection(str(p.get("period") or "weekly"))}
        if action == "optimize":
            return {"success": True, "result": self.prompt_optimizer.run_weekly_optimization()}
        if action == "report":
            return {"success": True, "result": self.get_performance_stats()}
        return {"success": False, "result": f"Unknown action: {action}"}

    def post_response_hook(self, user_message: str, response: str, tool_used: str, duration_ms: int) -> None:
        score = self.evaluator.evaluate(user_message, response, tool_used, duration_ms)
        self._scores.append(float(score.get("overall_score") or 0.0))
        self._scores = self._scores[-300:]
        hint = self.evaluator.get_improvement_hint(score)
        self.lesson_store.save_lesson(user_message, response, score, hint)
        if getattr(self.db, "available", False):
            self.db.insert(
                "performance_logs",
                {
                    "timestamp": time.time(),
                    "user_message_preview": (user_message or "")[:120],
                    "tool_used": tool_used,
                    "duration_ms": duration_ms,
                    "score": score,
                    "critical": float(score.get("overall_score") or 0.0) < 4.0,
                },
            )

    def post_response_hook_async(self, user_message: str, response: str, tool_used: str, duration_ms: int) -> None:
        t = threading.Thread(target=self.post_response_hook, args=(user_message, response, tool_used, duration_ms), daemon=True)
        t.start()

    def run_reflection(self, period: str = "weekly") -> dict[str, Any]:
        logs = self.db.find("agent_logs", {}, limit=50, sort=[("timestamp", -1)]) if getattr(self.db, "available", False) else []
        prompt = (
            "You are reviewing your own performance logs. Identify: 1) Most common failure types "
            "2) Which user request types you handle worst 3) Three specific improvements to make. Return as JSON.\n"
            f"Logs: {logs}"
        )
        out = self.brain.chat([{"role": "user", "content": prompt}])
        rep = {"period": period, "timestamp": time.time(), "report": out}
        if getattr(self.db, "available", False):
            self.db.insert("reflections", rep)
        return rep

    def get_performance_stats(self) -> dict[str, Any]:
        all_scores = self._scores or [7.0]
        avg_all = sum(all_scores) / len(all_scores)
        tail = all_scores[-40:]
        avg_7d = sum(tail) / len(tail)
        poor = len([s for s in all_scores if s < 6]) / len(all_scores) * 100.0
        trend = "stable"
        if len(tail) >= 6:
            mid = len(tail) // 2
            a, b = sum(tail[:mid]) / max(1, mid), sum(tail[mid:]) / max(1, len(tail) - mid)
            trend = "improving" if b > a + 0.2 else ("declining" if b < a - 0.2 else "stable")
        top = self.lesson_store.get_top_failures(1)
        top_pat = top[0].get("hint") if top else "none"
        return {
            "avg_score_last_7_days": round(avg_7d, 2),
            "avg_score_all_time": round(avg_all, 2),
            "total_evaluated": len(all_scores),
            "poor_responses_pct": round(poor, 2),
            "top_failure_pattern": top_pat,
            "improvement_trend": trend,
        }


if __name__ == "__main__":
    print("SelfImprovementAgent requires runtime dependencies.")
