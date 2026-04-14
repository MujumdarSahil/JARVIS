"""
Task / goal planner: structured plans stored in MongoDB.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

from core.agents.base_agent import BaseAgent
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database

logger = get_logger(__name__)


def _extract_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


class PlannerAgent(BaseAgent):
    """Breaks goals into steps; persists plans."""

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        return self._run_wrapped(task, lambda: self._execute_impl(task))

    def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        goal = str(params.get("goal") or task.get("description") or "")
        ctx = str(params.get("context") or "")
        if not goal:
            return {"success": False, "result": "No goal provided."}
        plan = self.create_plan(goal, ctx)
        return {"success": True, "result": plan}

    def create_plan(self, goal: str, context: str = "") -> dict[str, Any]:
        prompt = (
            "You are an experienced project manager. Break the goal into actionable steps. "
            "Return ONLY valid JSON with this shape:\n"
            '{"goal": str, "steps": [{"step_number": int, "action": str, "description": str, '
            '"estimated_time": str, "dependencies": [int], "status": "pending"}], '
            '"total_estimated_time": str, "complexity": "low"|"medium"|"high"}\n\n'
            f"Goal: {goal}\nContext: {context}"
        )
        raw = self.brain.chat(
            [
                {"role": "system", "content": "You are a project manager. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            parsed = {
                "goal": goal,
                "steps": [],
                "total_estimated_time": "unknown",
                "complexity": "medium",
                "raw": raw[:4000],
            }
        plan_id = uuid.uuid4().hex
        parsed["plan_id"] = plan_id
        parsed.setdefault("steps", [])
        for i, s in enumerate(parsed["steps"]):
            if isinstance(s, dict) and "status" not in s:
                s["status"] = "pending"
            if isinstance(s, dict):
                s["step_number"] = s.get("step_number", i + 1)

        if self.db.available:
            self.db.insert(
                "memories",
                {
                    "user_id": "default",
                    "content": json.dumps(parsed, ensure_ascii=False)[:20000],
                    "tags": ["plan"],
                    "importance_score": 7,
                    "timestamp": time.time(),
                    "plan_id": plan_id,
                    "plan_status": "active",
                    "goal": goal[:2000],
                    "source": "planner_agent",
                },
            )
        return parsed

    def get_active_plans(self) -> list:
        if not self.db.available:
            return []
        try:
            rows = self.db.find(
                "memories",
                {"tags": "plan", "plan_status": {"$ne": "completed"}},
                limit=20,
                sort=[("timestamp", -1)],
            )
            return rows
        except Exception as e:
            logger.warning("get_active_plans: %s", e)
            return []

    def update_plan_progress(self, plan_id: str, step: int, status: str) -> None:
        if not self.db.available or not plan_id:
            return
        try:
            doc = self.db.find_one("memories", {"plan_id": plan_id})
            if not doc:
                return
            raw = doc.get("content") or "{}"
            data = json.loads(raw) if isinstance(raw, str) else {}
            if not isinstance(data, dict):
                return
            for s in data.get("steps") or []:
                if isinstance(s, dict) and int(s.get("step_number") or -1) == int(step):
                    s["status"] = status
            all_done = all(
                isinstance(s, dict) and s.get("status") in ("done", "skipped")
                for s in (data.get("steps") or [])
            )
            new_status = "completed" if all_done and data.get("steps") else doc.get("plan_status", "active")
            self.db.update(
                "memories",
                {"plan_id": plan_id},
                {
                    "content": json.dumps(data, ensure_ascii=False)[:20000],
                    "plan_status": new_status,
                },
            )
        except Exception as e:
            logger.warning("update_plan_progress: %s", e)


if __name__ == "__main__":
    print("PlannerAgent module OK")
