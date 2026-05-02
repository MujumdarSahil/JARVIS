from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import re
from typing import Any


class UsageAnalyzer:
    """Analyzes usage metrics from DB logs with safe fallback behavior."""

    _STOPWORDS = {
        "the", "is", "a", "an", "to", "for", "and", "or", "of", "on", "in", "at", "with",
        "this", "that", "it", "you", "i", "we", "he", "she", "they", "me", "my", "your",
        "what", "how", "when", "where", "why", "who", "can", "could", "would", "should",
        "please", "jarvis", "sir", "today", "now", "from", "into", "about", "there", "then",
    }

    def __init__(self, db: Any, registry: Any) -> None:
        self.db = db
        self.registry = registry

    def _fetch_conversations(self, days: int = 7) -> list[dict[str, Any]]:
        if not getattr(self.db, "available", False):
            return []
        cutoff = datetime.now().timestamp() - (max(1, days) * 86400)
        rows = self.db.find("conversations", {"timestamp": {"$gte": cutoff}}, limit=5000, sort=[("timestamp", -1)])
        return [dict(r) for r in rows]

    def _fetch_agent_logs(self, days: int = 7) -> list[dict[str, Any]]:
        if not getattr(self.db, "available", False):
            return []
        cutoff = datetime.now().timestamp() - (max(1, days) * 86400)
        rows = self.db.find("agent_logs", {"timestamp": {"$gte": cutoff}}, limit=5000, sort=[("timestamp", -1)])
        return [dict(r) for r in rows]

    def _classify_request_type(self, text: str) -> str:
        low = (text or "").lower()
        if any(k in low for k in ("python", "javascript", "code", "debug", "function", "class", "bug")):
            return "code"
        if any(k in low for k in ("search", "latest", "news", "weather", "who won", "price")):
            return "search"
        if any(k in low for k in ("file", "folder", "directory", "read", "write", "open")):
            return "files"
        return "chat"

    def get_hourly_heatmap(self) -> dict[int, int]:
        out = {h: 0 for h in range(24)}
        for row in self._fetch_conversations(30):
            ts = float(row.get("timestamp") or 0)
            if ts <= 0:
                continue
            hour = datetime.fromtimestamp(ts).hour
            out[hour] += 1
        return out

    def get_top_requests(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._fetch_conversations(30)
        counter: Counter[str] = Counter()
        for row in rows:
            if row.get("role") != "user":
                continue
            text = str(row.get("content") or "").strip().lower()
            if not text:
                continue
            normalized = re.sub(r"\s+", " ", text)
            counter[normalized[:120]] += 1
        return [{"pattern": p, "count": c} for p, c in counter.most_common(max(1, limit))]

    def get_tool_performance(self) -> dict[str, dict[str, float]]:
        logs = self._fetch_agent_logs(30)
        per_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in logs:
            tool = str(row.get("tool_used") or "unknown")
            per_tool[tool].append(row)
        result: dict[str, dict[str, float]] = {}
        for tool, items in per_tool.items():
            calls = len(items)
            ok = 0
            durations: list[float] = []
            for i in items:
                resp = str(i.get("response", ""))
                success = bool(i.get("success", True)) and "error" not in resp.lower()
                if success:
                    ok += 1
                d = i.get("duration_ms")
                if isinstance(d, (int, float)):
                    durations.append(float(d))
            result[tool] = {
                "call_count": float(calls),
                "success_rate": (ok / calls) if calls else 0.0,
                "avg_duration_ms": (sum(durations) / len(durations)) if durations else 0.0,
            }
        if not result:
            fallback = getattr(self.registry, "_call_counts", {}) or {}
            for tool, count in fallback.items():
                result[str(tool)] = {"call_count": float(count), "success_rate": 1.0, "avg_duration_ms": 0.0}
        return result

    def get_user_vocabulary(self) -> list[dict[str, Any]]:
        words: Counter[str] = Counter()
        for row in self._fetch_conversations(30):
            if row.get("role") != "user":
                continue
            txt = str(row.get("content") or "").lower()
            for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", txt):
                if w in self._STOPWORDS:
                    continue
                words[w] += 1
        return [{"word": w, "count": c} for w, c in words.most_common(50)]

    def get_session_patterns(self) -> dict[str, Any]:
        rows = self._fetch_conversations(30)
        by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_session[str(r.get("session_id") or "default")].append(r)
        if not by_session:
            return {"avg_messages_per_session": 0.0, "avg_session_duration_min": 0.0, "most_active_day": "unknown"}
        msg_counts = []
        durations = []
        days: Counter[str] = Counter()
        for _, items in by_session.items():
            items.sort(key=lambda x: float(x.get("timestamp") or 0))
            msg_counts.append(len(items))
            ts_start = float(items[0].get("timestamp") or 0)
            ts_end = float(items[-1].get("timestamp") or 0)
            durations.append(max(0.0, (ts_end - ts_start) / 60.0))
            days[datetime.fromtimestamp(ts_start).strftime("%A")] += 1
        return {
            "avg_messages_per_session": round(sum(msg_counts) / len(msg_counts), 2),
            "avg_session_duration_min": round(sum(durations) / len(durations), 2),
            "most_active_day": (days.most_common(1)[0][0] if days else "unknown"),
        }

    def analyze_usage(self, days: int = 7) -> dict[str, Any]:
        conversations = self._fetch_conversations(days)
        logs = self._fetch_agent_logs(days)

        tool_counter: Counter[str] = Counter()
        fail_counter: Counter[str] = Counter()
        request_types: Counter[str] = Counter()
        keywords: Counter[str] = Counter()

        by_session: dict[str, int] = defaultdict(int)
        hourly = {h: 0 for h in range(24)}
        for row in conversations:
            role = str(row.get("role") or "")
            sid = str(row.get("session_id") or "default")
            by_session[sid] += 1
            ts = float(row.get("timestamp") or 0)
            if ts > 0:
                hourly[datetime.fromtimestamp(ts).hour] += 1
            if role != "user":
                continue
            content = str(row.get("content") or "")
            request_types[self._classify_request_type(content)] += 1
            for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", content.lower()):
                if w not in self._STOPWORDS:
                    keywords[w] += 1

        for row in logs:
            tool = str(row.get("tool_used") or "unknown")
            tool_counter[tool] += 1
            response = str(row.get("response") or "")
            if "error" in response.lower() or row.get("success") is False:
                fail_counter[tool] += 1

        avg_len = (sum(by_session.values()) / len(by_session)) if by_session else 0.0
        failure_rate_per_tool = {
            tool: round((fail_counter[tool] / count), 3) if count else 0.0
            for tool, count in tool_counter.items()
        }

        if not tool_counter and getattr(self.registry, "_call_counts", None):
            tool_counter = Counter({k: int(v) for k, v in self.registry._call_counts.items()})

        return {
            "most_used_tools": [{"tool": t, "count": c} for t, c in tool_counter.most_common(10)],
            "most_common_request_types": dict(request_types),
            "peak_usage_hours": [h for h, n in sorted(hourly.items(), key=lambda kv: kv[1], reverse=True)[:3]],
            "average_session_length": round(avg_len, 2),
            "most_common_keywords": [{"keyword": k, "count": c} for k, c in keywords.most_common(20)],
            "failure_rate_per_tool": failure_rate_per_tool,
            "hourly_heatmap": hourly,
            "top_requests": self.get_top_requests(),
            "tool_performance": self.get_tool_performance(),
            "user_vocabulary": self.get_user_vocabulary(),
            "session_patterns": self.get_session_patterns(),
        }


if __name__ == "__main__":
    print("UsageAnalyzer module loaded.")
