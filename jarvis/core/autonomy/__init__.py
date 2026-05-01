# SELF-AWARE SYSTEM
"""Autonomy subsystem package."""

from core.autonomy.monitor import ProactiveMonitor
from core.autonomy.reporter import ActivityReporter
from core.autonomy.scheduler import TaskScheduler

__all__ = ["TaskScheduler", "ProactiveMonitor", "ActivityReporter"]


if __name__ == "__main__":
    print("Autonomy package loaded.")
