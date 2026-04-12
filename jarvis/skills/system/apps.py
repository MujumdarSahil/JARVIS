"""
Launch applications and URLs; persist extra aliases in config.yaml.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain

logger = get_logger(__name__)

_DEFAULT_ALIASES: dict[str, str] = {
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "notepad": "notepad.exe",
    "vscode": "code",
    "explorer": "explorer.exe",
    "calculator": "calc.exe",
    "spotify": "spotify.exe",
}


def _ok(success: bool, result: str = "", error: str | None = None) -> dict[str, Any]:
    return {"success": success, "result": result, "error": error}


class AppSkill:
    """Open apps and URLs; manage aliases stored under ``apps.aliases`` in config."""

    def __init__(
        self,
        config_path: str | Path,
        brain: Brain | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        self._brain = brain

    def _load_yaml(self) -> dict[str, Any]:
        try:
            text = self._config_path.read_text(encoding="utf-8")
            return yaml.safe_load(text) or {}
        except (OSError, yaml.YAMLError) as e:
            logger.warning("AppSkill: could not load config: %s", e)
            return {}

    def _save_yaml(self, data: dict[str, Any]) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            yaml.safe_dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def _merged_aliases(self) -> dict[str, str]:
        data = self._load_yaml()
        apps = data.get("apps") or {}
        user_aliases = apps.get("aliases") or {}
        if not isinstance(user_aliases, dict):
            user_aliases = {}
        merged = dict(_DEFAULT_ALIASES)
        for k, v in user_aliases.items():
            merged[str(k).lower()] = str(v)
        return merged

    def get_app_aliases(self) -> dict[str, Any]:
        try:
            aliases = self._merged_aliases()
            return _ok(True, json.dumps(aliases, indent=2, ensure_ascii=False), None)
        except Exception as e:
            logger.exception("get_app_aliases failed: %s", e)
            return _ok(False, "", str(e))

    def open_app(self, name: str) -> dict[str, Any]:
        try:
            raw = (name or "").strip()
            if not raw:
                return _ok(False, "", "name required")
            key = raw.lower()
            aliases = self._merged_aliases()
            target = aliases[key] if key in aliases else raw
            logger.info("OPEN_APP name=%s target=%s", raw, target)
            tp = Path(target)
            if tp.is_file():
                subprocess.Popen([str(tp)], shell=False)  # noqa: S603
                return _ok(True, f"Launched: {target}", None)
            try:
                subprocess.Popen(target, shell=True)  # noqa: S603, S607
                return _ok(True, f"Launched: {target}", None)
            except Exception:
                pass
            if platform.system() == "Windows":
                os.startfile(target)  # noqa: S606
                return _ok(True, f"Started via os.startfile: {target}", None)
            return _ok(False, "", f"Could not launch: {target}")
        except Exception as e:
            logger.exception("open_app failed: %s", e)
            return _ok(False, "", str(e))

    def open_url(self, url: str, browser: str | None = None) -> dict[str, Any]:
        try:
            u = (url or "").strip()
            if not u:
                return _ok(False, "", "url required")
            if browser:
                try:
                    webbrowser.get(browser).open(u)
                except webbrowser.Error:
                    subprocess.Popen([browser, u], shell=False)  # noqa: S603
            else:
                webbrowser.open(u)
            return _ok(True, f"Opened URL: {u}", None)
        except Exception as e:
            logger.exception("open_url failed: %s", e)
            return _ok(False, "", str(e))

    def add_alias(self, name: str, path: str) -> dict[str, Any]:
        try:
            key = (name or "").strip().lower()
            val = (path or "").strip()
            if not key or not val:
                return _ok(False, "", "name and path required")
            data = self._load_yaml()
            apps = data.get("apps")
            if not isinstance(apps, dict):
                apps = {}
                data["apps"] = apps
            aliases = apps.get("aliases")
            if not isinstance(aliases, dict):
                aliases = {}
                apps["aliases"] = aliases
            aliases[key] = val
            self._save_yaml(data)
            if self._brain is not None:
                self._brain.reload_config()
            return _ok(True, f"Saved alias {key} -> {val}", None)
        except Exception as e:
            logger.exception("add_alias failed: %s", e)
            return _ok(False, "", str(e))


if __name__ == "__main__":
    p = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    a = AppSkill(p)
    print(a.get_app_aliases())
