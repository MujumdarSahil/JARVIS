"""
Routes natural-language requests to concrete skills using the LLM as a JSON planner.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

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

RULES:
- Use "direct" for greetings, math, general knowledge, opinions, anything that doesn't need a tool
- Use "search" ONLY when the user needs current/live information (news, prices, scores, weather)
- Use "code" when user says "write", "create", "generate", "make" + any code-related words
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
    ) -> None:
        self.brain = brain
        self.file_skill = file_skill
        self.shell_skill = shell_skill
        self.clipboard_skill = clipboard_skill
        self.app_skill = app_skill
        self.code_skill = code_skill
        self.search_skill = search_skill
        self.smarthome_skill = smarthome_skill
        self._skills_config_override: dict[str, Any] | None = (
            dict(skills_config) if isinstance(skills_config, dict) else None
        )

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
        return {"success": False, "result": "", "error": f"Unknown tool: {tool}"}

    def route(self, user_message: str, conversation_history: list[dict[str, str]]) -> dict[str, Any]:
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
                    return {
                        "tool_used": str(fast["tool"]),
                        "action": str(fast["action"]),
                        "raw_result": raw_result if isinstance(raw_result, dict) else {"value": raw_result},
                        "final_response": final_response.strip(),
                    }

            tools_txt = self.get_tools_description()
            hist_txt = _format_history_snippet(conversation_history)
            router_messages: list[dict[str, str]] = [
                {"role": "system", "content": f"{ROUTER_SYSTEM}\n\n{tools_txt}"},
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

            return {
                "tool_used": tool,
                "action": action,
                "raw_result": raw_result if isinstance(raw_result, dict) else {"value": raw_result},
                "final_response": final_response.strip(),
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
            }


if __name__ == "__main__":
    print("ToolRegistry module loaded; run Jarvis main for integration tests.")
