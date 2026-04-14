"""
Master planner: multi-agent routing, parallel/sequential execution, synthesis.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from core.agents.base_agent import BaseAgent
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database

logger = get_logger(__name__)

_MAX_PARALLEL = 3


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if m:
        try:
            d = json.loads(m.group(1).strip())
            return d if isinstance(d, dict) else None
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            d = json.loads(raw[start : end + 1])
            return d if isinstance(d, dict) else None
        except json.JSONDecodeError:
            pass
    return None


PLANNING_PROMPT_TEMPLATE = """You are the Jarvis orchestrator. Decide how to handle the user's latest message using specialized agents.

Available agents (use exact agent keys in JSON):
- research — Deep web research, news, fact-checking, multi-source summaries, "look up", "what happened", current events, verifying claims.
  Examples: "Research quantum error correction papers", "What is the latest on SpaceX Starship?", "Fact-check: does coffee dehydrate you?"
- coder — Code generation, debugging, review, explain code, run small Python snippets, convert between languages.
  Examples: "Write a Python script to parse CSV", "Fix this traceback", "Explain this function line by line", "Run this Python code"
- planner — Break a complex goal into ordered steps with time estimates; project-style planning.
  Examples: "Plan my weekend move to a new apartment", "Break down building a REST API from scratch"
- memory — Recall stored user preferences/facts, extract and save long-term memories, "what do you know about me?"
  Examples: "Remember I prefer dark mode", "What did I tell you about my job?", "Recall my dietary restrictions"
- tool_router — Files, shell, clipboard, apps, smart home via Jarvis tools (NOT web/code/planning).
  Examples: "List my Downloads folder", "Run ipconfig", "Open Chrome", "Turn on living room lights", "Read file X"

Routing patterns (learn these):
- "Hello" / small talk / general trivia with no tools → direct_answer true
- Weather / sports scores / news → research (or tool_router only if user asked to open an app)
- Pure coding → coder
- Multi-part: "search X and then write code for Y" → sequential: research then coder
- Independent: "summarize Python decorators AND look up today's gold price" → parallel: coder + research
- Smart home / local files / terminal → tool_router
- "Plan how to …" without immediate execution → planner first, optionally sequential with other agents

Return ONLY valid JSON:
{
  "strategy": "single" | "parallel" | "sequential",
  "agents": ["research"|"coder"|"planner"|"memory"|"tool_router"],
  "subtasks": [
    {
      "agent": "research",
      "description": "short label",
      "params": {},
      "depends_on": []
    }
  ],
  "direct_answer": true | false
}

For tool_router subtasks, set params to: {"tool": "smarthome", "action": "control_device", "inner_params": {"command": "..."}}
Use inner_params for the tool's param dict (maps to registry dispatch).

If direct_answer is true, set subtasks to [] and agents to [].

User message and context will follow.
"""


class Orchestrator(BaseAgent):
    """Coordinates specialized agents and optional tool dispatch."""

    def __init__(
        self,
        brain: Brain,
        db: Database,
        config: dict[str, Any],
        agents: dict[str, Any],
        registry: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__("orchestrator", brain, db, config, kwargs.get("event_bus"))
        self._agents = dict(agents)
        self._registry = registry
        self._max_parallel = min(int(config.get("max_parallel_tasks") or _MAX_PARALLEL), _MAX_PARALLEL)
        self._planning_model = str(config.get("planning_model") or "groq")
        self._synthesis_model = str(config.get("synthesis_model") or "groq")

    def get_agents_description(self) -> str:
        return (
            "research: web research, fact-check, deep dives\n"
            "coder: code write/debug/explain/review/run Python\n"
            "planner: structured multi-step plans\n"
            "memory: recall/save user-specific long-term facts\n"
            "tool_router: files, shell, clipboard, apps, smarthome via existing Jarvis tools"
        )

    def plan(self, user_message: str, history: list[dict[str, str]]) -> dict[str, Any]:
        hist_txt = ""
        for m in history[-16:]:
            if m.get("role") == "system":
                continue
            hist_txt += f"{m.get('role')}: {(m.get('content') or '')[:1200]}\n"
        prompt = (
            f"{PLANNING_PROMPT_TEMPLATE}\n\n"
            f"Available agents summary:\n{self.get_agents_description()}\n\n"
            f"Conversation context:\n{hist_txt}\n"
            f"Latest user message:\n{user_message}\n\n"
            "Return the JSON plan now."
        )
        raw = self.brain.chat(
            [
                {"role": "system", "content": "You output only valid JSON. No markdown fences or commentary."},
                {"role": "user", "content": prompt},
            ],
            preferred_provider=self._planning_model,
        )
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            return {
                "strategy": "single",
                "agents": [],
                "subtasks": [],
                "direct_answer": True,
                "parse_error": True,
            }
        parsed.setdefault("strategy", "single")
        parsed.setdefault("agents", [])
        parsed.setdefault("subtasks", [])
        parsed.setdefault("direct_answer", False)
        if not isinstance(parsed["subtasks"], list):
            parsed["subtasks"] = []
        return parsed

    def _dispatch_tool(self, st: dict[str, Any], user_message: str) -> dict[str, Any]:
        params = st.get("params") if isinstance(st.get("params"), dict) else {}
        tool = str(params.get("tool") or "").lower().strip()
        action = str(params.get("action") or "").strip()
        inner = params.get("inner_params")
        if not isinstance(inner, dict):
            inner = {}
        if not self._registry or not tool or not action:
            return {"success": False, "result": "tool_router unavailable or missing tool/action"}
        try:
            raw = self._registry._dispatch(tool, action, inner, user_message)
            return {"success": True, "result": raw, "agent": "tool_router", "task_id": "", "duration_ms": 0}
        except Exception as e:
            logger.exception("tool_router dispatch: %s", e)
            return {"success": False, "result": str(e), "agent": "tool_router", "task_id": "", "duration_ms": 0}

    def _run_agent_subtask(self, st: dict[str, Any], user_message: str, prior_ctx: str) -> dict[str, Any]:
        name = str(st.get("agent") or "").lower().strip()
        tid = uuid.uuid4().hex
        task = {
            "id": tid,
            "type": name,
            "description": str(st.get("description") or user_message),
            "params": st.get("params") if isinstance(st.get("params"), dict) else {},
            "context": prior_ctx,
        }
        if name == "tool_router":
            agent = None
        else:
            agent = self._agents.get(name)
        if agent is None and name != "tool_router":
            return {
                "success": False,
                "result": f"Unknown agent: {name}",
                "agent": name,
                "task_id": tid,
                "duration_ms": 0,
            }
        if name == "tool_router":
            return self._dispatch_tool(st, user_message)
        return agent.execute(task)

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Orchestrate from a structured task (same as route with embedded message)."""
        msg = str(task.get("description") or "")
        hist = task.get("context")
        if not isinstance(hist, list):
            hist = []
        out = self.route(msg, hist)
        return {
            "success": True,
            "result": out.get("response", ""),
            "agent": self.name,
            "task_id": str(task.get("id") or ""),
            "duration_ms": out.get("total_duration_ms", 0),
            "agents_used": out.get("agents_used", []),
            "tasks_completed": out.get("tasks_completed", 0),
        }

    def route(self, user_message: str, history: list[dict[str, str]]) -> dict[str, Any]:
        t0 = time.perf_counter()
        agents_used: list[str] = []
        tasks_completed = 0
        self.report_status("running", "planning")

        plan = self.plan(user_message, history)
        try:
            if plan.get("direct_answer"):
                self.report_status("running", "direct answer")
                reply = self.brain.chat(list(history), preferred_provider=self._synthesis_model)
                if not (reply or "").strip():
                    reply = self.brain.chat(
                        [
                            {"role": "system", "content": "You are Jarvis. Reply helpfully and concisely."},
                            {"role": "user", "content": user_message},
                        ],
                        preferred_provider=self._synthesis_model,
                    )
                dt = int((time.perf_counter() - t0) * 1000)
                self._log_orchestrator_run(
                    user_message,
                    plan,
                    reply,
                    agents_used,
                    tasks_completed,
                    dt,
                )
                self.report_status("idle", "")
                return {
                    "response": reply.strip(),
                    "agents_used": agents_used,
                    "tasks_completed": tasks_completed,
                    "total_duration_ms": dt,
                }

            subtasks = plan.get("subtasks") or []
            strategy = str(plan.get("strategy") or "single").lower()
            results: list[dict[str, Any]] = []
            prior = ""

            if strategy == "parallel":
                self.report_status("running", "parallel agents")
                workers = min(self._max_parallel, max(1, len(subtasks)))
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = []
                    for st in subtasks:
                        if not isinstance(st, dict):
                            continue
                        futs.append(ex.submit(self._run_agent_subtask, st, user_message, ""))
                    for fut in as_completed(futs):
                        r = fut.result()
                        results.append(r)
                        ag = str(r.get("agent") or "")
                        if ag and ag not in agents_used:
                            agents_used.append(ag)
                        if r.get("success"):
                            tasks_completed += 1
            elif strategy == "sequential":
                self.report_status("running", "sequential agents")
                for st in subtasks:
                    if not isinstance(st, dict):
                        continue
                    r = self._run_agent_subtask(st, user_message, prior)
                    results.append(r)
                    ag = str(r.get("agent") or st.get("agent") or "")
                    if ag and ag not in agents_used:
                        agents_used.append(ag)
                    if r.get("success"):
                        tasks_completed += 1
                    prior = f"{prior}\n\n{r.get('result')}"[:8000]
            else:
                self.report_status("running", "single agent")
                if not subtasks:
                    reply = self.brain.chat(
                        list(history),
                        preferred_provider=self._synthesis_model,
                    )
                    dt = int((time.perf_counter() - t0) * 1000)
                    self._log_orchestrator_run(user_message, plan, reply, [], 0, dt)
                    self.report_status("idle", "")
                    return {
                        "response": (reply or "").strip(),
                        "agents_used": [],
                        "tasks_completed": 0,
                        "total_duration_ms": dt,
                    }
                st = subtasks[0]
                if not isinstance(st, dict):
                    st = {"agent": "research", "description": user_message, "params": {}}
                r = self._run_agent_subtask(st, user_message, "")
                results.append(r)
                ag = str(r.get("agent") or st.get("agent") or "")
                if ag:
                    agents_used.append(ag)
                if r.get("success"):
                    tasks_completed = 1

            if not results:
                reply = self.brain.chat(
                    list(history),
                    preferred_provider=self._synthesis_model,
                )
                dt = int((time.perf_counter() - t0) * 1000)
                self._log_orchestrator_run(user_message, plan, reply, agents_used, tasks_completed, dt)
                self.report_status("idle", "")
                return {
                    "response": (reply or "").strip(),
                    "agents_used": agents_used,
                    "tasks_completed": tasks_completed,
                    "total_duration_ms": dt,
                }

            synth_in = json.dumps(results, ensure_ascii=False, default=str)[:12000]
            self.report_status("running", "synthesis")
            syn_prompt = (
                "Synthesize the following agent results into one clear, cohesive answer for the user. "
                "Attribute insights by role when helpful. User request:\n"
                f"{user_message}\n\nAgent results (JSON):\n{synth_in}"
            )
            final = self.brain.chat(
                [
                    {"role": "system", "content": "You are Jarvis, integrating specialist outputs."},
                    {"role": "user", "content": syn_prompt},
                ],
                preferred_provider=self._synthesis_model,
            )
            if not (final or "").strip():
                final = "\n\n".join(str(r.get("result") or "") for r in results)

            dt = int((time.perf_counter() - t0) * 1000)
            self._log_orchestrator_run(
                user_message,
                plan,
                final,
                agents_used,
                tasks_completed,
                dt,
            )
            self.report_status("idle", "")
            return {
                "response": (final or "").strip(),
                "agents_used": agents_used,
                "tasks_completed": tasks_completed,
                "total_duration_ms": dt,
            }
        except Exception as e:
            logger.exception("Orchestrator.route: %s", e)
            self.report_status("idle", "")
            dt = int((time.perf_counter() - t0) * 1000)
            return {
                "response": f"Orchestration error: {e}",
                "agents_used": agents_used,
                "tasks_completed": tasks_completed,
                "total_duration_ms": dt,
            }

    def _log_orchestrator_run(
        self,
        user_message: str,
        plan: dict[str, Any],
        final_text: str,
        agents_used: list[str],
        tasks_completed: int,
        duration_ms: int,
    ) -> None:
        try:
            self.db.insert(
                "agent_logs",
                {
                    "agent_name": self.name,
                    "task_id": uuid.uuid4().hex,
                    "task_description": user_message[:2000],
                    "started_at": time.time(),
                    "finished_at": time.time(),
                    "duration_ms": duration_ms,
                    "success": True,
                    "result_summary": (final_text or "")[:2000],
                    "model_used": self.brain.get_active_provider() or "",
                    "tokens_estimated": self.brain.estimate_tokens(user_message + final_text),
                    "timestamp": time.time(),
                    "plan": json.dumps(plan, default=str)[:4000],
                    "agents_used": agents_used,
                    "tasks_completed": tasks_completed,
                },
            )
        except Exception as e:
            logger.debug("orchestrator log: %s", e)


if __name__ == "__main__":
    print("Orchestrator module OK")
