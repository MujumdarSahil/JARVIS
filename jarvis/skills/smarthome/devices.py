"""
Device abstraction: registry, fuzzy match, regex-based natural language control (no LLM).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from utils.logger import get_logger

from .homeassistant import HomeAssistantClient
from .mqtt import MQTTClient

logger = get_logger(__name__)

ROOM_KEYWORDS = (
    "living room",
    "bedroom",
    "kitchen",
    "bathroom",
    "office",
    "garage",
    "hallway",
    "dining room",
    "lounge",
    "basement",
    "attic",
    "guest room",
    "kids room",
    "laundry",
    "study",
)

COLOR_MAP: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "warm": (255, 200, 100),
    "cool": (200, 220, 255),
    "orange": (255, 140, 0),
    "purple": (128, 0, 128),
    "yellow": (255, 255, 0),
}


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i, c1 in enumerate(a, 1):
        cur = [i]
        for j, c2 in enumerate(b, 1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (c1 != c2)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _slug(s: str) -> str:
    return _norm(s).replace(" ", "_")


class DeviceManager:
    def __init__(
        self,
        ha_client: HomeAssistantClient | None = None,
        mqtt_client: MQTTClient | None = None,
        config: dict[str, Any] | None = None,
        registry_path: str | Path | None = None,
    ) -> None:
        self.ha = ha_client
        self.mqtt = mqtt_client
        self.config: dict[str, Any] = dict(config or {})
        sh = self.config.get("smarthome") or self.config
        rooms_cfg = (sh.get("rooms") or {}) if isinstance(sh, dict) else {}
        self._room_aliases: dict[str, str] = {}
        if isinstance(rooms_cfg, dict):
            aliases = rooms_cfg.get("aliases") or {}
            if isinstance(aliases, dict):
                for k, v in aliases.items():
                    self._room_aliases[_norm(str(k))] = _slug(str(v))
        base = Path(__file__).resolve().parent.parent.parent
        self._registry_path = Path(registry_path) if registry_path else base / "devices.json"
        self._registry: dict[str, Any] = {
            "by_room": {},
            "by_type": {},
            "entities": [],
            "last_error": None,
        }

    def _ha_configured(self) -> bool:
        if not self.ha:
            return False
        try:
            r = self.ha.is_connected()
            return bool(r.get("success"))
        except Exception:
            return False

    def _infer_room(self, entity_id: str, friendly: str, attrs: dict[str, Any]) -> str:
        blob = f"{entity_id} {friendly}".lower()
        area = attrs.get("area_id") or attrs.get("area")
        if area:
            return _slug(str(area))
        for phrase in ROOM_KEYWORDS:
            if phrase in blob:
                return _slug(phrase.replace(" ", "_"))
        for alias_norm, target in self._room_aliases.items():
            if alias_norm in blob or alias_norm.replace(" ", "_") in entity_id.lower():
                return target
        parts = entity_id.split(".")
        if len(parts) >= 2:
            tail = parts[1].replace("_", " ")
            for phrase in ROOM_KEYWORDS:
                if phrase.replace(" ", "_") in parts[1].lower():
                    return _slug(phrase)
            toks = tail.split()
            if toks:
                return _slug(toks[0])
        return "unassigned"

    def discover_devices(self) -> dict[str, Any]:
        if not self.ha:
            loaded = self.load_device_registry(str(self._registry_path))
            if loaded.get("success"):
                return {"success": True, "data": self._registry, "error": None, "source": "cache"}
            return {
                "success": False,
                "data": self._registry,
                "error": "Home Assistant not configured or unreachable; no cache.",
            }

        res = self.ha.get_states()
        if not res.get("success"):
            self._registry["last_error"] = res.get("error")
            loaded = self.load_device_registry(str(self._registry_path))
            if loaded.get("success"):
                return {
                    "success": True,
                    "data": self._registry,
                    "error": res.get("error"),
                    "source": "cache_fallback",
                }
            return {"success": False, "data": self._registry, "error": res.get("error")}

        states = res.get("data") or []
        if not isinstance(states, list):
            return {"success": False, "data": {}, "error": "Invalid states list"}

        by_room: dict[str, list[dict[str, Any]]] = {}
        by_type: dict[str, list[dict[str, Any]]] = {}
        entities: list[dict[str, Any]] = []

        for st in states:
            if not isinstance(st, dict):
                continue
            eid = str(st.get("entity_id", ""))
            if not eid or "." not in eid:
                continue
            domain = eid.split(".", 1)[0]
            attrs = st.get("attributes") if isinstance(st.get("attributes"), dict) else {}
            friendly = str(attrs.get("friendly_name") or eid)
            room = self._infer_room(eid, friendly, attrs)
            row = {
                "entity_id": eid,
                "domain": domain,
                "friendly_name": friendly,
                "state": st.get("state"),
                "attributes": attrs,
                "room": room,
            }
            entities.append(row)
            by_room.setdefault(room, []).append(row)
            by_type.setdefault(domain, []).append(row)

        self._registry = {
            "by_room": by_room,
            "by_type": by_type,
            "entities": entities,
            "last_error": None,
        }
        self.save_device_registry(str(self._registry_path))
        return {"success": True, "data": self._registry, "error": None, "source": "live"}

    def _resolve_room_name(self, name: str) -> str:
        n = _norm(name)
        if n in self._room_aliases:
            return self._room_aliases[n]
        return _slug(name)

    def find_device(self, name: str) -> list[dict[str, Any]]:
        q = _norm(name)
        if not q:
            return []
        scored: list[tuple[int, dict[str, Any]]] = []
        for ent in self._registry.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            eid = _norm(ent.get("entity_id", ""))
            fn = _norm(ent.get("friendly_name", ""))
            room = _norm(str(ent.get("room", "")).replace("_", " "))
            cand = f"{eid} {fn} {room}"
            d1 = _levenshtein(q, fn) if fn else 999
            d2 = _levenshtein(q, eid) if eid else 999
            d3 = 0 if q in cand else min(d1, d2) + 2
            score = min(d1, d2, d3)
            if q in fn or q in eid or q in room:
                score = min(score, 1)
            scored.append((score, ent))
        scored.sort(key=lambda x: x[0])
        out: list[dict[str, Any]] = []
        for score, ent in scored[:20]:
            if score <= max(5, len(q) + 3):
                out.append(ent)
        return out[:8]

    def _entities_in_room(self, room_slug: str) -> list[dict[str, Any]]:
        by_room = self._registry.get("by_room") or {}
        resolved = self._resolve_room_name(room_slug.replace("_", " "))
        keys = [room_slug, resolved, _slug(room_slug)]
        for k in list(keys):
            if k in by_room:
                return list(by_room[k])
        for alias_norm, target in self._room_aliases.items():
            if alias_norm == _norm(room_slug) and target in by_room:
                return list(by_room[target])
        return []

    def _all_controllable_lights(self) -> list[str]:
        out: list[str] = []
        for ent in self._registry.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            eid = str(ent.get("entity_id", ""))
            if eid.startswith("light."):
                out.append(eid)
        return out

    def _all_switches(self) -> list[str]:
        return [
            str(e["entity_id"])
            for e in self._registry.get("entities") or []
            if isinstance(e, dict) and str(e.get("entity_id", "")).startswith("switch.")
        ]

    def _climate_entities(self) -> list[str]:
        return [
            str(e["entity_id"])
            for e in self._registry.get("entities") or []
            if isinstance(e, dict) and str(e.get("entity_id", "")).startswith("climate.")
        ]

    def _lock_entities(self) -> list[str]:
        return [
            str(e["entity_id"])
            for e in self._registry.get("entities") or []
            if isinstance(e, dict) and str(e.get("entity_id", "")).startswith("lock.")
        ]

    def _overhead_lights(self, room_slug: str | None) -> list[str]:
        lights = self._all_controllable_lights()
        if room_slug:
            in_room = {e["entity_id"] for e in self._entities_in_room(room_slug) if e.get("entity_id")}
            lights = [x for x in lights if x in in_room]
        return [x for x in lights if "overhead" in x.lower() or "ceiling" in x.lower()]

    def control(self, command: str) -> dict[str, Any]:
        if not self.ha:
            return {
                "success": False,
                "action_taken": "none",
                "entity_id": None,
                "result": None,
                "error": "Smart home is not configured. Set smarthome.homeassistant.url and token in config.yaml.",
            }
        cmd = (command or "").strip()
        if not cmd:
            return {
                "success": False,
                "action_taken": "none",
                "entity_id": None,
                "result": None,
                "error": "Empty command.",
            }
        low = cmd.lower()

        try:
            if re.search(r"\bturn off everything\b|\bshut off everything\b|\bturn everything off\b", low):
                seen: set[str] = set()
                results = []
                for eid in self._all_controllable_lights() + self._all_switches():
                    if eid in seen:
                        continue
                    seen.add(eid)
                    results.append(self.ha.turn_off(eid))
                return {
                    "success": any(r.get("success") for r in results) if results else True,
                    "action_taken": "turn_off_all",
                    "entity_id": "*",
                    "result": results,
                    "error": None,
                }

            if re.search(r"\bturn off overhead\b|\boverhead lights?\s+off\b", low):
                room_slug = None
                for phrase in ROOM_KEYWORDS:
                    if phrase in low:
                        room_slug = _slug(phrase)
                        break
                targets = self._overhead_lights(room_slug)
                results = [self.ha.turn_off(eid) for eid in targets]
                return {
                    "success": any(x.get("success") for x in results) if results else True,
                    "action_taken": "turn_off_overhead",
                    "entity_id": targets[0] if targets else None,
                    "result": results,
                    "error": None if targets else "No overhead lights matched.",
                }

            if re.search(r"\block\b.*\bdoors?\b|\bdoors?\b.*\block\b", low):
                locks = self._lock_entities()
                results = []
                for eid in locks:
                    results.append(self.ha.call_service("lock", "lock", {"entity_id": eid}))
                return {
                    "success": any(r.get("success") for r in results) if results else False,
                    "action_taken": "lock_doors",
                    "entity_id": ",".join(locks) if locks else None,
                    "result": results,
                    "error": None if locks else "No lock entities found.",
                }

            m = re.search(
                r"\bset\s+thermostat\s+to\s+([\d.]+)|\bthermostat\s+to\s+([\d.]+)|\bthermostat\b.*\bto\s+([\d.]+)|\btemperature\b.*\bto\s+([\d.]+)",
                low,
            )
            if m:
                temp = float(next(g for g in m.groups() if g is not None))
                climates = self._climate_entities()
                if not climates:
                    return {
                        "success": False,
                        "action_taken": "set_temperature",
                        "entity_id": None,
                        "result": None,
                        "error": "No climate entities.",
                    }
                target = climates[0]
                r = self.ha.set_temperature(target, temp)
                return {
                    "success": r.get("success", False),
                    "action_taken": "set_temperature",
                    "entity_id": target,
                    "result": r,
                    "error": r.get("error"),
                }

            m = re.search(r"\ball\s+lights?\s+to\s+(\d+)\s*%", low)
            if m:
                pct = int(m.group(1))
                b = max(1, min(255, int(round(pct * 255 / 100))))
                lights = self._all_controllable_lights()
                results = [self.ha.set_brightness(lid, b) for lid in lights[:40]]
                return {
                    "success": any(x.get("success") for x in results) if results else False,
                    "action_taken": "set_brightness_all",
                    "entity_id": lights[0] if lights else None,
                    "result": results,
                    "error": None if lights else "No lights found.",
                }

            m = re.search(r"\bset\s+(.+?)\s+lights?\s+to\s+(\d+)\s*%", low)
            if not m:
                m = re.search(
                    r"\bset\s+(.+?)\s+to\s+(\d+)\s*%?\s*brightness",
                    low,
                )
            if m:
                name = m.group(1).strip()
                pct = int(m.group(2))
                b = max(1, min(255, int(round(pct * 255 / 100))))
                found = self.find_device(name)
                lights = [f["entity_id"] for f in found if str(f.get("entity_id", "")).startswith("light.")]
                if not lights:
                    return {
                        "success": False,
                        "action_taken": "set_brightness",
                        "entity_id": None,
                        "result": None,
                        "error": f"No matching lights for '{name}'.",
                    }
                results = [self.ha.set_brightness(lid, b) for lid in lights]
                return {
                    "success": any(x.get("success") for x in results),
                    "action_taken": "set_brightness",
                    "entity_id": lights[0],
                    "result": results,
                    "error": None,
                }

            m = re.search(r"\bmake\s+(.+?)\s+lights?\s+(\w+)", low)
            if not m:
                m = re.search(r"\b(.+?)\s+lights?\s+(red|blue|green|white|warm|cool|orange|purple|yellow)\b", low)
            if m and any(c in low for c in COLOR_MAP):
                color_word = None
                for c in COLOR_MAP:
                    if c in low:
                        color_word = c
                        break
                if color_word:
                    rgb = COLOR_MAP[color_word]
                    room_or_name = None
                    for g in m.groups():
                        if g and str(g).lower() not in ("light", "lights", color_word):
                            room_or_name = str(g).strip()
                            break
                    targets: list[str] = []
                    if room_or_name:
                        rs = self._resolve_room_name(room_or_name)
                        targets = [
                            e["entity_id"]
                            for e in self._entities_in_room(rs)
                            if str(e.get("entity_id", "")).startswith("light.")
                        ]
                    if not targets:
                        targets = self._all_controllable_lights()
                    results = [self.ha.set_color(lid, rgb[0], rgb[1], rgb[2]) for lid in targets[:20]]
                    return {
                        "success": any(x.get("success") for x in results) if results else False,
                        "action_taken": "set_color",
                        "entity_id": targets[0] if targets else None,
                        "result": results,
                        "error": None if targets else "No lights to color.",
                    }

            m_on = re.search(r"\bturn on\b|\bswitch on\b", low)
            m_off = re.search(r"\bturn off\b|\bswitch off\b", low)
            m_toggle = re.search(r"\btoggle\b", low)
            if m_on or m_off or m_toggle:
                all_lights = ("all lights" in low or "every light" in low or "all the lights" in low) or (
                    "all" in low and ("light" in low or "lights" in low)
                )
                room_match = None
                for phrase in ROOM_KEYWORDS:
                    if phrase in low:
                        room_match = _slug(phrase)
                        break
                if not room_match:
                    rm = re.search(
                        r"(?:turn on|turn off|switch on|switch off|toggle)\s+(?:the\s+)?(?:all\s+)?(.+?)(?:\s+lights?)?$",
                        cmd,
                        re.I,
                    )
                    if rm:
                        guess = rm.group(1).strip()
                        if guess and guess.lower() not in ("all", "everything", "the lights", "lights"):
                            room_match = self._resolve_room_name(guess)

                targets: list[str] = []
                if all_lights or ("everything" in low and m_off):
                    targets = self._all_controllable_lights() + (self._all_switches() if "light" not in low else [])
                elif room_match:
                    ents = self._entities_in_room(room_match)
                    targets = [
                        e["entity_id"]
                        for e in ents
                        if str(e.get("entity_id", "")).startswith(("light.", "switch."))
                    ]
                else:
                    rm2 = re.search(
                        r"(?:turn on|turn off|switch on|switch off|toggle)\s+(?:the\s+)?(.+)",
                        cmd,
                        re.I,
                    )
                    name_guess = (rm2.group(1) if rm2 else "").strip()
                    name_guess = re.sub(r"\s+lights?$", "", name_guess, flags=re.I)
                    if name_guess:
                        found = self.find_device(name_guess)
                        targets = [
                            f["entity_id"]
                            for f in found
                            if str(f.get("entity_id", "")).startswith(("light.", "switch.", "fan."))
                        ]

                if not targets:
                    return {
                        "success": False,
                        "action_taken": "turn",
                        "entity_id": None,
                        "result": None,
                        "error": "Could not resolve devices for command.",
                    }

                results = []
                for eid in targets[:40]:
                    if m_off:
                        results.append(self.ha.turn_off(eid))
                    elif m_toggle:
                        results.append(self.ha.toggle(eid))
                    else:
                        results.append(self.ha.turn_on(eid))
                return {
                    "success": any(r.get("success") for r in results),
                    "action_taken": "turn_off" if m_off else ("toggle" if m_toggle else "turn_on"),
                    "entity_id": targets[0],
                    "result": results,
                    "error": None,
                }

            return {
                "success": False,
                "action_taken": "unparsed",
                "entity_id": None,
                "result": None,
                "error": "Command not recognized. Try e.g. 'turn on living room lights'.",
            }
        except Exception as e:
            logger.exception("control: %s", e)
            return {
                "success": False,
                "action_taken": "error",
                "entity_id": None,
                "result": None,
                "error": str(e),
            }

    def get_room_status(self, room: str) -> dict[str, Any]:
        if not self.ha:
            return {"success": False, "data": None, "error": "Smart home not configured."}
        slug = self._resolve_room_name(room)
        ents = self._entities_in_room(slug)
        if not ents and self._registry.get("entities"):
            ents = self._entities_in_room(_slug(room))
        live: list[dict[str, Any]] = []
        for e in ents:
            eid = str(e.get("entity_id", ""))
            st = self.ha.get_state(eid)
            if st.get("success"):
                live.append(st.get("data") if isinstance(st.get("data"), dict) else {"entity_id": eid, "raw": st})
            else:
                live.append({"entity_id": eid, "error": st.get("error")})
        return {"success": True, "data": {"room": slug, "entities": live}, "error": None}

    def get_all_status(self) -> str:
        if not self.ha:
            return "Smart home not configured."
        res = self.ha.get_states()
        if not res.get("success"):
            return f"Could not reach Home Assistant: {res.get('error')}"

        by_room: dict[str, list[str]] = {}
        states = res.get("data") or []
        if not isinstance(states, list):
            return "Invalid state data."
        for st in states:
            if not isinstance(st, dict):
                continue
            eid = str(st.get("entity_id", ""))
            dom = eid.split(".")[0] if "." in eid else ""
            if dom not in ("light", "switch", "climate", "sensor", "media_player", "lock"):
                continue
            attrs = st.get("attributes") if isinstance(st.get("attributes"), dict) else {}
            fn = str(attrs.get("friendly_name") or eid)
            room = self._infer_room(eid, fn, attrs).replace("_", " ").title()
            state = st.get("state")
            if dom == "climate":
                line = f"{fn}: {state}"
                cur = attrs.get("current_temperature")
                if cur is not None:
                    line += f", temp {cur}°C"
            elif dom == "sensor":
                line = f"{fn}: {state}"
            elif dom == "media_player":
                line = f"{fn}: {'ON' if state not in ('off', 'unavailable') else 'OFF'}"
            elif dom == "light":
                line = f"{fn}: {'ON' if state == 'on' else 'OFF'}"
            elif dom == "switch":
                line = f"{fn}: {'ON' if state == 'on' else 'OFF'}"
            else:
                line = f"{fn}: {state}"
            by_room.setdefault(room, []).append(line)

        parts: list[str] = []
        for room_name in sorted(by_room.keys()):
            parts.append(f"{room_name}: " + "; ".join(by_room[room_name][:12]))
        return "\n".join(parts) if parts else "No notable devices in state list."

    def save_device_registry(self, path: str | None = None) -> None:
        p = Path(path) if path else self._registry_path
        try:
            p.write_text(json.dumps(self._registry, indent=2), encoding="utf-8")
            logger.info("Saved device registry to %s", p)
        except OSError as e:
            logger.warning("save_device_registry: %s", e)

    def load_device_registry(self, path: str | None = None) -> dict[str, Any]:
        p = Path(path) if path else self._registry_path
        try:
            if not p.is_file():
                return {"success": False, "error": f"No file {p}"}
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"success": False, "error": "Invalid JSON"}
            self._registry = data
            return {"success": True, "error": None}
        except (OSError, json.JSONDecodeError) as e:
            return {"success": False, "error": str(e)}


if __name__ == "__main__":
    dm = DeviceManager(config={"smarthome": {"rooms": {"aliases": {"lounge": "living_room"}}}})
    print(dm.control("turn on living room lights"))
