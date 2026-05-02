"""Learning subsystem for usage analytics, patterns, and suggestions."""

from core.learning.pattern_engine import PatternEngine
from core.learning.suggestion_engine import SuggestionEngine
from core.learning.usage_analyzer import UsageAnalyzer
from core.learning.weekly_learner import WeeklyLearner

__all__ = ["UsageAnalyzer", "PatternEngine", "SuggestionEngine", "WeeklyLearner"]


if __name__ == "__main__":
    print("Learning package loaded.")
