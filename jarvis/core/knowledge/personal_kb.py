"""
Personal Knowledge Base — stores contacts, habits, goals, preferences, and work context.

Primary storage: MongoDB personal_kb collection.
Used to personalize Jarvis responses and inject user context into system prompts.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from utils.logger import get_logger

if TYPE_CHECKING:
    from core.db import Database
    from core.brain import Brain

logger = get_logger(__name__)

CATEGORIES = frozenset([
    "contacts", "habits", "goals", "preferences",
    "work", "health", "finances", "routines",
])


class PersonalKB:
    """Stores and retrieves personal facts about the user."""

    def __init__(self, db: "Database", brain: "Brain") -> None:
        self._db = db
        self._brain = brain
        self._use_db = bool(getattr(db, "available", False))
        self._cache: dict[str, list] = {}
        self._cache_ts: float = 0.0
        self._lock = threading.Lock()
        self._json_path = Path(__file__).resolve().parents[2] / "personal_kb.json"
        if not self._use_db:
            self._load_json()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def _load_json(self) -> None:
        try:
            self._json_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._json_path.exists():
                self._json_path.write_text("[]", encoding="utf-8")
            rows = json.loads(self._json_path.read_text(encoding="utf-8"))
            self._cache = {}
            for row in rows if isinstance(rows, list) else []:
                cat = str(row.get("category") or "").lower()
                if not cat:
                    continue
                self._cache.setdefault(cat, []).append(row)
        except Exception as e:
            logger.warning("Could not load personal_kb.json: %s", e)
            self._cache = {}

    def _save_json(self) -> None:
        try:
            rows: list[dict[str, Any]] = []
            for _, items in self._cache.items():
                rows.extend(items)
            self._json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("Could not save personal_kb.json: %s", e)

    def add(self, category: str, data: dict) -> dict:
        """Add a record to a category."""
        try:
            cat = category.lower()
            if cat not in CATEGORIES:
                cat = "preferences"
            doc = {"category": cat, "data": data, "timestamp": time.time()}
            if self._use_db:
                self._db.insert("personal_kb", doc)
            else:
                with self._lock:
                    self._load_json()
                    self._cache.setdefault(cat, []).append(doc)
                    self._save_json()
            self._cache_ts = 0  # invalidate cache
            logger.info("KB saved: %s -> %s", cat, data)
            return {"success": True, "category": cat, "data": data}
        except Exception as e:
            logger.error("PersonalKB.add failed: %s", e)
            return {"success": False, "error": str(e)}

    def get(self, category: str) -> list:
        """Return all records for a category."""
        try:
            cat = category.lower()
            if self._use_db:
                rows = self._db.find("personal_kb", {"category": cat}, sort=[("timestamp", -1)])
                return [r.get("data", {}) for r in (rows or [])]
            self._load_json()
            return [r.get("data", {}) for r in self._cache.get(cat, [])]
        except Exception as e:
            logger.error("PersonalKB.get failed: %s", e)
            return []

    def update(self, category: str, item_id: str, data: dict) -> dict:
        """Update a record by its MongoDB _id."""
        try:
            if not self._use_db:
                return {"success": False, "error": "MongoDB not available for update"}
            try:
                from bson import ObjectId
                q = {"_id": ObjectId(item_id)} if ObjectId.is_valid(item_id) else {"_id": item_id}
            except Exception:
                q = {"_id": item_id}
            n = self._db.update("personal_kb", q, {"$set": {"data": data, "updated_at": time.time()}})
            return {"success": n > 0, "updated": n}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search(self, query: str) -> list:
        """Text search across all categories."""
        try:
            if self._use_db:
                rows = self._db.text_search("personal_kb", query, limit=20)
                return [{"category": r.get("category"), "data": r.get("data", {})} for r in (rows or [])]
            # In-memory fallback: simple substring search
            q = query.lower()
            results = []
            for cat, records in self._cache.items():
                for rec in records:
                    if q in str(rec).lower():
                        results.append({"category": cat, "data": rec.get("data", {})})
            return results[:20]
        except Exception as e:
            logger.error("PersonalKB.search failed: %s", e)
            return []

    def get_full_profile(self) -> dict:
        """Return all categories as one dict."""
        try:
            profile: dict[str, list] = {}
            for cat in CATEGORIES:
                profile[cat] = self.get(cat)
            return profile
        except Exception as e:
            logger.error("PersonalKB.get_full_profile failed: %s", e)
            return {}

    def get_context_summary(self) -> str:
        """
        Short paragraph (≤200 words) Jarvis injects into every system prompt.
        Returns empty string if no data exists yet.
        """
        try:
            now = time.time()
            # Cache for 5 minutes
            if now - self._cache_ts < 300 and hasattr(self, "_context_cache"):
                return self._context_cache

            profile = self.get_full_profile()
            # Build from known facts
            parts: list[str] = []

            contacts = profile.get("contacts", [])
            if contacts:
                names = ", ".join(c.get("name", "") for c in contacts[:3] if c.get("name"))
                if names:
                    parts.append(f"Contacts include: {names}")

            work = profile.get("work", [])
            if work:
                w = work[0]
                role = w.get("role") or w.get("position", "")
                company = w.get("company", "")
                if role:
                    parts.append(f"User works as {role}{' at ' + company if company else ''}.")

            prefs = profile.get("preferences", [])
            if prefs:
                pref_strs = [f"{p.get('preference', '')}" for p in prefs[:3] if p.get("preference")]
                if pref_strs:
                    parts.append(f"Preferences: {', '.join(pref_strs)}.")

            goals = profile.get("goals", [])
            if goals:
                g_strs = [g.get("goal", "") for g in goals[:2] if g.get("goal")]
                if g_strs:
                    parts.append(f"Current goals: {', '.join(g_strs)}.")

            finances = profile.get("finances", [])
            if finances:
                f = finances[0]
                if f.get("monthly_income"):
                    parts.append(f"Monthly income: {f['monthly_income']}.")

            if not parts:
                return ""

            summary = " ".join(parts)
            # Enforce ≤200 words
            words = summary.split()
            if len(words) > 200:
                summary = " ".join(words[:200]) + "..."

            self._context_cache = summary
            self._cache_ts = now
            return summary
        except Exception as e:
            logger.error("get_context_summary failed: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def add_contact(
        self,
        name: str,
        email: str | None = None,
        phone: str | None = None,
        relationship: str = "contact",
        notes: str = "",
    ) -> dict:
        return self.add("contacts", {
            "name": name,
            "email": email or "",
            "phone": phone or "",
            "relationship": relationship,
            "notes": notes,
            "last_interaction": time.strftime("%Y-%m-%d"),
        })

    def find_contact(self, name: str) -> dict:
        """Fuzzy search contacts by name."""
        try:
            contacts = self.get("contacts")
            name_lower = name.lower()
            # Exact match first
            for c in contacts:
                if c.get("name", "").lower() == name_lower:
                    return {"success": True, "contact": c}
            # Partial match
            for c in contacts:
                if name_lower in c.get("name", "").lower():
                    return {"success": True, "contact": c}
            return {"success": False, "error": f"Contact '{name}' not found."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_goal(self, goal: str, progress: int) -> dict:
        """Update progress on a goal (0-100)."""
        try:
            goals = self.get("goals")
            goal_lower = goal.lower()
            for g in goals:
                if goal_lower in g.get("goal", "").lower():
                    g["progress_pct"] = max(0, min(100, progress))
                    g["status"] = "completed" if progress >= 100 else "in_progress"
                    return {"success": True, "goal": g}
            # Add new goal
            return self.add("goals", {
                "goal": goal,
                "progress_pct": progress,
                "status": "in_progress",
                "deadline": "",
                "milestones": [],
            })
        except Exception as e:
            return {"success": False, "error": str(e)}

    def extract_from_conversation(self, messages: list) -> dict:
        """
        Extract personal facts from conversation messages using Brain.
        Runs in a background thread — never blocks response.
        """
        def _extract():
            try:
                text = "\n".join(
                    f"{m.get('role', '?')}: {m.get('content', '')}"
                    for m in messages[-10:]
                    if m.get("role") != "system"
                )
                prompt = (
                    "Extract any personal facts, preferences, contacts, or habits mentioned "
                    "in this conversation. Return a JSON array of objects, each with 'category' "
                    "(one of: contacts, habits, goals, preferences, work, health, finances, routines) "
                    "and 'data' (a dict). If nothing personal was mentioned, return [].\n\n"
                    f"Conversation:\n{text[:4000]}"
                )
                raw = self._brain.chat([
                    {"role": "system", "content": "You are a personal knowledge extractor. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ])
                import json, re
                m = re.search(r"\[[\s\S]*\]", raw or "")
                if m:
                    facts = json.loads(m.group(0))
                    for fact in (facts or []):
                        if isinstance(fact, dict) and fact.get("category") and fact.get("data"):
                            self.add(fact["category"], fact["data"])
                    logger.debug("PersonalKB: extracted %d facts from conversation", len(facts or []))
            except Exception as e:
                logger.debug("extract_from_conversation failed: %s", e)

        t = threading.Thread(target=_extract, daemon=True)
        t.start()
        return {"success": True, "message": "Extraction running in background"}


if __name__ == "__main__":
    print("PersonalKB module OK")
