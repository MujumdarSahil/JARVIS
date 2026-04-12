"""
Smart home: Home Assistant, MQTT, device NL control, routines.
"""

from __future__ import annotations

from typing import Any

from .devices import DeviceManager
from .homeassistant import HomeAssistantClient
from .mqtt import MQTTClient
from .routines import RoutineManager

__all__ = [
    "DeviceManager",
    "HomeAssistantClient",
    "MQTTClient",
    "RoutineManager",
    "SmarthomeSkill",
]


class SmarthomeSkill:
    """Tool-facing facade for ToolRegistry (device + routine operations)."""

    def __init__(
        self,
        device_manager: DeviceManager | None,
        routine_manager: RoutineManager | None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.dm = device_manager
        self.rm = routine_manager
        self.config: dict[str, Any] = dict(config or {})

    def _need_dm(self) -> dict[str, Any] | None:
        if self.dm is None:
            return {
                "success": False,
                "result": "",
                "error": "Smart home is disabled or not configured. Enable smarthome in config.yaml and set Home Assistant URL and token.",
            }
        return None

    def control_device(self, command: str = "", **kwargs: Any) -> dict[str, Any]:
        err = self._need_dm()
        if err:
            return err
        cmd = str(command or kwargs.get("command") or "").strip()
        r = self.dm.control(cmd)
        return {
            "success": bool(r.get("success")),
            "result": r,
            "error": r.get("error"),
        }

    def get_status(self, **kwargs: Any) -> dict[str, Any]:
        err = self._need_dm()
        if err:
            return err
        text = self.dm.get_all_status()
        ok = not text.startswith("Smart home not configured") and not text.startswith("Could not reach")
        return {"success": ok, "result": text, "data": text, "error": None if ok else text}

    def run_routine(self, name: str = "", **kwargs: Any) -> dict[str, Any]:
        err = self._need_dm()
        if err:
            return err
        if self.rm is None:
            return {"success": False, "result": "", "error": "Routine manager not available."}
        n = str(name or kwargs.get("name") or "").strip()
        r = self.rm.run_routine(n)
        return {
            "success": bool(r.get("success")),
            "result": r.get("data"),
            "error": r.get("error"),
        }

    def list_devices(self, **kwargs: Any) -> dict[str, Any]:
        err = self._need_dm()
        if err:
            return err
        r = self.dm.discover_devices()
        return {
            "success": bool(r.get("success")),
            "result": r.get("data"),
            "data": r.get("data"),
            "error": r.get("error"),
        }

    def get_room_status(self, room: str = "", **kwargs: Any) -> dict[str, Any]:
        err = self._need_dm()
        if err:
            return err
        rm = str(room or kwargs.get("room") or "").strip()
        if not rm:
            return {"success": False, "result": "", "error": "room parameter is required"}
        r = self.dm.get_room_status(rm)
        return {
            "success": bool(r.get("success")),
            "result": r.get("data"),
            "data": r.get("data"),
            "error": r.get("error"),
        }


if __name__ == "__main__":
    s = SmarthomeSkill(None, None)
    print(s.control_device("test"))
