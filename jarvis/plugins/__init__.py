"""Jarvis plugin package."""

from plugins.base_plugin import BasePlugin
from plugins.loader import PluginLoader
from plugins.registry import PluginRegistry

__all__ = ["BasePlugin", "PluginLoader", "PluginRegistry"]


if __name__ == "__main__":
    print("plugins package ready")
