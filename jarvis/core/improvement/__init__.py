# SELF-AWARE SYSTEM
"""Self-improvement subsystem package."""

from core.improvement.evaluator import ResponseEvaluator
from core.improvement.lesson_store import LessonStore
from core.improvement.prompt_optimizer import PromptOptimizer

__all__ = ["ResponseEvaluator", "LessonStore", "PromptOptimizer"]


if __name__ == "__main__":
    print("Improvement package loaded.")
