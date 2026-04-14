"""
Web research specialist: search, summarize, fact-check, deep research.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS  # legacy fallback

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


class ResearchAgent(BaseAgent):
    """Deep web research, summarization, fact-checking."""

    def __init__(
        self,
        name: str,
        brain: Brain,
        db: Database,
        config: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(name, brain, db, config, kwargs.get("event_bus"))

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        return self._run_wrapped(task, lambda: self._execute_impl(task))

    def _execute_impl(self, task: dict[str, Any]) -> dict[str, Any]:
        params = task.get("params") if isinstance(task.get("params"), dict) else {}
        action = str(params.get("action") or task.get("type") or "research").lower()
        if action == "fact_check":
            claim = str(params.get("claim") or params.get("query") or task.get("description") or "")
            return self.fact_check(claim)
        if action == "deep_research":
            topic = str(params.get("topic") or params.get("query") or "")
            depth = int(params.get("depth") or self.config.get("deep_research_depth") or 2)
            return self.deep_research(topic, depth=min(depth, 2))
        query = str(params.get("query") or params.get("topic") or task.get("description") or "")
        if not query:
            return {"success": False, "result": "No search query provided."}
        max_results = int(self.config.get("max_search_results") or 8)
        report = self._research_query(query, max_results=max_results)
        return {"success": True, "result": report}

    def _ddg_results(self, query: str, max_results: int) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        try:
            n = max(1, min(int(max_results), 15))
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=n))
            for item in raw:
                items.append(
                    {
                        "title": str(item.get("title", "")).strip(),
                        "url": str(item.get("href", "")).strip(),
                        "body": str(item.get("body", "")).strip(),
                    }
                )
        except Exception as e:
            logger.warning("DDG search failed: %s", e)
        return items

    def _research_query(self, query: str, max_results: int = 8) -> str:
        results = self._ddg_results(query, max_results)
        if not results:
            return f"No web results for: {query}"

        facts_blocks: list[str] = []
        for i, r in enumerate(results, start=1):
            snippet = f"{r.get('title','')}\n{r.get('body','')}"[:4000]
            prompt = (
                f"From this search snippet, extract 2-5 concise factual bullet points. "
                f"Cite the source URL inline.\n\nURL: {r.get('url')}\n\n{snippet}"
            )
            chunk = self.think(
                prompt,
                system="You are a research analyst. Output plain bullet lines only.",
            )
            facts_blocks.append(f"### Source {i} ({r.get('url')})\n{chunk}")

        synth_prompt = (
            "Combine the following extracted notes into one coherent research report with sections: "
            "Summary, Key facts (bullets), Sources (numbered list of URLs). "
            "Resolve contradictions briefly if any.\n\n"
            + "\n\n".join(facts_blocks)
        )
        report = self.think(
            synth_prompt,
            system="You are a senior researcher. Be accurate; do not invent sources.",
        )

        tags = ["research"] + [w for w in re.findall(r"\w+", query.lower())[:8] if len(w) > 2]
        importance = 6 if len(results) >= 4 else 5
        if self.db.available:
            self.db.insert(
                "memories",
                {
                    "user_id": "default",
                    "content": report[:12000],
                    "tags": tags,
                    "importance_score": importance,
                    "timestamp": time.time(),
                    "source": "research_agent",
                    "query": query[:500],
                    "text_hash": self.brain.text_fingerprint(f"{query}|{report[:800]}"),
                },
            )
        return report

    def deep_research(self, topic: str, depth: int = 2) -> dict[str, Any]:
        depth = max(1, min(int(depth), 2))
        main = self._research_query(topic, max_results=8)
        subtopics_prompt = (
            f"Given this research draft on '{topic}', list 3-5 focused sub-questions for follow-up search. "
            f"Return JSON array of strings only.\n\nDRAFT:\n{main[:6000]}"
        )
        raw = self.think(subtopics_prompt, system="Return only a JSON array of strings, no markdown.")
        subs: list[str] = []
        parsed = _extract_json(raw)
        if isinstance(parsed, list):
            subs = [str(x) for x in parsed[:5] if x]
        else:
            subs = [topic]

        extra: list[str] = []
        if depth >= 2:
            for s in subs[:4]:
                extra.append(self._research_query(s, max_results=5))

        final_prompt = (
            "Synthesize the following into one deep-dive report with executive summary and citations.\n\n"
            f"PRIMARY:\n{main}\n\nFOLLOW-UPS:\n" + "\n---\n".join(extra)
        )
        final = self.think(final_prompt, system="You are a research director. Be thorough and cite URLs mentioned in text.")
        return {"success": True, "result": final, "subtopics": subs}

    def fact_check(self, claim: str) -> dict[str, Any]:
        if not (claim or "").strip():
            return {"success": False, "result": "Empty claim."}
        for_side = self._ddg_results(f"evidence supporting: {claim}", 5)
        against_side = self._ddg_results(f"evidence against or debunking: {claim}", 5)
        pack = "SUPPORTING:\n" + "\n".join(str(x) for x in for_side)
        pack += "\n\nCONTRADICTING:\n" + "\n".join(str(x) for x in against_side)
        verdict = self.think(
            f"Claim: {claim}\n\nEvidence pack:\n{pack[:8000]}\n\n"
            "Return: (1) verdict: supported | contradicted | unclear (2) 2-4 sentence rationale (3) list URLs you relied on.",
            system="You are a fact-checker. Be neutral and source-grounded.",
        )
        return {"success": True, "result": verdict}


if __name__ == "__main__":
    print("ResearchAgent module OK")
