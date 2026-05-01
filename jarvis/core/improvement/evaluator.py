# SELF-AWARE SYSTEM
"""Heuristic response quality evaluator (<50ms)."""

from __future__ import annotations

import re
from typing import Any


class ResponseEvaluator:
    def _extract_keywords(self, text: str) -> set[str]:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", (text or "").lower())
        stop = {"what", "when", "where", "which", "would", "could", "should", "this", "that", "with", "from", "have", "your"}
        return {w for w in words if w not in stop}

    def evaluate(self, user_message: str, jarvis_response: str, tool_used: str, duration_ms: int) -> dict[str, Any]:
        user_k = self._extract_keywords(user_message)
        resp_low = (jarvis_response or "").lower()
        overlap = len([k for k in user_k if k in resp_low])
        relevance = max(1, min(10, 3 + overlap))

        qlen = len((user_message or "").split())
        rlen = len((jarvis_response or "").split())
        if qlen <= 8 and rlen > 140:
            completeness = 5
        elif qlen >= 18 and rlen < 40:
            completeness = 3
        else:
            completeness = 7 if rlen >= 15 else 5

        if duration_ms < 1000:
            speed = 10
        elif duration_ms < 3000:
            speed = 7
        elif duration_ms < 6000:
            speed = 5
        elif duration_ms > 10000:
            speed = 2
        else:
            speed = 4

        msg = (user_message or "").lower()
        t = (tool_used or "").lower()
        if any(k in msg for k in ["search", "latest", "news"]) and "search" not in t:
            tool_acc = 3
        elif any(k in msg for k in ["write code", "generate code", "python", "javascript"]) and "code" not in t:
            tool_acc = 3
        else:
            tool_acc = 9

        err_phrases = ["i'm sorry", "i cannot", "error occurred", "failed to"]
        penalty = sum(1 for p in err_phrases if p in resp_low)
        error_free = max(1, 10 - (penalty * 2))

        overall = (
            relevance * 0.35 + completeness * 0.25 + speed * 0.2 + tool_acc * 0.15 + error_free * 0.05
        )
        tier = "excellent" if overall >= 8 else ("good" if overall >= 6 else ("acceptable" if overall >= 4 else "poor"))
        return {
            "overall_score": round(overall, 2),
            "dimensions": {
                "relevance": relevance,
                "completeness": completeness,
                "speed": speed,
                "tool_accuracy": tool_acc,
                "error_free": error_free,
            },
            "quality_tier": tier,
            "needs_improvement": overall < 6,
        }

    def should_retry(self, score: dict[str, Any]) -> bool:
        return float(score.get("overall_score") or 0.0) < 4.0

    def get_improvement_hint(self, score: dict[str, Any]) -> str:
        dims = score.get("dimensions") or {}
        if dims.get("tool_accuracy", 10) <= 4:
            return "Wrong tool selected."
        if dims.get("completeness", 10) <= 4:
            return "Response was too short for complex question."
        if dims.get("speed", 10) <= 4:
            return "Response too slow — consider caching."
        if dims.get("relevance", 10) <= 4:
            return "Response did not address the core request."
        return "General quality dip — review phrasing and directness."


if __name__ == "__main__":
    ev = ResponseEvaluator()
    print(ev.evaluate("write python code", "Hello there", "direct", 1200))
