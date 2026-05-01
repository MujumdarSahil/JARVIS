# SELF-AWARE SYSTEM
"""Keyword-based sentiment analysis without external ML."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


class SentimentAnalyzer:
    def __init__(self) -> None:
        self.keywords: dict[str, list[str]] = {
            "frustrated": [
                "not working",
                "nothing is working",
                "frustrated",
                "broken",
                "useless",
                "wrong",
                "stupid",
                "again",
                "still",
                "why won't",
                "doesn't work",
                "!!",
            ],
            "excited": ["amazing", "great", "awesome", "wow", "fantastic", "love it", "perfect", "yes!", "finally"],
            "confused": ["what", "don't understand", "confused", "how does", "explain", "what do you mean", "unclear"],
            "urgent": ["asap", "urgent", "immediately", "now", "hurry", "quick", "emergency", "right now"],
            "positive": ["thanks", "thank you", "good", "nice", "helpful", "excellent", "well done"],
            "negative": ["bad", "terrible", "awful", "hate", "worst", "disappointed", "useless"],
        }
        self.priority = ["urgent", "frustrated", "confused", "excited", "negative", "positive", "neutral"]

    def analyze(self, text: str) -> dict[str, Any]:
        raw = text or ""
        low = raw.lower()
        scores: dict[str, float] = defaultdict(float)
        signals: list[str] = []

        for sentiment, words in self.keywords.items():
            for w in words:
                hits = low.count(w.lower())
                if hits > 0:
                    scores[sentiment] += float(hits)
                    signals.extend([w] * hits)

        caps_bonus = 0.3 if any(len(t) > 1 and t.isupper() for t in re.findall(r"\b\w+\b", raw)) else 0.0
        exclaim_bonus = min(raw.count("!") * 0.1, 0.6)
        intensity_boost = caps_bonus + exclaim_bonus
        for key in list(scores.keys()):
            scores[key] += intensity_boost

        dominant = "neutral"
        if scores:
            ordered = sorted(scores.items(), key=lambda kv: (kv[1], -self.priority.index(kv[0]) if kv[0] in self.priority else -99), reverse=True)
            dominant = ordered[0][0]

        total = sum(scores.values())
        conf = (scores.get(dominant, 0.0) / total) if total > 0 else 0.5
        if dominant == "neutral":
            conf = 0.8 if raw.strip() else 0.5

        intensity_score = scores.get(dominant, 0.0) + exclaim_bonus + caps_bonus
        if intensity_score < 1.5:
            intensity = "low"
        elif intensity_score < 3.5:
            intensity = "medium"
        else:
            intensity = "high"

        return {
            "sentiment": dominant,
            "confidence": round(max(0.0, min(conf, 1.0)), 3),
            "signals": sorted(set(signals))[:12],
            "intensity": intensity,
        }

    def analyze_conversation_trend(self, messages: list[str]) -> dict[str, Any]:
        sample = (messages or [])[-5:]
        if not sample:
            return {"trending": "stable", "average_sentiment": "neutral", "mood_shift": "none"}

        mapped: list[str] = [self.analyze(m).get("sentiment", "neutral") for m in sample]
        polarity = {"positive": 1, "excited": 1, "neutral": 0, "confused": -1, "negative": -1, "frustrated": -2, "urgent": -1}
        vals = [polarity.get(s, 0) for s in mapped]
        delta = vals[-1] - vals[0]
        trending = "stable"
        if delta > 0:
            trending = "improving"
        elif delta < 0:
            trending = "declining"

        avg_val = sum(vals) / len(vals)
        avg_sent = "neutral"
        if avg_val >= 0.6:
            avg_sent = "positive"
        elif avg_val <= -0.8:
            avg_sent = "frustrated"
        elif avg_val <= -0.2:
            avg_sent = "negative"

        return {"trending": trending, "average_sentiment": avg_sent, "mood_shift": f"{mapped[0]} -> {mapped[-1]}"}


if __name__ == "__main__":
    analyzer = SentimentAnalyzer()
    test = "i am really frustrated nothing is working today"
    result = analyzer.analyze(test)
    print(result)
    assert result["sentiment"] == "frustrated", f"Expected frustrated, got {result['sentiment']}"
