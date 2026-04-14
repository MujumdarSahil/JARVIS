"""
Long-term memory: extract, recall, forget; deduplicated by fact hash.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any

try:
    from bson import ObjectId
except ImportError:
    ObjectId = None  # type: ignore[misc, assignment]

from core.agents.base_agent import BaseAgent
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database

logger = get_logger(__name__)

_CATEGORIES = (
    "personal_info | preferences | work | family | health | interests | routines | goals"
)


def _extract_json_array(text: str) -> list | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if m:
        try:
            v = json.loads(m.group(1).strip())
            return v if isinstance(v, list) else None
        except json.JSONDecodeError:
            pass
    start, end = raw.find("["), raw.rfind("]")
    if start >= 0 and end > start:
        try:
            v = json.loads(raw[start : end + 1])
            return v if isinstance(v, list) else None
        except json.JSONDecodeError:
            pass
    return None


class MemoryAgent(BaseAgent):
    """Extract, store, retrieve long-term memories."""

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        return self._run_wrapped(task, lambda: self._execute_impl(task))

    def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        action = str(params.get("action") or "recall").lower()
        if action == "extract":
            conv = params.get("conversation")
            if not isinstance(conv, list):
                conv = []
            facts = self.extract_memories(conv)
            return {"success": True, "result": facts}
        if action == "recall":
            q = str(params.get("query") or task.get("description") or "")
            lim = int(params.get("limit") or 5)
            return {"success": True, "result": self.recall(q, lim)}
        if action == "forget":
            mid = str(params.get("memory_id") or "")
            return self._forget(mid)
        return {"success": False, "result": "Unknown memory action."}

    def _forget(self, memory_id: str) -> dict[str, Any]:
        if not memory_id or not self.db.available:
            return {"success": False, "result": "Cannot delete."}
        try:
            q: dict[str, Any] = {}
            if ObjectId is not None and ObjectId.is_valid(memory_id):
                q["_id"] = ObjectId(memory_id)
            else:
                q["text_hash"] = memory_id
            n = self.db.delete("memories", q)
            return {"success": n > 0, "result": f"deleted:{n}"}
        except Exception as e:
            return {"success": False, "result": str(e)}

    def extract_memories(self, conversation: list) -> list:
        lines = []
        for m in conversation[-40:]:
            if isinstance(m, dict):
                role = m.get("role", "")
                content = (m.get("content") or "").strip()
                if content and role in ("user", "assistant"):
                    lines.append(f"{role}: {content}")
        blob = "\n".join(lines)
        if not blob.strip():
            return []
        prompt = (
            f"Extract all personal facts, preferences, and important information the user shared. "
            f"Categories: {_CATEGORIES}. "
            "Return ONLY a JSON array of objects: "
            '[{"fact": str, "category": str, "importance": 1-10}]\n\n'
            f"Conversation:\n{blob}"
        )
        raw = self.think(prompt, system="Return valid JSON only.")
        arr = _extract_json_array(raw) or []
        out: list[dict[str, Any]] = []
        min_imp = int(self.config.get("min_importance") or 5)
        for item in arr:
            if not isinstance(item, dict):
                continue
            fact = str(item.get("fact") or "").strip()
            if not fact:
                continue
            imp = int(item.get("importance") or 5)
            if imp < min_imp:
                continue
            cat = str(item.get("category") or "personal_info")
            self.save_memory(fact, cat, imp, tags=[cat])
            out.append({"fact": fact, "category": cat, "importance": imp})
        return out

    def save_memory(self, fact: str, category: str, importance: int, tags: list | None = None) -> str:
        tags = list(tags or [])
        h = self.brain.text_fingerprint(fact)
        if self.db.available:
            try:
                existing = self.db.find_one("memories", {"text_hash": h, "user_id": "default"})
                if existing:
                    return str(existing.get("_id", ""))
                oid = self.db.insert(
                    "memories",
                    {
                        "user_id": "default",
                        "content": fact[:8000],
                        "tags": tags + ["memory_agent"],
                        "importance_score": int(importance),
                        "category": category,
                        "text_hash": h,
                        "timestamp": time.time(),
                        "source": "memory_agent",
                    },
                )
                return oid
            except Exception as e:
                logger.warning("save_memory: %s", e)
        return ""

    def recall(self, query: str, limit: int = 5) -> list:
        if not self.db.available:
            return []
        try:
            rows = self.db.text_search("memories", query, limit=limit)
            if not rows:
                rows = self.db.find(
                    "memories",
                    {"content": {"$regex": re.escape(query[:80]), "$options": "i"}},
                    limit=limit,
                    sort=[("importance_score", -1)],
                )
            slim = []
            for r in rows[:limit]:
                slim.append(
                    {
                        "id": str(r.get("_id", "")),
                        "content": r.get("content"),
                        "category": r.get("category"),
                        "importance_score": r.get("importance_score"),
                        "tags": r.get("tags"),
                    }
                )
            return slim
        except Exception as e:
            logger.warning("recall: %s", e)
            return []

    def get_user_profile(self) -> dict[str, Any]:
        if not self.db.available:
            return {"summary": "", "facts": []}
        try:
            rows = self.db.find(
                "memories",
                {"user_id": "default", "importance_score": {"$gte": 7}},
                limit=30,
                sort=[("importance_score", -1)],
            )
            facts = [str(r.get("content") or "") for r in rows if r.get("content")]
            prompt = (
                "Summarize this user profile in 5-8 bullet points for an assistant context.\n\n"
                + "\n".join(f"- {f}" for f in facts[:25])
            )
            summary = self.think(prompt, system="Be concise; third person.")
            return {"summary": summary, "facts": facts}
        except Exception as e:
            logger.warning("get_user_profile: %s", e)
            return {"summary": "", "facts": []}

    def run_periodic_extraction(self, conversation_history: list) -> None:
        try:
            self.extract_memories(conversation_history)
        except Exception as e:
            logger.warning("periodic extraction: %s", e)


if __name__ == "__main__":
    print("MemoryAgent module OK")
