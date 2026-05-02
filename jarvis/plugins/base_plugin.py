"""Base class for all Jarvis plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BasePlugin(ABC):
    """Base class every plugin must inherit."""

    name: str = "unnamed"
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    commands: list[str] = []
    keywords: list[str] = []
    requires_config: list[str] = []
    enabled_by_default: bool = True

    def __init__(self, brain: Any = None, db: Any = None, config: dict[str, Any] | None = None, notifier: Any = None) -> None:
        self.brain = brain
        self.db = db
        self.config = dict(config or {})
        self.notifier = notifier
        self._call_count = 0
        self._error_count = 0

    @abstractmethod
    def execute(self, command: str, args: str, context: dict[str, Any]) -> dict[str, Any]:
        """Main plugin entry point."""
        raise NotImplementedError

    def on_load(self) -> bool:
        return True

    def on_unload(self) -> None:
        return None

    def health_check(self) -> dict[str, Any]:
        return {"healthy": True, "call_count": self._call_count, "error_count": self._error_count}

    def get_help(self) -> str:
        cmds = ", ".join(self.commands) if self.commands else "none"
        return f"{self.name} v{self.version}: {self.description} | Commands: {cmds}"

    def _success(self, response: str) -> dict[str, Any]:
        self._call_count += 1
        return {"success": True, "response": response, "error": None, "plugin": self.name}

    def _error(self, error: str) -> dict[str, Any]:
        self._error_count += 1
        return {"success": False, "response": f"Plugin error: {error}", "error": error, "plugin": self.name}


if __name__ == "__main__":
    print("BasePlugin loaded.")
