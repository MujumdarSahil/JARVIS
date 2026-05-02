from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha1
from typing import Any


class PatternEngine:
    def __init__(self, usage_analyzer: Any, db: Any) -> None:
        self.usage_analyzer = usage_analyzer
        self.db = db
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        if not getattr(self.db, "available", False):
            return
        col = self.db.get_collection("patterns")
        if col is None:
            return
        try:
            col.create_index([("pattern_id", 1)], unique=True)
            col.create_index([("first_seen", 1)], expireAfterSeconds=90 * 24 * 3600)
        except Exception:
            pass

    def _pattern(self, ptype: str, desc: str, confidence: float, actionable: bool, action: str, times: int) -> dict[str, Any]:
        pid = sha1(f"{ptype}:{desc}".encode("utf-8")).hexdigest()[:16]
        return {
            "pattern_id": pid,
            "type": ptype,
            "description": desc,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "actionable": bool(actionable),
            "suggested_action": action,
            "first_seen": datetime.now().timestamp(),
            "times_confirmed": int(times),
        }

    def find_patterns(self) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        usage = self.usage_analyzer.analyze_usage(30)
        hourly = usage.get("hourly_heatmap", {}) or {}
        total_messages = sum(int(v) for v in hourly.values())

        if total_messages >= 5:
            top_hour = sorted(hourly.items(), key=lambda kv: kv[1], reverse=True)[0]
            if top_hour[1] >= 5:
                h = int(top_hour[0])
                bucket = "morning" if h < 12 else "afternoon" if h < 17 else "evening"
                patterns.append(self._pattern(
                    "time_based",
                    f"User is most active around {h:02d}:00 ({bucket}).",
                    min(0.95, top_hour[1] / max(1.0, total_messages * 0.3)),
                    True,
                    "suggest_time_aligned_brief",
                    top_hour[1],
                ))

        convs = self.usage_analyzer._fetch_conversations(30)  # noqa: SLF001
        if len(convs) >= 5:
            by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for c in convs:
                by_session[str(c.get("session_id") or "default")].append(c)
            seq_counter: Counter[str] = Counter()
            frustration = 0
            search_no_followup = 0
            search_count = 0
            for _, items in by_session.items():
                items.sort(key=lambda x: float(x.get("timestamp") or 0))
                user_msgs = [str(i.get("content") or "") for i in items if i.get("role") == "user"]
                cls = [self.usage_analyzer._classify_request_type(m) for m in user_msgs]  # noqa: SLF001
                for i in range(len(cls) - 1):
                    seq_counter[f"{cls[i]}->{cls[i+1]}"] += 1
                for i in range(1, len(user_msgs)):
                    if len(user_msgs[i].split()) >= 3 and user_msgs[i].lower() in user_msgs[i - 1].lower():
                        frustration += 1
                for i, c in enumerate(cls):
                    if c == "search":
                        search_count += 1
                        if i == len(cls) - 1:
                            search_no_followup += 1
            if seq_counter:
                seq, cnt = seq_counter.most_common(1)[0]
                if cnt >= 5:
                    patterns.append(self._pattern("sequence", f"Common request sequence detected: {seq}.", 0.75, True, f"preload_{seq.split('->')[-1]}", cnt))
            if frustration >= 5:
                patterns.append(self._pattern("frustration", "User often rephrases requests after responses.", min(0.9, frustration / max(5.0, len(convs))), True, "offer_clarification_first", frustration))
            if search_count >= 5 and (search_no_followup / search_count) >= 0.6:
                patterns.append(self._pattern("success", "Search responses are often accepted without follow-up.", 0.8, False, "keep_search_style", search_no_followup))

        req_types = usage.get("most_common_request_types", {})
        code_count = int(req_types.get("code", 0))
        chat_count = int(req_types.get("chat", 0))
        if code_count >= 5:
            patterns.append(self._pattern("preference", "User prefers code-oriented requests.", min(0.95, code_count / max(1.0, code_count + chat_count)), True, "prioritize_code_assistant", code_count))

        self.save_patterns(patterns)
        return patterns

    def save_patterns(self, patterns: list[dict[str, Any]]) -> None:
        if not getattr(self.db, "available", False):
            return
        for p in patterns:
            pid = p.get("pattern_id")
            if not pid:
                continue
            existing = self.db.find_one("patterns", {"pattern_id": pid})
            if existing:
                self.db.update(
                    "patterns",
                    {"pattern_id": pid},
                    {"$set": {"confidence": p.get("confidence"), "description": p.get("description")}, "$inc": {"times_confirmed": 1}},
                )
            else:
                self.db.insert("patterns", p)

    def get_actionable_patterns(self) -> list[dict[str, Any]]:
        if not getattr(self.db, "available", False):
            return [p for p in self.find_patterns() if p.get("actionable") and float(p.get("confidence") or 0) > 0.7]
        rows = self.db.find("patterns", {"actionable": True, "confidence": {"$gt": 0.7}}, limit=50, sort=[("confidence", -1)])
        return [dict(r) for r in rows]

    def get_pattern_summary(self) -> str:
        pats = self.get_actionable_patterns()[:3]
        if not pats:
            return "I am still learning your usage patterns."
        return "I've noticed " + ", ".join(str(p.get("description", "")).rstrip(".") for p in pats) + "."


if __name__ == "__main__":
    print("PatternEngine module loaded.")
