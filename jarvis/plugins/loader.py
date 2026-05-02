"""Plugin auto-discovery and loading."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

from plugins.base_plugin import BasePlugin
from utils.logger import get_logger

logger = get_logger(__name__)


class PluginLoader:
    def __init__(
        self,
        plugins_dir: str = "plugins",
        brain: Any = None,
        db: Any = None,
        config: dict[str, Any] | None = None,
        notifier: Any = None,
    ) -> None:
        self.brain = brain
        self.db = db
        self.config = dict(config or {})
        self.notifier = notifier
        root = Path(__file__).resolve().parent.parent
        cfg = self.config.get("plugins") or {}
        base_dir = root / plugins_dir
        self.builtin_dir = root / str(cfg.get("builtin_dir") or str(base_dir / "builtin"))
        self.custom_dir = root / str(cfg.get("custom_dir") or str(base_dir / "custom"))
        self._module_map: dict[str, str] = {}
        self._class_map: dict[str, type[BasePlugin]] = {}

    def discover(self) -> list[type[BasePlugin]]:
        classes: list[type[BasePlugin]] = []
        for folder in (self.builtin_dir, self.custom_dir):
            if not folder.exists():
                continue
            for py_file in sorted(folder.glob("*.py")):
                stem = py_file.stem.lower()
                if stem.startswith("_") or stem.startswith("base_"):
                    continue
                module_name = f"jarvis_plugin_{folder.name}_{py_file.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec is None or spec.loader is None:
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = mod
                    spec.loader.exec_module(mod)
                    for _, obj in inspect.getmembers(mod, inspect.isclass):
                        if obj is BasePlugin or not issubclass(obj, BasePlugin):
                            continue
                        classes.append(obj)
                        self._module_map[obj.name] = module_name
                        self._class_map[obj.name] = obj
                except Exception as e:
                    logger.warning("[PLUGIN] Failed to load %s: %s", py_file.name, e)
        return classes

    def load_plugin(self, plugin_class: type[BasePlugin]) -> BasePlugin | None:
        try:
            plugin = plugin_class(brain=self.brain, db=self.db, config=self.config, notifier=self.notifier)
            if not plugin.on_load():
                logger.warning("[PLUGIN] Failed to load %s: on_load returned False", plugin_class.__name__)
                return None
            logger.info("[PLUGIN] Loaded: %s v%s — %s", plugin.name, plugin.version, plugin.description)
            return plugin
        except Exception as e:
            logger.warning("[PLUGIN] Failed to load %s: %s", plugin_class.__name__, e)
            return None

    def load_all(self) -> dict[str, BasePlugin]:
        loaded: dict[str, BasePlugin] = {}
        for cls in self.discover():
            plugin = self.load_plugin(cls)
            if plugin is not None:
                loaded[plugin.name] = plugin
        return loaded

    def reload_plugin(self, name: str) -> BasePlugin | None:
        cls = self._class_map.get(name)
        module_name = self._module_map.get(name)
        if cls is None or module_name is None:
            return None
        try:
            module = sys.modules.get(module_name)
            if module is None:
                return None
            module = importlib.reload(module)
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if obj is not BasePlugin and issubclass(obj, BasePlugin) and getattr(obj, "name", "") == name:
                    self._class_map[name] = obj
                    return self.load_plugin(obj)
        except Exception as e:
            logger.warning("[PLUGIN] Failed to reload %s: %s", name, e)
        return None


if __name__ == "__main__":
    loader = PluginLoader()
    print([c.__name__ for c in loader.discover()])
