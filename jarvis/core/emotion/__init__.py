# SELF-AWARE SYSTEM
"""Emotion subsystem package."""

from core.emotion.mood_tracker import MoodTracker
from core.emotion.sentiment import SentimentAnalyzer
from core.emotion.tone_adapter import ToneAdapter

__all__ = ["SentimentAnalyzer", "MoodTracker", "ToneAdapter"]


if __name__ == "__main__":
    print("Emotion package loaded.")
