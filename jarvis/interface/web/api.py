"""
REST API blueprint for the Jarvis web UI.

TODO: add auth (local network only for now).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from duckduckgo_search import DDGS
from flask import Blueprint, jsonify, request

if TYPE_CHECKING:
    from interface.web.app import WebInterface


def _error(message: str, code: int = 400):
    return jsonify({"error": message, "code": code}), code


def create_api_blueprint(web: "WebInterface") -> Blueprint:
    bp = Blueprint("api", __name__, url_prefix="/api")

    @bp.route("/status", methods=["GET"])
    def api_status():
        try:
            return jsonify(web.get_status_payload())
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/chat", methods=["POST"])
    def api_chat():
        try:
            body = request.get_json(silent=True) or {}
            message = (body.get("message") or "").strip()
            if not message:
                return _error("message is required", 400)
            reply, tool_used, provider = web.process_chat_message(message)
            return jsonify(
                {
                    "response": reply,
                    "tool_used": tool_used,
                    "provider": provider,
                }
            )
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/history", methods=["GET"])
    def api_history_get():
        try:
            return jsonify({"history": web.memory.get_history()})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/history", methods=["DELETE"])
    def api_history_delete():
        try:
            web.memory.clear()
            return jsonify({"ok": True, "message": "Conversation history cleared."})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/sysinfo", methods=["GET"])
    def api_sysinfo():
        try:
            data = web.parse_system_info()
            if data.get("error"):
                return _error(str(data["error"]), 500)
            return jsonify(data)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/providers", methods=["GET"])
    def api_providers():
        try:
            chain = web.brain.get_configured_providers()
            primary = chain[0] if chain else ""
            models = web.brain._config.get("models") or {}
            prov_cfg = models.get("providers") if isinstance(models, dict) else {}
            if not isinstance(prov_cfg, dict):
                prov_cfg = {}
            details = []
            for name in chain:
                pc = prov_cfg.get(name)
                if isinstance(pc, dict):
                    details.append(
                        {
                            "name": name,
                            "model": (pc.get("model") or "")[:200],
                            "has_key": bool(str(pc.get("api_key") or "").strip() or name == "ollama"),
                        }
                    )
                else:
                    details.append({"name": name, "model": "", "has_key": False})
            return jsonify(
                {
                    "primary": primary,
                    "chain": chain,
                    "active_last_success": web.brain.get_active_provider(),
                    "providers": details,
                }
            )
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/provider", methods=["POST"])
    def api_provider_post():
        try:
            body = request.get_json(silent=True) or {}
            name = (body.get("provider") or "").strip()
            if not name:
                return _error("provider is required", 400)
            ok, err = web.brain.set_primary_provider(name)
            if not ok:
                return _error(err or "Could not switch provider", 400)
            web.brain.reload_config()
            return jsonify(
                {
                    "ok": True,
                    "primary": (web.brain._config.get("models") or {}).get("primary"),
                }
            )
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/skills", methods=["GET"])
    def api_skills():
        try:
            smap = web.registry._skills_map()
            keys = [
                "web_search",
                "file_control",
                "shell_control",
                "clipboard",
                "app_launcher",
                "code_assistant",
                "smarthome",
            ]
            skills = []
            for k in keys:
                en = bool(smap.get(k, True))
                skills.append({"id": k, "enabled": en, "ok": en})
            return jsonify({"skills": skills})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/search", methods=["POST"])
    def api_search():
        try:
            body = request.get_json(silent=True) or {}
            query = (body.get("query") or "").strip()
            if not query:
                return _error("query is required", 400)
            max_results = int(body.get("max_results", 8))
            max_results = max(1, min(max_results, 15))
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
            items = []
            for item in raw:
                items.append(
                    {
                        "title": str(item.get("title", "")).strip(),
                        "url": str(item.get("href", "")).strip(),
                        "body": str(item.get("body", "")).strip(),
                    }
                )
            return jsonify({"query": query, "results": items})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/run", methods=["POST"])
    def api_run():
        try:
            if not web.registry._enabled("shell_control", True):
                return _error("shell_control skill is disabled", 403)
            body = request.get_json(silent=True) or {}
            command = (body.get("command") or "").strip()
            if not command:
                return _error("command is required", 400)
            timeout = int(body.get("timeout", 30))
            out = web.registry.shell_skill.run(command, timeout=timeout)
            return jsonify(
                {
                    "success": out.get("success"),
                    "stdout": out.get("stdout", ""),
                    "stderr": out.get("stderr", ""),
                    "returncode": out.get("returncode"),
                    "duration": out.get("duration"),
                }
            )
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/files", methods=["GET"])
    def api_files():
        try:
            if not web.registry._enabled("file_control", True):
                return _error("file_control skill is disabled", 403)
            path = (request.args.get("path") or ".").strip() or "."
            res = web.registry.file_skill.list_dir(path)
            if not res.get("success"):
                return _error(res.get("error") or "list_dir failed", 400)
            entries = _parse_list_dir_result(res.get("result") or "")
            return jsonify({"path": path, "entries": entries})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/file", methods=["GET"])
    def api_file():
        try:
            if not web.registry._enabled("file_control", True):
                return _error("file_control skill is disabled", 403)
            path = (request.args.get("path") or "").strip()
            if not path:
                return _error("path query parameter is required", 400)
            max_chars = int(request.args.get("max_chars", 8000))
            res = web.registry.file_skill.read_file(path, max_chars=max_chars)
            if not res.get("success"):
                return _error(res.get("error") or "read failed", 400)
            return jsonify({"path": path, "content": res.get("result", "")})
        except Exception as e:
            return _error(str(e), 500)

    def _smarthome_dm():
        return getattr(web, "device_manager", None)

    def _smarthome_rm():
        return getattr(web, "routine_manager", None)

    @bp.route("/smarthome/devices", methods=["GET"])
    def api_smarthome_devices():
        try:
            dm = _smarthome_dm()
            if dm is None:
                return jsonify(
                    {
                        "configured": False,
                        "devices": [],
                        "message": "Enable smarthome in config.yaml to use this feature.",
                    }
                )
            disc = dm.discover_devices()
            reg = getattr(dm, "_registry", {}) or {}
            return jsonify(
                {
                    "configured": True,
                    "success": disc.get("success"),
                    "source": disc.get("source"),
                    "registry": reg,
                }
            )
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/smarthome/status", methods=["GET"])
    def api_smarthome_status():
        try:
            dm = _smarthome_dm()
            if dm is None:
                return jsonify({"configured": False, "summary": "", "message": "Smart home not enabled in config."})
            summary = dm.get_all_status()
            reg = getattr(dm, "_registry", {}) or {}
            rooms = list((reg.get("by_room") or {}).keys()) if isinstance(reg, dict) else []
            return jsonify({"configured": True, "summary": summary, "rooms": rooms})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/smarthome/control", methods=["POST"])
    def api_smarthome_control():
        try:
            dm = _smarthome_dm()
            if dm is None:
                return _error("Smart home not enabled.", 400)
            ha = getattr(dm, "ha", None)
            body = request.get_json(silent=True) or {}
            eid = (body.get("entity_id") or "").strip()
            if eid and ha:
                if "brightness" in body:
                    b = int(body["brightness"])
                    r = ha.set_brightness(eid, b)
                    return jsonify({"ok": r.get("success"), "result": r})
                st = (body.get("state") or "").strip().lower()
                if st in ("on", "off"):
                    r = ha.turn_on(eid) if st == "on" else ha.turn_off(eid)
                    return jsonify({"ok": r.get("success"), "result": r})
                temp = body.get("temperature")
                if temp is not None:
                    r = ha.set_temperature(eid, float(temp))
                    return jsonify({"ok": r.get("success"), "result": r})
            cmd = (body.get("command") or "").strip()
            if not cmd:
                return _error("command or entity_id action required", 400)
            r = dm.control(cmd)
            return jsonify({"ok": r.get("success"), "result": r})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/smarthome/rooms", methods=["GET"])
    def api_smarthome_rooms():
        try:
            dm = _smarthome_dm()
            if dm is None:
                return jsonify({"configured": False, "rooms": []})
            reg = getattr(dm, "_registry", {}) or {}
            by_room = reg.get("by_room") or {}
            rooms = []
            if isinstance(by_room, dict):
                for name, ents in by_room.items():
                    n = len(ents) if isinstance(ents, list) else 0
                    rooms.append({"id": name, "name": str(name).replace("_", " ").title(), "device_count": n})
            rooms.sort(key=lambda x: x["name"])
            return jsonify({"configured": True, "rooms": rooms})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/smarthome/routine", methods=["POST"])
    def api_smarthome_routine():
        try:
            rm = _smarthome_rm()
            if rm is None:
                return _error("Smart home not enabled.", 400)
            body = request.get_json(silent=True) or {}
            name = (body.get("name") or "").strip()
            if not name:
                return _error("name is required", 400)
            r = rm.run_routine(name)
            if not r.get("success"):
                return jsonify({"ok": False, "error": r.get("error"), "data": r.get("data")}), 400
            return jsonify({"ok": True, "data": r.get("data")})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/smarthome/routines", methods=["GET"])
    def api_smarthome_routines():
        try:
            rm = _smarthome_rm()
            if rm is None:
                return jsonify({"configured": False, "routines": []})
            return jsonify({"configured": True, "routines": rm.describe_routines()})
        except Exception as e:
            return _error(str(e), 500)

    return bp


def _parse_list_dir_result(text: str) -> list[dict[str, Any]]:
    """Parse FileSkill.list_dir text lines (fixed-width layout) into structured entries."""
    entries: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.rstrip()
        if not line or line.startswith("(empty"):
            continue
        if len(line) < 22:
            entries.append({"kind": "", "size": "", "mtime": "", "name": line})
            continue
        kind = line[0:4].strip()
        size = line[6:18].strip()
        tail = line[20:]
        sep = tail.find("  ")
        if sep >= 0:
            mtime = tail[:sep].strip()
            name = tail[sep + 2 :].strip()
        else:
            mtime = tail.strip()
            name = ""
        entries.append({"kind": kind, "size": size, "mtime": mtime, "name": name})
    return entries
