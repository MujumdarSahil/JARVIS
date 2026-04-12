"""
DuckDuckGo-backed web search for quick factual lookups.
"""

from __future__ import annotations

from typing import Any

from duckduckgo_search import DDGS

from utils.logger import get_logger

logger = get_logger(__name__)


def web_search(query: str, max_results: int = 5) -> str:
    """
    Run a DuckDuckGo text search and return a human-readable result block.

    On failure, returns a short explanation instead of raising.
    """
    q = (query or "").strip()
    if not q:
        return "No search query was provided."

    n = max(1, min(int(max_results), 15))
    lines: list[str] = [f"Web search results for: {q}", ""]

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(q, max_results=n))
    except Exception as e:
        logger.warning("web_search failed for %r: %s", q, e)
        return f"Web search could not be completed: {e}"

    if not results:
        return f"No results found for: {q}"

    for i, item in enumerate(results, start=1):
        title = str(item.get("title", "")).strip() or "(no title)"
        href = str(item.get("href", "")).strip() or "(no url)"
        body = str(item.get("body", "")).strip() or "(no snippet)"
        lines.append(f"{i}. {title}")
        lines.append(f"   URL: {href}")
        lines.append(f"   {body}")
        lines.append("")

    return "\n".join(lines).rstrip()


class SearchSkill:
    """Thin wrapper so :class:`~skills.tools.registry.ToolRegistry` can call ``web_search`` uniformly."""

    def web_search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        try:
            text = web_search(query, max_results=max_results)
            failed = text.startswith("Web search could not")
            return {"success": not failed, "result": text, "error": text if failed else None}
        except Exception as e:
            logger.exception("SearchSkill.web_search failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}


if __name__ == "__main__":
    sk = SearchSkill()
    print(sk.web_search("python pathlib", max_results=2))
