# SELF-AWARE SYSTEM
"""Prompt optimizations from stored lessons."""

from __future__ import annotations

import time
from typing import Any


class PromptOptimizer:
    def __init__(self, brain: Any, lesson_store: Any) -> None:
        self.brain = brain
        self.lesson_store = lesson_store

    def optimize_system_prompt(self, base_prompt: str, relevant_lessons: list[dict]) -> str:
        if not relevant_lessons:
            return base_prompt
        additions: list[str] = []
        for row in relevant_lessons[:5]:
            hint = str(row.get("hint") or "").strip()
            if not hint:
                continue
            additions.append(f"- {hint}")
        if not additions:
            return base_prompt
        addon = "Lessons learned:\n" + "\n".join(additions)
        words = addon.split()
        if len(words) > 300:
            addon = " ".join(words[:300])
        return f"{base_prompt}\n\n{addon}"

    def optimize_routing_prompt(self, base_routing_prompt: str, recent_failures: list[dict]) -> str:
        if not recent_failures:
            return base_routing_prompt
        lines = []
        for row in recent_failures[:5]:
            pat = row.get("user_message_pattern", "pattern")
            hint = row.get("hint", "choose better route")
            lines.append(f"- Failure pattern '{pat}': {hint}")
        return f"{base_routing_prompt}\n\nRouting failure patterns:\n" + "\n".join(lines)

    def run_weekly_optimization(self) -> dict[str, Any]:
        top = self.lesson_store.get_top_failures(10)
        worst = top[:3]
        if not worst:
            return {"improvements_made": 0, "sections_updated": [], "top_patterns_fixed": []}
        prompt = (
            "You are improving system prompts.\n"
            "Given these failures, propose improved text for: system_prompt, routing_prompt, synthesis_prompt.\n"
            "Return strict JSON with keys system_prompt, routing_prompt, synthesis_prompt.\n"
            f"Failures: {worst}"
        )
        raw = self.brain.chat([{"role": "user", "content": prompt}])
        doc = {"timestamp": time.time(), "raw": raw, "patterns": [x.get("user_message_pattern") for x in worst]}
        db = getattr(self.lesson_store, "db", None)
        if db is not None and getattr(db, "available", False):
            db.insert("optimized_prompts", doc)
        return {"improvements_made": 1, "sections_updated": ["system_prompt", "routing_prompt", "synthesis_prompt"], "top_patterns_fixed": doc["patterns"]}

    def load_optimized_prompts(self) -> dict[str, Any]:
        db = getattr(self.lesson_store, "db", None)
        if db is None or not getattr(db, "available", False):
            return {}
        rows = db.find("optimized_prompts", {}, limit=1, sort=[("timestamp", -1)])
        return rows[0] if rows else {}


if __name__ == "__main__":
    print("PromptOptimizer requires brain + lesson_store.")
