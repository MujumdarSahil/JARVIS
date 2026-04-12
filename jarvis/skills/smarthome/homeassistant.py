"""
Home Assistant REST API client (requests, 5s timeout).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

HA_TIMEOUT = 5.0


def _ok(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def _err(msg: str, data: Any = None) -> dict[str, Any]:
    return {"success": False, "data": data, "error": msg}


class HomeAssistantClient:
    """Thin wrapper around Home Assistant REST API."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = (base_url or "").rstrip("/")
        self._token = (token or "").strip()
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._base or not self._token:
            return _err("Home Assistant is not configured (missing URL or token).")
        url = f"{self._base}{path}"
        try:
            r = requests.request(
                method,
                url,
                headers=self._headers,
                params=params,
                json=json_body,
                timeout=HA_TIMEOUT,
            )
            if r.status_code >= 400:
                return _err(f"HTTP {r.status_code}: {r.text[:500]}", data=None)
            if not r.content:
                return _ok({})
            try:
                return _ok(r.json())
            except ValueError:
                return _ok(r.text)
        except requests.RequestException as e:
            logger.warning("HA request failed %s %s: %s", method, path, e)
            return _err(str(e))

    def is_connected(self) -> dict[str, Any]:
        try:
            if not self._base or not self._token:
                return _err("Home Assistant is not configured (missing URL or token).")
            url = f"{self._base}/api/"
            r = requests.get(url, headers=self._headers, timeout=HA_TIMEOUT)
            if r.status_code == 200:
                try:
                    return _ok(r.json())
                except ValueError:
                    return _ok({"raw": r.text})
            return _err(f"HTTP {r.status_code}", data=r.text[:200] if r.text else None)
        except requests.RequestException as e:
            logger.warning("HA is_connected: %s", e)
            return _err(str(e))

    def get_states(self) -> dict[str, Any]:
        try:
            return self._request("GET", "/api/states")
        except Exception as e:
            logger.exception("get_states: %s", e)
            return _err(str(e))

    def get_state(self, entity_id: str) -> dict[str, Any]:
        try:
            eid = (entity_id or "").strip()
            if not eid:
                return _err("entity_id is required")
            return self._request("GET", f"/api/states/{quote(eid, safe='')}")
        except Exception as e:
            logger.exception("get_state: %s", e)
            return _err(str(e))

    def set_state(self, entity_id: str, state: str, attributes: dict | None = None) -> dict[str, Any]:
        try:
            eid = (entity_id or "").strip()
            if not eid:
                return _err("entity_id is required")
            body: dict[str, Any] = {"state": state}
            if attributes is not None:
                body["attributes"] = attributes
            return self._request("POST", f"/api/states/{quote(eid, safe='')}", json_body=body)
        except Exception as e:
            logger.exception("set_state: %s", e)
            return _err(str(e))

    def call_service(self, domain: str, service: str, data: dict | None = None) -> dict[str, Any]:
        try:
            dom = (domain or "").strip()
            svc = (service or "").strip()
            if not dom or not svc:
                return _err("domain and service are required")
            path = f"/api/services/{quote(dom, safe='')}/{quote(svc, safe='')}"
            return self._request("POST", path, json_body=dict(data) if data else {})
        except Exception as e:
            logger.exception("call_service: %s", e)
            return _err(str(e))

    def get_entities(self, domain: str | None = None) -> dict[str, Any]:
        try:
            res = self.get_states()
            if not res.get("success"):
                return res
            states = res.get("data")
            if not isinstance(states, list):
                return _err("Unexpected states payload", data=states)
            if not domain:
                return _ok(states)
            dom = domain.strip().lower()
            filtered = [s for s in states if isinstance(s, dict) and str(s.get("entity_id", "")).split(".")[0] == dom]
            return _ok(filtered)
        except Exception as e:
            logger.exception("get_entities: %s", e)
            return _err(str(e))

    def turn_on(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:
        try:
            payload: dict[str, Any] = {"entity_id": entity_id}
            payload.update(kwargs)
            return self.call_service("homeassistant", "turn_on", payload)
        except Exception as e:
            logger.exception("turn_on: %s", e)
            return _err(str(e))

    def turn_off(self, entity_id: str) -> dict[str, Any]:
        try:
            return self.call_service("homeassistant", "turn_off", {"entity_id": entity_id})
        except Exception as e:
            logger.exception("turn_off: %s", e)
            return _err(str(e))

    def toggle(self, entity_id: str) -> dict[str, Any]:
        try:
            return self.call_service("homeassistant", "toggle", {"entity_id": entity_id})
        except Exception as e:
            logger.exception("toggle: %s", e)
            return _err(str(e))

    def set_brightness(self, entity_id: str, brightness: int) -> dict[str, Any]:
        try:
            b = int(brightness)
            b = max(0, min(255, b))
            return self.call_service("light", "turn_on", {"entity_id": entity_id, "brightness": b})
        except Exception as e:
            logger.exception("set_brightness: %s", e)
            return _err(str(e))

    def set_color(self, entity_id: str, r: int, g: int, b: int) -> dict[str, Any]:
        try:
            return self.call_service(
                "light",
                "turn_on",
                {"entity_id": entity_id, "rgb_color": [int(r), int(g), int(b)]},
            )
        except Exception as e:
            logger.exception("set_color: %s", e)
            return _err(str(e))

    def set_temperature(self, entity_id: str, temp: float) -> dict[str, Any]:
        try:
            return self.call_service("climate", "set_temperature", {"entity_id": entity_id, "temperature": float(temp)})
        except Exception as e:
            logger.exception("set_temperature: %s", e)
            return _err(str(e))

    def set_volume(self, entity_id: str, volume: float) -> dict[str, Any]:
        try:
            v = max(0.0, min(1.0, float(volume)))
            return self.call_service("media_player", "volume_set", {"entity_id": entity_id, "volume_level": v})
        except Exception as e:
            logger.exception("set_volume: %s", e)
            return _err(str(e))

    def play_media(self, entity_id: str, media_url: str, media_type: str = "music") -> dict[str, Any]:
        try:
            return self.call_service(
                "media_player",
                "play_media",
                {
                    "entity_id": entity_id,
                    "media_content_id": media_url,
                    "media_content_type": media_type or "music",
                },
            )
        except Exception as e:
            logger.exception("play_media: %s", e)
            return _err(str(e))

    def get_automations(self) -> dict[str, Any]:
        try:
            res = self.get_entities("automation")
            if not res.get("success"):
                return res
            return _ok(res.get("data") or [])
        except Exception as e:
            logger.exception("get_automations: %s", e)
            return _err(str(e))

    def trigger_automation(self, automation_id: str) -> dict[str, Any]:
        try:
            aid = (automation_id or "").strip()
            if not aid:
                return _err("automation_id is required")
            if not aid.startswith("automation."):
                aid = f"automation.{aid}"
            return self.call_service("automation", "trigger", {"entity_id": aid})
        except Exception as e:
            logger.exception("trigger_automation: %s", e)
            return _err(str(e))

    def get_history(self, entity_id: str, hours: int = 24) -> dict[str, Any]:
        try:
            eid = (entity_id or "").strip()
            if not eid:
                return _err("entity_id is required")
            start = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
            start_iso = start.isoformat()
            path = f"/api/history/period/{quote(start_iso, safe='')}"
            return self._request("GET", path, params={"filter_entity_id": eid})
        except Exception as e:
            logger.exception("get_history: %s", e)
            return _err(str(e))


if __name__ == "__main__":
    c = HomeAssistantClient("http://127.0.0.1:8123", "")
    print("empty token:", c.is_connected())
