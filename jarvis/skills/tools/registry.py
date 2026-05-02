"""
Routes natural-language requests to concrete skills using the LLM as a JSON planner.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


def _debug_log(location: str, message: str, data: dict[str, Any], run_id: str, hypothesis_id: str) -> None:
    try:
        payload = {
            "sessionId": "92b104",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open("debug-92b104.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass

# FIXED: improved router system prompt with explicit JSON examples and tool list
ROUTER_SYSTEM = """You are a tool router for JARVIS AI. Analyze the user's message and return ONLY a valid JSON object — no other text, no markdown, no explanation.

Available tools:
- code.generate — user wants code written. Params: {description: str, language: str}
- code.explain — user wants code explained. Params: {code: str}
- code.debug — user wants code fixed. Params: {code: str, error: str}
- code.review — user wants code reviewed. Params: {code: str}
- code.run_snippet — user wants code executed. Params: {code: str}
- search.web_search — user wants current/live info from internet. Params: {query: str, max_results: int}
- files.list_dir — list files in folder. Params: {path: str}
- files.read_file — read a file. Params: {path: str}
- files.write_file — write/create a file. Params: {path: str, content: str}
- files.find_files — search for files. Params: {pattern: str, search_dir: str}
- files.open_file — open a file with default app. Params: {path: str}
- shell.run — run a terminal command. Params: {command: str}
- shell.get_system_info — get CPU/RAM/disk stats. No params needed.
- shell.get_running_processes — list running processes. No params needed.
- apps.open_app — launch an application. Params: {name: str}
- apps.open_url — open a URL in browser. Params: {url: str}
- clipboard.read — read clipboard. No params needed.
- clipboard.write — write to clipboard. Params: {text: str}
- smarthome.control_device — control smart home device. Params: {command: str}
- gmail.read_inbox — read unread emails. Params: {}
- gmail.send_email — draft and send email (requires confirmation). Params: {to: str, subject: str, body: str}
- gmail.get_calendar — upcoming calendar events. Params: {days_ahead: int}
- gmail.create_event — create calendar event. Params: {title: str, start_datetime: str, end_datetime: str}
- gmail.summarize_day — inbox + calendar daily summary. Params: {}
- browser.browse — open and describe a webpage. Params: {url: str}
- browser.search — Google search via browser. Params: {query: str}
- browser.scrape — extract data from website. Params: {url: str}
- browser.extract_price — get product price from URL. Params: {url: str}
- browser.monitor_site — watch for website changes. Params: {url: str, selector: str}
- github.list_repos — list GitHub repositories. Params: {}
- github.list_issues — show repo issues. Params: {repo: str}
- github.review_pr — AI code review of pull request. Params: {repo: str, pr_number: int}
- github.check_ci — check CI/CD pipeline status. Params: {repo: str}
- github.repo_summary — full repo overview. Params: {repo: str}
- finance.market_summary — stock market overview. Params: {}
- finance.stock_price — get stock price. Params: {symbol: str}
- finance.crypto_price — get crypto price. Params: {coin: str}
- finance.add_expense — log an expense. Params: {amount: float, category: str, description: str}
- finance.expense_summary — spending summary. Params: {period: str}
- finance.monthly_report — full financial report. Params: {}
- kb.add_contact — save a contact. Params: {name: str, email: str}
- kb.find_contact — look up a contact. Params: {name: str}
- kb.update_goal — update a personal goal. Params: {goal: str, progress: int}
- kb.get_profile — show personal profile. Params: {}
- direct — answer from knowledge, no tool needed. Params: {}

ROUTING EXAMPLES (learn these patterns):
"write a python function to sort a list" → {"tool":"code","action":"generate","params":{"description":"python function to sort a list","language":"python"}}
"write code for addition" → {"tool":"code","action":"generate","params":{"description":"addition of two numbers","language":"python"}}
"explain this code: def foo(): pass" → {"tool":"code","action":"explain","params":{"code":"def foo(): pass"}}
"what is the weather today" → {"tool":"search","action":"web_search","params":{"query":"weather today","max_results":5}}
"who won the IPL 2024" → {"tool":"search","action":"web_search","params":{"query":"IPL 2024 winner","max_results":3}}
"open chrome" → {"tool":"apps","action":"open_app","params":{"name":"chrome"}}
"list files in downloads" → {"tool":"files","action":"list_dir","params":{"path":"C:/Users/mujum/Downloads"}}
"what is 2+2" → {"tool":"direct","action":"direct","params":{}}
"hello" → {"tool":"direct","action":"direct","params":{}}
"who is Elon Musk" → {"tool":"direct","action":"direct","params":{}}
"what is the capital of France" → {"tool":"direct","action":"direct","params":{}}
"show my CPU usage" → {"tool":"shell","action":"get_system_info","params":{}}
"run ipconfig" → {"tool":"shell","action":"run","params":{"command":"ipconfig"}}
"turn on living room lights" → {"tool":"smarthome","action":"control_device","params":{"command":"turn on living room lights"}}
"make a plan to learn X" → {"tool":"direct","action":"direct","params":{}}
"create a 30 day plan for X" → {"tool":"direct","action":"direct","params":{}}
"plan to become X" → {"tool":"direct","action":"direct","params":{}}
"how do I learn X in N days" → {"tool":"direct","action":"direct","params":{}}
"roadmap for X" → {"tool":"direct","action":"direct","params":{}}
"check my email" → {"tool":"gmail","action":"read_inbox","params":{}}
"what's on my calendar" → {"tool":"gmail","action":"get_calendar","params":{"days_ahead":7}}
"what's the price of RELIANCE stock" → {"tool":"finance","action":"stock_price","params":{"symbol":"RELIANCE.NS"}}
"bitcoin price" → {"tool":"finance","action":"crypto_price","params":{"coin":"bitcoin"}}
"I spent 250 on lunch" → {"tool":"finance","action":"add_expense","params":{"amount":250,"category":"food","description":"lunch"}}
"list my github repos" → {"tool":"github","action":"list_repos","params":{}}
"open amazon" → {"tool":"browser","action":"browse","params":{"url":"https://www.amazon.in"}}

RULES:
- Use "direct" for greetings, math, general knowledge, opinions, anything that doesn't need a tool
- Use "search" ONLY when the user needs current/live information (news, prices, scores, weather)
- Use "code" only when the user wants programming source code (languages, functions, scripts, APIs) — not for life/career/learning plans (those are "direct")
- Use "code" when user says "write", "create", "generate", "make" + code-related words (python, javascript, function, class, bug, compile)
- Use "gmail" for email/inbox/calendar actions
- Use "finance" for stocks, crypto, expenses, budget
- Use "browser" for visiting URLs, scraping, prices from websites
- Use "github" for repos, issues, PRs, CI
- Return ONLY the JSON object. Absolutely no other text.

Legacy schema (still supported): direct answer with {"tool":"direct","action":"none","params":{},"direct_response":true,"final_response":"..."}
For protected Windows paths, include "allow_protected_path": true in params only if the user explicitly confirmed the risk."""


def _format_history_snippet(messages: list[dict[str, str]], limit: int = 8) -> str:
    rows: list[str] = []
    for m in messages:
        if m.get("role") == "system":
            continue
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        rows.append(f"{role}: {content}")
    tail = rows[-limit:]
    return "\n".join(tail) if tail else "(no prior context)"


def _tool_preview(raw: Any) -> str:
    try:
        return json.dumps(raw, ensure_ascii=False)[:8000]
    except (TypeError, ValueError):
        return str(raw)[:8000]


def _is_empty_param(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


# FIXED: inject primary text args from user_message when the router omits them
def _inject_router_text_params(tool: str, action: str, params: dict[str, Any], user_message: str) -> None:
    t, a = tool.lower().strip(), action.strip()
    if t == "code" and a == "generate":
        if _is_empty_param(params.get("description")):
            params["description"] = user_message
    elif t == "code" and a in ("explain", "review"):
        if _is_empty_param(params.get("code")):
            params["code"] = user_message
    elif t == "code" and a == "debug":
        if _is_empty_param(params.get("code")):
            params["code"] = user_message
    elif t == "search" and a == "web_search":
        if _is_empty_param(params.get("query")):
            params["query"] = user_message
    elif t == "shell" and a == "run":
        if _is_empty_param(params.get("command")):
            params["command"] = user_message
    elif t == "smarthome" and a == "control_device":
        if _is_empty_param(params.get("command")):
            params["command"] = user_message
    # FIXED: files.read_file — never inject path from user_message (must come from router)


# FIXED: pass user_message into _invoke for missing required-param fallback; skip unsafe cases
def _invoke(
    obj: Any,
    method: str,
    params: dict[str, Any] | None,
    *,
    tool: str,
    action: str,
    user_message: str,
) -> Any:
    try:
        fn = getattr(obj, method)
        kw = dict(params or {})
        sig = inspect.signature(fn)
        call_kw = {k: v for k, v in kw.items() if k in sig.parameters}

        skip_universal = (tool == "files" and action == "read_file") or (
            tool == "code" and action == "convert"
        )

        if not skip_universal:
            for pname, p in sig.parameters.items():
                if pname == "self":
                    continue
                if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue
                if p.default is not inspect.Parameter.empty:
                    continue
                if pname in call_kw:
                    continue
                logger.error(
                    "Missing required param '%s' for %s.%s — injecting user_message as fallback",
                    pname,
                    tool,
                    action,
                )
                call_kw[pname] = user_message

        return fn(**call_kw)
    except Exception as e:
        logger.exception("Tool invoke failed: %s.%s %s", obj, method, e)
        return {"success": False, "result": "", "error": str(e)}


def _smarthome_keyword_route(message: str) -> dict[str, Any] | None:
    """Fast offline routing for common smart home phrases (before LLM router)."""
    m = (message or "").strip()
    if not m:
        return None
    low = m.lower()

    if re.search(r"\b(show|list)\s+(me\s+)?(all\s+)?(my\s+)?(smart\s+home\s+)?(device|devices|entities)\b", low):
        return {"tool": "smarthome", "action": "list_devices", "params": {}}

    if re.search(
        r"\b(run|start|execute)\s+(the\s+)?(?P<n>good\s+morning|good\s+night|movie\s+mode|away\s+mode)(?P<r>\s+routine)?\b",
        low,
    ) or re.search(r"\b(?P<n>good\s+morning|good\s+night)\s+routine\b", low):
        mm = re.search(
            r"(good\s+morning|good\s+night|movie\s+mode|away\s+mode)",
            low,
        )
        if mm:
            name = mm.group(1).replace(" ", "_")
            return {"tool": "smarthome", "action": "run_routine", "params": {"name": name}}

    if re.search(
        r"\bwhat\s*(?:\'|i)?s\s+the\s+temperature\b.*\b(?P<room>bedroom|kitchen|living\s+room|office|bathroom|garage)\b",
        low,
    ) or re.search(r"\btemperature\s+in\s+(?:the\s+)?(?P<room>bedroom|kitchen|living\s+room|office|bathroom|garage)\b", low):
        mm = re.search(
            r"\b(bedroom|kitchen|living\s+room|office|bathroom|garage)\b",
            low,
        )
        if mm:
            room = mm.group(1).replace(" ", "_")
            return {"tool": "smarthome", "action": "get_room_status", "params": {"room": room}}

    if re.search(r"\b(smart\s+home|house)\s+status\b|\bdevice\s+status\b", low):
        return {"tool": "smarthome", "action": "get_status", "params": {}}

    if re.search(
        r"\bturn\b.*\b(light|lights)\b|\bdim\b|\bset\b.*\b(thermostat|temperature)\b|\bmake\b.*\blights?\b|\block\b.*\bdoor|\bturn off everything\b",
        low,
    ):
        return {"tool": "smarthome", "action": "control_device", "params": {"command": m}}

    return None


class ToolRegistry:
    """Central registry: describe tools, route messages, execute, and summarize."""

    def __init__(
        self,
        brain: Any,
        file_skill: Any,
        shell_skill: Any,
        clipboard_skill: Any,
        app_skill: Any,
        code_skill: Any,
        search_skill: Any,
        skills_config: dict[str, Any] | None = None,
        smarthome_skill: Any | None = None,
        orchestrator: Any | None = None,
        emotion_agent: Any | None = None,
        self_improvement_agent: Any | None = None,
        lesson_store: Any | None = None,
        prompt_optimizer: Any | None = None,
        autonomous_agent: Any | None = None,
        autonomy_reporter: Any | None = None,
        finance_agent: Any | None = None,
        personal_kb: Any | None = None,
        plugin_registry: Any | None = None,
        notifier: Any | None = None,
    ) -> None:
        self.brain = brain
        self.file_skill = file_skill
        self.shell_skill = shell_skill
        self.clipboard_skill = clipboard_skill
        self.app_skill = app_skill
        self.code_skill = code_skill
        self.search_skill = search_skill
        self.smarthome_skill = smarthome_skill
        self.orchestrator = orchestrator
        self.emotion_agent = emotion_agent
        self.self_improvement_agent = self_improvement_agent
        self.lesson_store = lesson_store
        self.prompt_optimizer = prompt_optimizer
        self.autonomous_agent = autonomous_agent
        self.autonomy_reporter = autonomy_reporter
        self.finance_agent = finance_agent
        self.personal_kb = personal_kb
        self.plugin_registry = plugin_registry
        self.notifier = notifier
        self._call_counts: dict[str, int] = {}
        self._skills_config_override: dict[str, Any] | None = (
            dict(skills_config) if isinstance(skills_config, dict) else None
        )

    def get_call_counts(self) -> dict[str, int]:
        return self._call_counts

    def _skills_map(self) -> dict[str, Any]:
        if self._skills_config_override is not None:
            return self._skills_config_override
        cfg = getattr(self.brain, "_config", {}) or {}
        s = cfg.get("skills")
        return dict(s) if isinstance(s, dict) else {}

    def _enabled(self, key: str, default: bool = True) -> bool:
        return bool(self._skills_map().get(key, default))

    # FIXED: robust router JSON extraction (fences, brace slice, regex) + DEBUG logging
    def _extract_json(self, text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            logger.debug("Router JSON: empty raw response")
            return None
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                logger.debug("Router JSON: direct parse succeeded")
                return d
        except json.JSONDecodeError:
            pass
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if fence:
            inner = (fence.group(1) or "").strip()
            try:
                d = json.loads(inner)
                if isinstance(d, dict):
                    logger.debug("Router JSON: markdown fence parse succeeded")
                    return d
            except json.JSONDecodeError:
                pass
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            sub = raw[start : end + 1]
            try:
                d = json.loads(sub)
                if isinstance(d, dict):
                    logger.debug("Router JSON: first/last brace substring succeeded")
                    return d
            except json.JSONDecodeError:
                pass
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                d = json.loads(m.group(0))
                if isinstance(d, dict):
                    logger.debug("Router JSON: regex object pattern succeeded")
                    return d
            except json.JSONDecodeError:
                pass
        logger.debug("Router JSON extraction failed; raw response (truncated): %s", raw[:2000])
        return None

    def get_tools_description(self) -> str:
        lines: list[str] = [
            "Available tools:",
            "- files: list_dir, read_file, write_file, delete_file, copy_file, move_file, create_folder, find_files, get_file_info, open_file",
            "- shell: run, run_powershell, get_running_processes, kill_process, get_system_info, get_network_info",
            "- clipboard: read, write, append, clear",
            "- apps: open_app, open_url, add_alias, get_app_aliases",
            "- code: generate, explain, debug, review, convert, run_snippet",
            "- search: web_search",
            "- vision.screenshot: capture and describe the screen",
            "- vision.analyze_image: analyze an image path or URL",
            "- vision.read_screen: OCR the current screen",
            "- notify.remind: set a reminder with delay or exact time",
            "- direct: no tool needed, answer from knowledge",
        ]
        if self.smarthome_skill is not None and self._enabled("smarthome", True):
            lines.insert(-1, "- smarthome: control_device, get_status, run_routine, list_devices, get_room_status")
        if not self._enabled("file_control", True):
            lines = [x for x in lines if not x.startswith("- files:")]
        if not self._enabled("shell_control", True):
            lines = [x for x in lines if not x.startswith("- shell:")]
        if not self._enabled("clipboard", True):
            lines = [x for x in lines if not x.startswith("- clipboard:")]
        if not self._enabled("app_launcher", True):
            lines = [x for x in lines if not x.startswith("- apps:")]
        if not self._enabled("code_assistant", True):
            lines = [x for x in lines if not x.startswith("- code:")]
        if not self._enabled("web_search", True):
            lines = [x for x in lines if not x.startswith("- search:")]
        return "\n".join(lines)

    def _dispatch(self, tool: str, action: str, params: dict[str, Any], user_message: str = "") -> Any:
        if tool == "files":
            if not self._enabled("file_control", True):
                return {"success": False, "result": "", "error": "file_control skill is disabled"}
            return _invoke(
                self.file_skill, action, params, tool=tool, action=action, user_message=user_message
            )
        if tool == "shell":
            if not self._enabled("shell_control", True):
                return {"success": False, "stdout": "", "stderr": "shell_control skill is disabled", "returncode": -1, "duration": 0.0}
            return _invoke(
                self.shell_skill, action, params, tool=tool, action=action, user_message=user_message
            )
        if tool == "clipboard":
            if not self._enabled("clipboard", True):
                return {"success": False, "result": "", "error": "clipboard skill is disabled"}
            return _invoke(
                self.clipboard_skill, action, params, tool=tool, action=action, user_message=user_message
            )
        if tool == "apps":
            if not self._enabled("app_launcher", True):
                return {"success": False, "result": "", "error": "app_launcher skill is disabled"}
            return _invoke(self.app_skill, action, params, tool=tool, action=action, user_message=user_message)
        if tool == "code":
            if not self._enabled("code_assistant", True):
                return {"success": False, "result": "", "language": "", "error": "code_assistant skill is disabled"}
            return _invoke(self.code_skill, action, params, tool=tool, action=action, user_message=user_message)
        if tool == "search":
            if not self._enabled("web_search", True):
                return {"success": False, "result": "", "error": "web_search skill is disabled"}
            return _invoke(
                self.search_skill, action, params, tool=tool, action=action, user_message=user_message
            )
        if tool == "smarthome":
            if self.smarthome_skill is None or not self._enabled("smarthome", True):
                return {
                    "success": False,
                    "result": "",
                    "error": "Smart home is disabled or not configured. Enable smarthome in config.yaml.",
                }
            return _invoke(
                self.smarthome_skill, action, params, tool=tool, action=action, user_message=user_message
            )
        # Phase 2 tools — routed through orchestrator agents
        if tool in ("gmail", "browser", "github", "finance", "kb"):
            orch = getattr(self, "orchestrator", None)
            if orch is not None:
                agents_map = getattr(orch, "_agents", {}) or {}
                if tool == "kb":
                    # KB actions directly on personal_kb stored in orchestrator config
                    agent = agents_map.get("memory")
                    if agent is not None:
                        kb = getattr(agent, "_personal_kb", None) or getattr(agent, "personal_kb", None)
                        if kb is None:
                            return {"success": False, "result": "Personal KB not available.", "error": "KB not wired"}
                        if action == "add_contact":
                            return kb.add_contact(**{k: v for k, v in params.items() if k in ("name","email","phone","relationship","notes")})
                        if action == "find_contact":
                            return kb.find_contact(params.get("name", ""))
                        if action == "update_goal":
                            return kb.update_goal(params.get("goal", ""), int(params.get("progress", 0)))
                        if action == "get_profile":
                            return {"success": True, "result": str(kb.get_full_profile())}
                        return {"success": False, "error": f"Unknown kb action: {action}"}
                agent = agents_map.get(tool)
                if agent is not None:
                    task = {"action": action, "params": params, "description": f"{tool}.{action}"}
                    return agent.execute(task)
            return {"success": False, "result": f"{tool} agent not available.", "error": f"{tool} not initialized"}
        return {"success": False, "result": "", "error": f"Unknown tool: {tool}"}

    def _agents_enabled(self) -> bool:
        cfg = getattr(self.brain, "_config", {}) or {}
        ag = cfg.get("agents")
        return bool(isinstance(ag, dict) and ag.get("enabled", True))

    def _direct_chat(self, user_message: str, conversation_history: list[dict[str, str]]) -> dict[str, Any]:
        """Bypass all tools — go straight to LLM for conversational response."""
        try:
            response = self.brain.chat(conversation_history)
            return {
                "tool_used": "direct",
                "action": "direct",
                "raw_result": {},
                "final_response": response,
                "response": response,
            }
        except Exception as e:
            return {
                "tool_used": "direct",
                "action": "direct",
                "raw_result": {},
                "final_response": str(e),
                "response": str(e),
            }

    def _direct_chat_with_response(
        self,
        user_message: str,
        history: list[dict[str, str]],
        override_response: str,
    ) -> dict[str, Any]:
        _ = user_message
        _ = history
        return {
            "tool_used": "direct",
            "action": "direct",
            "raw_result": {},
            "final_response": override_response,
            "response": override_response,
        }

    def _guess_category(self, description: str) -> str:
        desc = description.lower()
        if any(w in desc for w in ["lunch", "dinner", "breakfast", "food", "restaurant", "chai", "coffee", "eat", "meal", "snack", "biryani", "pizza"]):
            return "food"
        if any(w in desc for w in ["uber", "ola", "auto", "cab", "bus", "train", "metro", "petrol", "fuel", "taxi"]):
            return "transport"
        if any(w in desc for w in ["movie", "netflix", "game", "concert", "party", "drink", "bar"]):
            return "entertainment"
        if any(w in desc for w in ["amazon", "flipkart", "clothes", "shirt", "shoes", "shopping", "order"]):
            return "shopping"
        if any(w in desc for w in ["doctor", "medicine", "pharmacy", "hospital", "gym", "health"]):
            return "health"
        if any(w in desc for w in ["course", "book", "udemy", "college", "school", "class"]):
            return "education"
        return "other"

    def route(self, user_message: str, conversation_history: list[dict[str, str]]) -> dict[str, Any]:
        """
        When an orchestrator is configured and enabled, run multi-agent routing first.
        On failure, use the legacy tool router (LLM JSON + skills).
        """
        msg_lower = (user_message or "").lower().strip()
        started = time.perf_counter()
        # #region agent log
        _debug_log(
            "skills/tools/registry.py:route_entry",
            "route called",
            {"msg_lower": msg_lower[:120], "has_autonomous": self.autonomous_agent is not None, "has_emotion": self.emotion_agent is not None},
            "run1",
            "H1",
        )
        # #endregion

        # Expense logging — intercept before orchestrator loses it
        expense_pattern = re.compile(
            r"(spent|paid|spend|cost me|costs?)\s+(?:rs\.?|rupees?|inr|₹)?\s*(\d+(?:\.\d+)?)\s*(?:rs\.?|rupees?|inr|₹)?\s*(?:on|for)?\s*(.*)",
            re.IGNORECASE,
        )
        match = expense_pattern.search(msg_lower)
        if match:
            amount = float(match.group(2))
            description = match.group(3).strip() or "misc"
            category = self._guess_category(description)
            if self.finance_agent:
                result = self.finance_agent.execute(
                    {
                        "action": "add_expense",
                        "params": {"amount": amount, "category": category, "description": description},
                    }
                )
                return {
                    "tool_used": "finance",
                    "action": "add_expense",
                    "raw_result": result,
                    "final_response": (
                        f"Logged ₹{amount:.0f} for {description} under {category}, Sir. "
                        "Running total for this month will be updated."
                    ),
                    "response": f"Logged ₹{amount:.0f} for {description} under {category}, Sir.",
                }

        # Explicit "remember" commands — save directly to PersonalKB
        remember_patterns = [
            r"remember that (.+)",
            r"my goal is (.+)",
            r"i want to (.+) by (december|january|february|march|next year|\d{4})",
            r"save (?:that|this)[:\s]+(.+)",
            r"note that (.+)",
            r"add (?:to )?my goals?[:\s]+(.+)",
        ]
        for pattern in remember_patterns:
            m = re.search(pattern, msg_lower)
            if m and self.personal_kb:
                fact = m.group(1).strip()
                if any(w in fact for w in ["goal", "launch", "build", "finish", "complete", "achieve", "want to", "plan to"]):
                    self.personal_kb.add(
                        "goals",
                        {
                            "goal": fact,
                            "deadline": m.group(2) if (m.lastindex and m.lastindex >= 2) else "unspecified",
                            "progress_pct": 0,
                            "status": "active",
                        },
                    )
                    return self._direct_chat_with_response(
                        user_message,
                        conversation_history,
                        f"Understood, Sir. I've saved your goal: '{fact}'. I'll keep track of this for you.",
                    )
                self.personal_kb.add(
                    "preferences",
                    {"preference": fact, "strength": 5, "category": "general"},
                )
                return self._direct_chat_with_response(
                    user_message,
                    conversation_history,
                    f"Noted, Sir. I've remembered: '{fact}'.",
                )

        # Slash command handling for autonomy/self-improvement.
        if msg_lower.startswith("/schedule ") and self.autonomous_agent is not None:
            body = user_message[len("/schedule ") :].strip()
            parsed = self.autonomous_agent.parse_natural_language_schedule(body)
            out = self.autonomous_agent.execute(
                {"action": "schedule", "params": {"name": body[:40] or "scheduled_task", "task_action": "direct", "params": {"text": body}, "schedule": parsed.get("schedule_str")}}
            )
            txt = f"Scheduled task created: {out.get('result')}"
            # #region agent log
            _debug_log(
                "skills/tools/registry.py:schedule_branch",
                "schedule branch completed",
                {"body": body[:120], "result": str(out)[:220]},
                "run1",
                "H3",
            )
            # #endregion
            return {"tool_used": "autonomous", "action": "schedule", "raw_result": out, "final_response": txt, "response": txt}
        if msg_lower.startswith("/monitor ") and self.autonomous_agent is not None:
            if msg_lower.strip() == "/monitor list":
                out = self.autonomous_agent.execute({"action": "list_monitors", "params": {}})
                txt = _tool_preview(out.get("result"))
                return {"tool_used": "autonomous", "action": "list_monitors", "raw_result": out, "final_response": txt, "response": txt}
            body = user_message[len("/monitor ") :].strip()
            parsed = self.autonomous_agent.parse_natural_language_condition(body)
            out = self.autonomous_agent.execute({"action": "monitor", "params": {"name": body[:40] or "monitor", **parsed}})
            txt = f"Monitor created: {out.get('result')}"
            return {"tool_used": "autonomous", "action": "monitor", "raw_result": out, "final_response": txt, "response": txt}
        if msg_lower == "/tasks" and self.autonomous_agent is not None:
            out = self.autonomous_agent.execute({"action": "list_tasks", "params": {}})
            txt = _tool_preview(out.get("result"))
            return {"tool_used": "autonomous", "action": "list_tasks", "raw_result": out, "final_response": txt, "response": txt}
        if msg_lower == "/daily" and self.autonomy_reporter is not None:
            txt = self.autonomy_reporter.get_daily_summary()
            return {"tool_used": "autonomous", "action": "daily_summary", "raw_result": {"summary": txt}, "final_response": txt, "response": txt}
        if msg_lower == "/brief" and self.autonomy_reporter is not None:
            txt = self.autonomy_reporter.generate_morning_brief()
            return {"tool_used": "autonomous", "action": "morning_brief", "raw_result": {"brief": txt}, "final_response": txt, "response": txt}
        if msg_lower == "/performance" and self.self_improvement_agent is not None:
            out = self.self_improvement_agent.get_performance_stats()
            txt = _tool_preview(out)
            return {"tool_used": "self_improvement", "action": "performance", "raw_result": out, "final_response": txt, "response": txt}
        if msg_lower in ("/mood", "show current mood") and self.emotion_agent is not None:
            out = self.emotion_agent.execute({"action": "get_mood", "params": {}})
            txt = _tool_preview(out.get("result"))
            return {"tool_used": "emotion", "action": "get_mood", "raw_result": out, "final_response": txt, "response": txt}

        emotion_info: dict[str, Any] = {}
        if self.emotion_agent is not None:
            try:
                logger.debug(f"[DEBUG EMOTION] Processing: {user_message[:50]}")
                emotion_info = self.emotion_agent.process_message(user_message)
            except Exception as e:
                logger.debug("emotion processing failed: %s", e)
                # #region agent log
                _debug_log(
                    "skills/tools/registry.py:emotion_exception",
                    "emotion processing failed",
                    {"error": str(e)[:220]},
                    "run1",
                    "H4",
                )
                # #endregion

        greet_starts = (
            "hello",
            "hi ",
            "hey",
            "good morning",
            "good night",
            "how are you",
        )
        if any(msg_lower.startswith(w) for w in greet_starts) or msg_lower in ("hi", "hey"):
            return self._direct_chat(user_message, conversation_history)

        planning_keywords = [
            "make a plan",
            "create a plan",
            "30 day",
            "roadmap",
            "how do i learn",
            "how to become",
            "step by step guide",
            "teach me how",
        ]
        if any(kw in msg_lower for kw in planning_keywords):
            return self._direct_chat(user_message, conversation_history)

        if re.match(r"^[\d\s\+\-\*\/\(\)\.\^%]+$", msg_lower):
            return self._direct_chat(user_message, conversation_history)

        vision_keywords = [
            "screenshot",
            "screen",
            "what's on my",
            "whats on my",
            "take a picture",
            "analyze image",
            "read image",
            "what do you see",
        ]
        if any(kw in msg_lower for kw in vision_keywords) and self.orchestrator is not None:
            try:
                out = self.orchestrator.route(user_message, conversation_history)
                resp = (out.get("response") or "").strip()
                return {
                    "tool_used": "orchestrator",
                    "action": "agents",
                    "raw_result": out,
                    "final_response": resp,
                    "response": resp,
                    "agents_used": list(out.get("agents_used") or []),
                    "tasks_completed": int(out.get("tasks_completed") or 0),
                }
            except Exception:
                pass

        reminder_keywords = ["remind me", "set a reminder", "notify me", "alert me", "reminder for"]
        if any(kw in msg_lower for kw in reminder_keywords) and self.orchestrator is not None:
            try:
                out = self.orchestrator.route(user_message, conversation_history)
                resp = (out.get("response") or "").strip()
                return {
                    "tool_used": "orchestrator",
                    "action": "agents",
                    "raw_result": out,
                    "final_response": resp,
                    "response": resp,
                    "agents_used": list(out.get("agents_used") or []),
                    "tasks_completed": int(out.get("tasks_completed") or 0),
                }
            except Exception:
                pass

        # Plugin registry routing (before orchestrator)
        if self.plugin_registry:
            try:
                command = user_message.strip().split(" ", 1)[0] if user_message.strip().startswith("/") else user_message
                args = user_message.strip().split(" ", 1)[1] if user_message.strip().startswith("/") and " " in user_message.strip() else ""
                plugin_result = self.plugin_registry.route(
                    command,
                    args,
                    {
                        "user_message": user_message,
                        "conversation_history": conversation_history,
                        "mood": str((emotion_info or {}).get("mood") or "neutral"),
                        "session_id": "default",
                    },
                )
                if plugin_result:
                    k = f"plugin.{plugin_result['plugin']}"
                    self._call_counts[k] = self._call_counts.get(k, 0) + 1
                    return {
                        "tool_used": k,
                        "action": "execute",
                        "raw_result": plugin_result,
                        "final_response": plugin_result["response"],
                        "response": plugin_result["response"],
                    }
            except Exception as e:
                logger.warning("Plugin routing failed: %s", e)

        if self.orchestrator is not None and self._agents_enabled():
            try:
                out = self.orchestrator.route(user_message, conversation_history)
                resp = (out.get("response") or "").strip()
                return {
                    "tool_used": "orchestrator",
                    "action": "agents",
                    "raw_result": out,
                    "final_response": resp,
                    "response": resp,
                    "agents_used": list(out.get("agents_used") or []),
                    "tasks_completed": int(out.get("tasks_completed") or 0),
                }
            except Exception as e:
                logger.warning("Orchestrator routing failed; falling back to skills: %s", e)

        out = self._route_legacy(user_message, conversation_history)
        mood = str(emotion_info.get("mood") or "neutral")
        if self.emotion_agent is not None and mood != "neutral":
            greet = self.emotion_agent.tone_adapter.get_greeting_style(mood)
            if greet:
                out["final_response"] = f"{greet} {out.get('final_response','')}".strip()
                out["response"] = out["final_response"]
        if self.self_improvement_agent is not None:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.self_improvement_agent.post_response_hook_async(
                user_message,
                str(out.get("final_response") or ""),
                str(out.get("tool_used") or ""),
                duration_ms,
            )
            # #region agent log
            _debug_log(
                "skills/tools/registry.py:post_hook_async",
                "post response hook queued",
                {"tool_used": str(out.get("tool_used")), "duration_ms": duration_ms},
                "run1",
                "H2",
            )
            # #endregion
        if out.get("tool_used"):
            self._call_counts[out["tool_used"]] = self._call_counts.get(out["tool_used"], 0) + 1
        return out

    def _route_legacy(self, user_message: str, conversation_history: list[dict[str, str]]) -> dict[str, Any]:
        """
        Plan with the LLM, optionally run a tool, then ask the LLM to summarize.

        On any routing failure, falls back to ``brain.chat(conversation_history)``.
        """
        try:
            if self.smarthome_skill is not None and self._enabled("smarthome", True):
                fast = _smarthome_keyword_route(user_message)
                if fast:
                    raw_result = self._dispatch(
                        str(fast["tool"]),
                        str(fast["action"]),
                        fast.get("params") if isinstance(fast.get("params"), dict) else {},
                        user_message,
                    )
                    summarize_messages = [
                        {
                            "role": "system",
                            "content": (
                                "You are Jarvis. Summarize the tool outcome clearly for the user in natural language. "
                                "If the tool failed, explain briefly and suggest a fix."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"User request:\n{user_message}\n\n"
                                f"Tool: {fast['tool']}.{fast['action']}\n"
                                f"Result (JSON or text):\n{_tool_preview(raw_result)}"
                            ),
                        },
                    ]
                    final_response = self.brain.chat(summarize_messages)
                    if not (final_response or "").strip():
                        final_response = _tool_preview(raw_result)
                    fr = final_response.strip()
                    return {
                        "tool_used": str(fast["tool"]),
                        "action": str(fast["action"]),
                        "raw_result": raw_result if isinstance(raw_result, dict) else {"value": raw_result},
                        "final_response": fr,
                        "response": fr,
                    }

            tools_txt = self.get_tools_description()
            hist_txt = _format_history_snippet(conversation_history)
            recent_failures = self.lesson_store.get_relevant_lessons(user_message, limit=3) if self.lesson_store is not None else []
            router_system = ROUTER_SYSTEM
            if self.prompt_optimizer is not None:
                router_system = self.prompt_optimizer.optimize_routing_prompt(router_system, recent_failures)
            router_messages: list[dict[str, str]] = [
                {"role": "system", "content": f"{router_system}\n\n{tools_txt}"},
                {
                    "role": "user",
                    "content": (
                        f"Conversation context:\n{hist_txt}\n\n"
                        f"Latest user message:\n{user_message}\n\n"
                        "Return the JSON decision now."
                    ),
                },
            ]
            raw_plan = self.brain.chat(router_messages)
            decision = self._extract_json(raw_plan or "")

            if not decision:
                logger.warning("Router returned invalid JSON; falling back to direct chat.")
                final = self.brain.chat(conversation_history)
                return {
                    "tool_used": "chat",
                    "action": "brain.chat",
                    "raw_result": {},
                    "final_response": final,
                    "response": final,
                }

            # FIXED: support router returning tool "direct" (new prompt) plus legacy direct_response flag
            if decision.get("direct_response") is True or str(decision.get("tool", "")).lower().strip() == "direct":
                final = (decision.get("final_response") or decision.get("answer") or "").strip()
                if not final:
                    final = self.brain.chat(conversation_history)
                return {
                    "tool_used": "direct",
                    "action": str(decision.get("action", "none")),
                    "raw_result": {},
                    "final_response": final,
                    "response": final,
                }

            tool = str(decision.get("tool", "")).lower().strip()
            action = str(decision.get("action", "")).strip()
            params = decision.get("params")
            if not isinstance(params, dict):
                params = {}

            if not tool or not action:
                final = self.brain.chat(conversation_history)
                return {
                    "tool_used": "chat",
                    "action": "brain.chat",
                    "raw_result": {},
                    "final_response": final,
                    "response": final,
                }

            # FIXED: merge router text params from user_message before dispatch
            _inject_router_text_params(tool, action, params, user_message)

            raw_result = self._dispatch(tool, action, params, user_message)

            summarize_messages = [
                {
                    "role": "system",
                    "content": (
                        "You are Jarvis. Summarize the tool outcome clearly for the user in natural language. "
                        "If the tool failed, explain briefly and suggest a fix."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{user_message}\n\n"
                        f"Tool: {tool}.{action}\n"
                        f"Result (JSON or text):\n{_tool_preview(raw_result)}"
                    ),
                },
            ]
            final_response = self.brain.chat(summarize_messages)
            if not (final_response or "").strip():
                final_response = _tool_preview(raw_result)

            fr = final_response.strip()
            return {
                "tool_used": tool,
                "action": action,
                "raw_result": raw_result if isinstance(raw_result, dict) else {"value": raw_result},
                "final_response": fr,
                "response": fr,
            }
        except Exception as e:
            logger.exception("ToolRegistry.route failed: %s", e)
            try:
                final = self.brain.chat(conversation_history)
            except Exception as e2:
                logger.exception("Fallback brain.chat failed: %s", e2)
                final = f"I encountered an error: {e}"
            return {
                "tool_used": "chat",
                "action": "brain.chat",
                "raw_result": {"error": str(e)},
                "final_response": final,
                "response": final,
            }


if __name__ == "__main__":
    print("ToolRegistry module loaded; run Jarvis main for integration tests.")
