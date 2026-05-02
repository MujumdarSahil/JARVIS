"""
REST API blueprint for the Jarvis web UI.

TODO: add auth (local network only for now).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS  # legacy fallback
from flask import Blueprint, jsonify, request

from core.db import db

try:
    from bson import ObjectId
except ImportError:
    ObjectId = None  # type: ignore[misc, assignment]

if TYPE_CHECKING:
    from interface.web.app import WebInterface


def _error(message: str, code: int = 400):
    return jsonify({"error": message, "code": code}), code


def _scrub_mongo_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        else:
            out[k] = v
    return out


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
            raw = []
            try:
                try:
                    with DDGS() as ddg_client:
                        raw = list(ddg_client.text(query, max_results=max_results))
                finally:
                    pass
            except Exception as e:
                return _error(str(e), 500)
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

    def _agent_roster():
        orch = getattr(web.registry, "orchestrator", None)
        rows: list[dict[str, Any]] = []
        if orch is None:
            return rows
        rows.append(
            {
                "name": orch.name,
                "status": getattr(orch, "status", "idle"),
                "current_task": getattr(orch, "current_task", ""),
                "tasks_completed": getattr(orch, "tasks_completed", 0),
            }
        )
        for name, agent in getattr(orch, "_agents", {}).items():
            rows.append(
                {
                    "name": name,
                    "status": getattr(agent, "status", "idle"),
                    "current_task": getattr(agent, "current_task", ""),
                    "tasks_completed": getattr(agent, "tasks_completed", 0),
                }
            )
        return rows

    @bp.route("/agents", methods=["GET"])
    def api_agents_list():
        try:
            return jsonify({"agents": _agent_roster()})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/agents/<name>", methods=["GET"])
    def api_agent_detail(name: str):
        try:
            key = (name or "").strip().lower()
            for a in _agent_roster():
                if str(a.get("name") or "").lower() == key:
                    return jsonify({"agent": a})
            return _error("Agent not found", 404)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/memories", methods=["GET"])
    def api_memories_list():
        try:
            if not db.available:
                return jsonify({"memories": [], "message": "MongoDB not available"})
            category = (request.args.get("category") or "").strip()
            limit = max(1, min(int(request.args.get("limit") or 30), 200))
            search = (request.args.get("search") or "").strip()
            if search:
                rows = db.text_search("memories", search, limit=limit)
            else:
                q: dict[str, Any] = {"user_id": "default"}
                if category:
                    q["category"] = category
                rows = db.find("memories", q, limit=limit, sort=[("timestamp", -1)])
            return jsonify({"memories": [_scrub_mongo_doc(dict(r)) for r in rows]})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/memories/<mid>", methods=["DELETE"])
    def api_memories_delete(mid: str):
        try:
            if not db.available:
                return _error("MongoDB not available", 503)
            q: dict[str, Any] = {}
            if ObjectId is not None and ObjectId.is_valid(mid):
                q["_id"] = ObjectId(mid)
            else:
                q["text_hash"] = mid
            n = db.delete("memories", q)
            if n <= 0:
                return _error("Memory not found", 404)
            return jsonify({"ok": True, "deleted": n})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/stats/conversations", methods=["GET"])
    def api_stats_conversations():
        try:
            orch = getattr(web.registry, "orchestrator", None)
            n_agents = (len(getattr(orch, "_agents", {}) or {}) + 1) if orch else 0
            tc = tm = 0
            avg_res = 0.0
            most_tool = "none"
            total_msgs = 0
            if db.available:
                try:
                    col = db.get_collection("conversations")
                    if col is not None:
                        tc = len(col.distinct("session_id"))
                        total_msgs = db.count("conversations", {})
                    tm = db.count("memories", {})
                    logs = db.find("agent_logs", {}, limit=1000)
                    if logs:
                        durs = [l.get("duration_ms", 0) for l in logs if l.get("duration_ms")]
                        if durs:
                            avg_res = sum(durs) / len(durs)
                        tools = [l.get("tool_used") for l in logs if l.get("tool_used")]
                        if tools:
                            from collections import Counter
                            most_tool = Counter(tools).most_common(1)[0][0]
                except Exception:
                    pass
            uptime_s = int(time.perf_counter() - web._started_at)
            return jsonify({
                "total_conversations": tc,
                "total_messages": total_msgs,
                "avg_response_time_ms": round(avg_res, 1),
                "most_used_tool": most_tool,
                "total_memories": tm,
                "uptime": uptime_s
            })
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/stats/tools", methods=["GET"])
    def api_stats_tools():
        try:
            counts = web.registry.get_call_counts()
            if not counts and db.available:
                try:
                    logs = db.find("agent_logs", {}, limit=5000)
                    from collections import Counter
                    counts = Counter([l.get("tool_used") for l in logs if l.get("tool_used")])
                except Exception:
                    pass
            if not counts: # fallback mock
                counts = {"files": 0, "search": 0, "code": 0, "shell": 0, "smarthome": 0, "direct": 0}
            return jsonify(counts)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/stats/response-times", methods=["GET"])
    def api_stats_response_times():
        try:
            buckets = {"0-1s": 0, "1-3s": 0, "3-6s": 0, "6-10s": 0, "10s+": 0}
            if db.available:
                try:
                    logs = db.find("agent_logs", {}, limit=2000)
                    for l in logs:
                        ms = l.get("duration_ms", 0)
                        if ms < 1000: buckets["0-1s"] += 1
                        elif ms < 3000: buckets["1-3s"] += 1
                        elif ms < 6000: buckets["3-6s"] += 1
                        elif ms < 10000: buckets["6-10s"] += 1
                        else: buckets["10s+"] += 1
                except Exception:
                    pass
            return jsonify(buckets)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/stats/agents", methods=["GET"])
    def api_stats_agents():
        try:
            agents = {}
            orch = getattr(web.registry, "orchestrator", None)
            if orch:
                agents["orchestrator"] = {"calls": getattr(orch, "tasks_completed", 0), "avg_duration": 0}
                for name, agent in getattr(orch, "_agents", {}).items():
                    agents[name] = {"calls": getattr(agent, "tasks_completed", 0), "avg_duration": 0}
            return jsonify(agents)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/lessons", methods=["GET"])
    def api_lessons():
        try:
            ls = getattr(web.registry, "lesson_store", None)
            if ls:
                lessons = ls.get_top_failures(10)
                return jsonify([_scrub_mongo_doc(dict(l)) for l in lessons])
            return jsonify([])
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/schedule/<task_id>", methods=["PATCH"])
    def api_schedule_patch(task_id: str):
        try:
            aa = getattr(web.registry, "autonomous_agent", None)
            if aa is None: return _error("autonomy disabled", 400)
            body = request.get_json(silent=True) or {}
            enabled = body.get("enabled", True)
            if enabled: aa.scheduler.resume_task(task_id)
            else: aa.scheduler.pause_task(task_id)
            return jsonify({"ok": True})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/schedule/<task_id>/run", methods=["POST"])
    def api_schedule_run(task_id: str):
        try:
            aa = getattr(web.registry, "autonomous_agent", None)
            if aa is None: return _error("autonomy disabled", 400)
            res = aa.scheduler.run_task_now(task_id)
            return jsonify(res)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/mood/history", methods=["GET"])
    def api_mood_history():
        try:
            ea = getattr(web.registry, "emotion_agent", None)
            if ea:
                hist = ea.mood_tracker.get_mood_history(50)
                return jsonify(hist)
            return jsonify([])
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/mood/sentiment-breakdown", methods=["GET"])
    def api_mood_sentiment_breakdown():
        try:
            breakdown = {"positive": 0, "negative": 0, "neutral": 0, "frustrated": 0, "excited": 0, "confused": 0, "urgent": 0}
            if db.available:
                try:
                    logs = db.find("emotion_logs", {}, limit=100)
                    for l in logs:
                        s = l.get("sentiment", {}).get("sentiment", "neutral")
                        if s in breakdown: breakdown[s] += 1
                except Exception:
                    pass
            return jsonify(breakdown)
        except Exception as e:
            return _error(str(e), 500)

    # ─────────────────── Phase 2: Gmail / Calendar ────────────────────────
    def _gmail_agent():
        orch = getattr(web.registry, "orchestrator", None)
        agents = getattr(orch, "_agents", {}) if orch else {}
        return agents.get("gmail")

    @bp.route("/gmail/inbox", methods=["GET"])
    def api_gmail_inbox():
        try:
            agent = _gmail_agent()
            if agent is None:
                return jsonify({"configured": False, "emails": [], "message": "Gmail not enabled."})
            result = agent.execute({"action": "read_inbox", "params": {"max_results": 20}})
            return jsonify({"configured": True, "summary": result.get("result", ""), "emails": result.get("emails", [])})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/gmail/send", methods=["POST"])
    def api_gmail_send():
        try:
            agent = _gmail_agent()
            if agent is None:
                return _error("Gmail not enabled.", 400)
            body = request.get_json(silent=True) or {}
            confirmed = bool(body.get("confirmed", False))
            result = agent.execute({"action": "send_email", "params": {**body, "confirmed": confirmed}})
            return jsonify(result)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/gmail/calendar", methods=["GET"])
    def api_gmail_calendar():
        try:
            agent = _gmail_agent()
            if agent is None:
                return jsonify({"configured": False, "events": [], "message": "Gmail not enabled."})
            days = int(request.args.get("days_ahead", 7))
            result = agent.execute({"action": "get_calendar", "params": {"days_ahead": days}})
            return jsonify({"configured": True, "summary": result.get("result", ""), "events": result.get("events", [])})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/gmail/event", methods=["POST"])
    def api_gmail_event():
        try:
            agent = _gmail_agent()
            if agent is None:
                return _error("Gmail not enabled.", 400)
            body = request.get_json(silent=True) or {}
            result = agent.execute({"action": "create_event", "params": body})
            return jsonify(result)
        except Exception as e:
            return _error(str(e), 500)

    # ─────────────────── Phase 2: Finance ─────────────────────────────────
    def _finance_agent():
        orch = getattr(web.registry, "orchestrator", None)
        agents = getattr(orch, "_agents", {}) if orch else {}
        return agents.get("finance")

    @bp.route("/finance/markets", methods=["GET"])
    def api_finance_markets():
        try:
            agent = _finance_agent()
            if agent is None:
                return jsonify({"configured": False, "message": "Finance not enabled."})
            result = agent.execute({"action": "market_summary", "params": {}})
            return jsonify({"configured": True, "summary": result.get("result", ""), "data": result.get("data", {})})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/finance/expenses", methods=["GET"])
    def api_finance_expenses_get():
        try:
            agent = _finance_agent()
            if agent is None:
                return jsonify({"configured": False, "message": "Finance not enabled."})
            period = request.args.get("period", "this_month")
            result = agent.execute({"action": "expense_summary", "params": {"period": period}})
            return jsonify({"configured": True, "summary": result.get("result", ""), "data": result.get("data", {})})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/finance/expenses", methods=["POST"])
    def api_finance_expenses_post():
        try:
            agent = _finance_agent()
            if agent is None:
                return _error("Finance not enabled.", 400)
            body = request.get_json(silent=True) or {}
            result = agent.execute({"action": "add_expense", "params": body})
            return jsonify(result)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/finance/budget", methods=["GET"])
    def api_finance_budget():
        try:
            agent = _finance_agent()
            if agent is None:
                return jsonify({"configured": False, "message": "Finance not enabled."})
            result = agent.execute({"action": "budget_status", "params": {}})
            return jsonify({"configured": True, "summary": result.get("result", ""), "data": result.get("data", {})})
        except Exception as e:
            return _error(str(e), 500)

    # ─────────────────── Phase 2: GitHub ──────────────────────────────────
    def _github_agent():
        orch = getattr(web.registry, "orchestrator", None)
        agents = getattr(orch, "_agents", {}) if orch else {}
        return agents.get("github")

    @bp.route("/github/repos", methods=["GET"])
    def api_github_repos():
        try:
            agent = _github_agent()
            if agent is None:
                return jsonify({"configured": False, "repos": [], "message": "GitHub not enabled."})
            result = agent.execute({"action": "list_repos", "params": {}})
            return jsonify({"configured": True, "summary": result.get("result", ""), "repos": result.get("repos", [])})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/github/issues/<path:repo>", methods=["GET"])
    def api_github_issues(repo: str):
        try:
            agent = _github_agent()
            if agent is None:
                return _error("GitHub not enabled.", 400)
            result = agent.execute({"action": "list_issues", "params": {"repo": repo}})
            return jsonify({"summary": result.get("result", ""), "issues": result.get("issues", [])})
        except Exception as e:
            return _error(str(e), 500)

    # ─────────────────── Phase 2: Personal KB ─────────────────────────────
    def _personal_kb():
        ctx = getattr(web, "_context", None) or getattr(web, "context", None)
        if ctx:
            kb = getattr(ctx, "_personal_kb", None)
            if kb:
                return kb
        orch = getattr(web.registry, "orchestrator", None)
        agents = getattr(orch, "_agents", {}) if orch else {}
        mem = agents.get("memory")
        if mem:
            return getattr(mem, "_personal_kb", None) or getattr(mem, "personal_kb", None)
        return None

    @bp.route("/kb/profile", methods=["GET"])
    def api_kb_profile():
        try:
            kb = _personal_kb()
            if kb is None:
                return jsonify({"configured": False, "profile": {}, "message": "Personal KB not available."})
            return jsonify({"configured": True, "profile": kb.get_full_profile()})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/kb/contact", methods=["POST"])
    def api_kb_contact_post():
        try:
            kb = _personal_kb()
            if kb is None:
                return _error("Personal KB not available.", 400)
            body = request.get_json(silent=True) or {}
            result = kb.add_contact(
                name=body.get("name", ""),
                email=body.get("email"),
                phone=body.get("phone"),
                relationship=body.get("relationship", "contact"),
                notes=body.get("notes", ""),
            )
            return jsonify(result)
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/kb/contacts", methods=["GET"])
    def api_kb_contacts():
        try:
            kb = _personal_kb()
            if kb is None:
                return jsonify({"configured": False, "contacts": []})
            contacts = kb.get("contacts")
            return jsonify({"configured": True, "contacts": contacts})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/identity", methods=["GET"])
    def api_identity():
        try:
            js = getattr(web, "jarvis_self", None)
            if js is None:
                return jsonify({"configured": False, "identity": {}})
            return jsonify({"configured": True, "identity": js.identity})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/relationship", methods=["GET"])
    def api_relationship():
        try:
            rel = getattr(web, "relationship", None)
            if rel is None:
                return jsonify({"configured": False, "relationship": {}})
            return jsonify({"configured": True, "relationship": rel.relationship_data, "summary": rel.get_relationship_summary()})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/patterns", methods=["GET"])
    def api_patterns():
        try:
            pe = getattr(web, "pattern_engine", None)
            if pe is None:
                return jsonify({"configured": False, "patterns": []})
            return jsonify({"configured": True, "patterns": pe.find_patterns(), "summary": pe.get_pattern_summary()})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/suggestions", methods=["GET"])
    def api_suggestions():
        try:
            se = getattr(web, "suggestion_engine", None)
            if se is None:
                return jsonify({"configured": False, "suggestions": []})
            sugs = se.get_suggestions({})
            return jsonify({"configured": True, "suggestions": se.format_suggestion_for_web(sugs)})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/suggestions/<sid>/accept", methods=["POST"])
    def api_suggestion_accept(sid: str):
        try:
            se = getattr(web, "suggestion_engine", None)
            if se is None:
                return _error("Suggestion engine unavailable", 400)
            se.mark_accepted(sid)
            return jsonify({"ok": True})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/suggestions/<sid>/dismiss", methods=["POST"])
    def api_suggestion_dismiss(sid: str):
        try:
            se = getattr(web, "suggestion_engine", None)
            if se is None:
                return _error("Suggestion engine unavailable", 400)
            se.mark_dismissed(sid)
            return jsonify({"ok": True})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/weekly-report", methods=["GET"])
    def api_weekly_report():
        try:
            wl = getattr(web, "weekly_learner", None)
            if wl is None:
                return jsonify({"configured": False, "report": {}})
            return jsonify({"configured": True, "report": wl.get_weekly_report(0)})
        except Exception as e:
            return _error(str(e), 500)

    @bp.route("/trajectory", methods=["GET"])
    def api_trajectory():
        try:
            wl = getattr(web, "weekly_learner", None)
            if wl is None:
                return jsonify({"configured": False, "trajectory": {}})
            return jsonify({"configured": True, "trajectory": wl.get_improvement_trajectory()})
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
