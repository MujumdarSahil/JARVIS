"""
Named routines with delays, optional schedule, YAML persistence.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

import schedule
import yaml

from utils.logger import get_logger

from .devices import DeviceManager

logger = get_logger(__name__)

BUILTIN_ROUTINES: dict[str, list[dict[str, Any]]] = {
    "good_morning": [
        {"command": "set all lights to 80%", "delay": 0.0},
        {"command": "set thermostat to 22", "delay": 1.0},
    ],
    "good_night": [
        {"command": "turn off all lights", "delay": 0.0},
        {"command": "lock doors", "delay": 0.5},
        {"command": "set thermostat to 18", "delay": 1.0},
    ],
    "movie_mode": [
        {"command": "set living room lights to 20%", "delay": 0.0},
        {"command": "turn off overhead lights", "delay": 0.5},
    ],
    "away_mode": [
        {"command": "turn off all lights", "delay": 0.0},
        {"command": "set thermostat to 16", "delay": 2.0},
    ],
}


class RoutineManager:
    def __init__(self, device_manager: DeviceManager, config: dict[str, Any], config_path: Path | None = None) -> None:
        self.dm = device_manager
        self.config = dict(config or {})
        sh = self.config.get("smarthome") or {}
        self._routines: list[dict[str, Any]] = []
        if isinstance(sh, dict):
            raw = sh.get("routines")
            if isinstance(raw, list):
                self._routines = [dict(x) for x in raw if isinstance(x, dict)]
        jarvis_root = Path(__file__).resolve().parent.parent.parent
        self._config_path = Path(config_path) if config_path else jarvis_root / "config.yaml"
        self._schedule_stop = threading.Event()
        self._schedule_thread: threading.Thread | None = None
        self._scheduled_jobs: dict[str, schedule.Job] = {}

    def _normalize_routine_name(self, name: str) -> str:
        raw = (name or "").strip().lower()
        raw = re.sub(r"\s+routine\s*$", "", raw).strip()
        return raw.replace(" ", "_")

    def _actions_for(self, name: str) -> list[dict[str, Any]]:
        key = self._normalize_routine_name(name)
        for r in self._routines:
            rn = self._normalize_routine_name(str(r.get("name", "")))
            if rn == key:
                acts = r.get("actions")
                if isinstance(acts, list):
                    return [dict(a) for a in acts if isinstance(a, dict)]
        if key in BUILTIN_ROUTINES:
            return [dict(x) for x in BUILTIN_ROUTINES[key]]
        for bk, acts in BUILTIN_ROUTINES.items():
            if bk.replace("_", " ") == key.replace("_", " "):
                return [dict(x) for x in acts]
        return []

    def run_routine(self, name: str) -> dict[str, Any]:
        try:
            actions = self._actions_for(name)
            if not actions:
                return {"success": False, "data": None, "error": f"Unknown routine: {name}"}
            results: list[dict[str, Any]] = []
            for step in actions:
                delay = float(step.get("delay", 0) or 0)
                if delay > 0:
                    time.sleep(delay)
                cmd = str(step.get("command", "")).strip()
                if not cmd:
                    continue
                results.append(self.dm.control(cmd))
            return {
                "success": all(x.get("success") for x in results) if results else True,
                "data": {"routine": name, "steps": results},
                "error": None,
            }
        except Exception as e:
            logger.exception("run_routine: %s", e)
            return {"success": False, "data": None, "error": str(e)}

    def list_routines(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for r in self._routines:
            n = str(r.get("name", "")).strip()
            if n:
                names.append(n)
                seen.add(n.lower().replace(" ", "_"))
        for k in BUILTIN_ROUTINES:
            if k not in seen:
                names.append(k)
        return names

    def describe_routines(self) -> list[dict[str, Any]]:
        return [{"name": n, "steps": len(self._actions_for(n))} for n in self.list_routines()]

    def add_routine(self, name: str, actions: list[Any]) -> dict[str, Any]:
        try:
            if not name or not str(name).strip():
                return {"success": False, "data": None, "error": "name required"}
            if not isinstance(actions, list):
                return {"success": False, "data": None, "error": "actions must be a list"}
            new_entry = {"name": str(name).strip(), "actions": actions}
            replaced = False
            for i, r in enumerate(self._routines):
                if str(r.get("name", "")).lower() == new_entry["name"].lower():
                    self._routines[i] = new_entry
                    replaced = True
                    break
            if not replaced:
                self._routines.append(new_entry)
            err = self._persist_routines_yaml()
            if err:
                return {"success": False, "data": None, "error": err}
            return {"success": True, "data": new_entry, "error": None}
        except Exception as e:
            logger.exception("add_routine: %s", e)
            return {"success": False, "data": None, "error": str(e)}

    def delete_routine(self, name: str) -> dict[str, Any]:
        try:
            key = (name or "").strip().lower()
            before = len(self._routines)
            self._routines = [r for r in self._routines if str(r.get("name", "")).strip().lower() != key]
            if len(self._routines) == before:
                return {"success": False, "data": None, "error": f"Routine not found: {name}"}
            err = self._persist_routines_yaml()
            if err:
                return {"success": False, "data": None, "error": err}
            return {"success": True, "data": {"removed": name}, "error": None}
        except Exception as e:
            logger.exception("delete_routine: %s", e)
            return {"success": False, "data": None, "error": str(e)}

    def _persist_routines_yaml(self) -> str | None:
        try:
            path = self._config_path
            if not path.is_file():
                return f"Config file not found: {path}"
            text = path.read_text(encoding="utf-8")
            data = yaml.safe_load(text) or {}
            if "yamlsmarthome" in data:
                key = "yamlsmarthome"
            elif "smarthome" in data:
                key = "smarthome"
            else:
                key = "yamlsmarthome"
            if key not in data:
                data[key] = {}
            if not isinstance(data[key], dict):
                data[key] = {}
            data[key]["routines"] = self._routines
            path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True), encoding="utf-8")
            self.config.setdefault(key, {})
            if isinstance(self.config.get(key), dict):
                self.config[key]["routines"] = self._routines
            return None
        except Exception as e:
            logger.exception("persist routines: %s", e)
            return str(e)

    def schedule_routine(self, name: str, time_str: str) -> dict[str, Any]:
        try:
            t = (time_str or "").strip()
            if not t:
                return {"success": False, "data": None, "error": "time_str required"}
            key = f"{name}@{t}"

            def _job() -> None:
                try:
                    self.run_routine(name)
                except Exception as e:
                    logger.exception("scheduled routine failed: %s", e)

            old = self._scheduled_jobs.pop(key, None)
            if old is not None:
                try:
                    schedule.cancel_job(old)
                except Exception:
                    pass
            job = schedule.every().day.at(t).do(_job)
            self._scheduled_jobs[key] = job
            if self._schedule_thread is None or not self._schedule_thread.is_alive():
                self._schedule_stop.clear()
                self._schedule_thread = threading.Thread(target=self._schedule_loop, name="routine-schedule", daemon=True)
                self._schedule_thread.start()
            return {"success": True, "data": {"name": name, "time": t}, "error": None}
        except Exception as e:
            logger.exception("schedule_routine: %s", e)
            return {"success": False, "data": None, "error": str(e)}

    def _schedule_loop(self) -> None:
        while not self._schedule_stop.wait(timeout=30.0):
            try:
                schedule.run_pending()
            except Exception as e:
                logger.warning("schedule.run_pending: %s", e)


if __name__ == "__main__":
    dm = DeviceManager(config={})
    rm = RoutineManager(dm, {"smarthome": {"routines": []}})
    print("routines:", rm.list_routines())
