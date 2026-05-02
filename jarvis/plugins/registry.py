"""Plugin registry and lifecycle manager."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

from plugins.base_plugin import BasePlugin
from plugins.loader import PluginLoader
from utils.logger import get_logger

logger = get_logger(__name__)


class PluginRegistry:
    def __init__(self, loader: PluginLoader) -> None:
        self.loader = loader
        self.plugins: dict[str, BasePlugin] = {}
        self.command_map: dict[str, BasePlugin] = {}
        self.keyword_map: dict[str, BasePlugin] = {}
        self._enabled: dict[str, bool] = {}
        self._timeout_seconds = 10
        self._lock = threading.Lock()

    def initialize(self) -> int:
        self.plugins = self.loader.load_all()
        self._rebuild_maps()
        return len(self.plugins)

    def _rebuild_maps(self) -> None:
        self.command_map = {}
        self.keyword_map = {}
        disabled = set((self.loader.config.get("plugins") or {}).get("disabled") or [])
        for name, plugin in self.plugins.items():
            enabled = plugin.enabled_by_default and name not in disabled
            self._enabled[name] = enabled
            for cmd in plugin.commands:
                self.command_map[cmd.strip().lower()] = plugin
            for kw in plugin.keywords:
                self.keyword_map[kw.strip().lower()] = plugin

    def match_keywords(self, user_message: str) -> BasePlugin | None:
        low = (user_message or "").lower()
        for kw, plugin in self.keyword_map.items():
            if kw and kw in low and self._enabled.get(plugin.name, True):
                return plugin
        return None

    def _execute_with_timeout(self, plugin: BasePlugin, command: str, args: str, context: dict[str, Any]) -> dict[str, Any]:
        # Isolate plugin crashes/timeouts from Jarvis.
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(plugin.execute, command, args, context)
                return fut.result(timeout=self._timeout_seconds)
        except FutureTimeout:
            return plugin._error(f"timeout after {self._timeout_seconds}s")
        except Exception as e:
            return plugin._error(str(e))

    def route(self, command: str, args: str, context: dict[str, Any]) -> dict[str, Any] | None:
        if not command:
            return None
        key = command.strip().lower()
        plugin = self.command_map.get(key)
        if plugin is None:
            plugin = self.match_keywords(command)
        if plugin is None:
            return None
        if not self._enabled.get(plugin.name, True):
            return None
        return self._execute_with_timeout(plugin, command, args, context)

    def get_plugin(self, name: str) -> BasePlugin | None:
        return self.plugins.get(name)

    def list_plugins(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, plugin in self.plugins.items():
            out.append(
                {
                    "name": name,
                    "version": plugin.version,
                    "description": plugin.description,
                    "commands": list(plugin.commands),
                    "enabled": self._enabled.get(name, True),
                    "health": plugin.health_check(),
                }
            )
        return out

    def _persist_disabled(self) -> None:
        cfg = self.loader.config.setdefault("plugins", {})
        cfg["disabled"] = sorted([n for n, v in self._enabled.items() if not v])

    def enable_plugin(self, name: str) -> bool:
        if name not in self.plugins:
            return False
        self._enabled[name] = True
        self._persist_disabled()
        return True

    def disable_plugin(self, name: str) -> bool:
        if name not in self.plugins:
            return False
        self._enabled[name] = False
        self._persist_disabled()
        return True

    def reload_plugin(self, name: str) -> bool:
        with self._lock:
            old = self.plugins.get(name)
            if old is not None:
                try:
                    old.on_unload()
                except Exception:
                    pass
            fresh = self.loader.reload_plugin(name)
            if fresh is None:
                return False
            self.plugins[name] = fresh
            self._rebuild_maps()
            return True

    def get_all_commands(self) -> list[str]:
        return sorted(self.command_map.keys())

    def get_help_text(self) -> str:
        lines = ["Plugin Commands:"]
        for p in self.plugins.values():
            if self._enabled.get(p.name, True):
                lines.append(f"- {p.get_help()}")
        return "\n".join(lines)


if __name__ == "__main__":
    from plugins.loader import PluginLoader

    reg = PluginRegistry(PluginLoader())
    print("Loaded:", reg.initialize())
