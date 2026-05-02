"""
Browser Agent skill — wraps BrowserAgent + WebScraper as a BaseAgent.

Named BrowserAgentSkill to avoid name conflict with skills/browser/agent.py::BrowserAgent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.agents.base_agent import BaseAgent
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database
    from skills.browser.agent import BrowserAgent
    from skills.browser.scraper import WebScraper

logger = get_logger(__name__)


class BrowserAgentSkill(BaseAgent):
    """Agent that routes browser/scraping tasks."""

    def __init__(
        self,
        brain: "Brain",
        db: "Database",
        config: dict,
        browser_agent: "BrowserAgent | None" = None,
        scraper: "WebScraper | None" = None,
    ) -> None:
        super().__init__("browser", brain, db, config)
        self._browser = browser_agent
        self._scraper = scraper

    def _not_configured(self) -> dict:
        return {
            "success": False,
            "result": "Browser is not enabled. Set browser.enabled: true in config.yaml.",
        }

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        def _run():
            action = str(task.get("action") or task.get("type") or "").lower().strip()
            params = task.get("params") or {}

            if self._browser is None:
                return self._not_configured()

            if action == "browse":
                return self._browse(params)
            if action == "search":
                return self._search(params)
            if action == "scrape":
                return self._scrape(params)
            if action == "screenshot":
                return self._screenshot(params)
            if action == "monitor_site":
                return self._monitor_site(params)
            if action == "extract_price":
                return self._extract_price(params)
            if action == "scrape_jobs":
                return self._scrape_jobs(params)
            return {"success": False, "result": f"Unknown browser action: {action}"}

        return self._run_wrapped(task, _run)

    def _browse(self, params: dict) -> dict:
        url = params.get("url", "")
        if not url:
            return {"success": False, "result": "No URL provided."}
        data = self._browser.get_page_structured(url)
        if not data.get("success"):
            return {"success": False, "result": data.get("error", "Browse failed")}

        summary = self.think(
            f"Describe the content of this webpage briefly:\n"
            f"Title: {data.get('title')}\n"
            f"Headings: {data.get('headings', [])[:5]}\n"
            f"Paragraphs: {data.get('paragraphs', [])[:3]}"
        )
        return {
            "success": True,
            "result": summary or str(data.get("headings", "")[:500]),
            "url": data.get("url"),
            "title": data.get("title"),
            "structured": data,
        }

    def _search(self, params: dict) -> dict:
        query = params.get("query", "")
        if not query:
            return {"success": False, "result": "No search query provided."}
        results = self._browser.search_google(query, num_results=params.get("num_results", 5))
        if not results:
            return {"success": True, "result": "No results found.", "results": []}
        lines = [f"{i+1}. {r['title']} — {r['url']}\n   {r['snippet']}" for i, r in enumerate(results)]
        return {
            "success": True,
            "result": "\n".join(lines),
            "results": results,
        }

    def _scrape(self, params: dict) -> dict:
        url = params.get("url", "")
        if not url:
            return {"success": False, "result": "No URL provided."}
        if self._scraper:
            data = self._scraper.scrape_article(url)
        else:
            data = self._browser.get_page_structured(url)
        if not data.get("success"):
            return {"success": False, "result": data.get("error", "Scrape failed")}
        return {
            "success": True,
            "result": data.get("summary") or data.get("text", "")[:1000],
            "data": data,
        }

    def _screenshot(self, params: dict) -> dict:
        url = params.get("url")
        path = params.get("save_path")
        result = self._browser.screenshot(url=url, save_path=path)
        msg = f"Screenshot saved to {result.get('path')}" if result.get("success") else result.get("error", "Failed")
        return {"success": result.get("success", False), "result": msg, "path": result.get("path")}

    def _monitor_site(self, params: dict) -> dict:
        url = params.get("url", "")
        selector = params.get("selector", "body")
        interval = params.get("interval_minutes", 60)
        if not url:
            return {"success": False, "result": "No URL provided."}
        result = self._browser.monitor_url(url, selector, interval)
        return {"success": True, "result": result.get("message", "Monitor registered."), "data": result}

    def _extract_price(self, params: dict) -> dict:
        url = params.get("url", "")
        if not url:
            return {"success": False, "result": "No URL provided."}
        result = self._browser.extract_price(url)
        if not result.get("success"):
            return {"success": False, "result": result.get("error", "Price extraction failed")}
        price = result.get("primary_price") or "not found"
        return {
            "success": True,
            "result": f"Price: {price} on {result.get('title', url)}",
            "prices": result.get("prices_found", []),
            "primary_price": result.get("primary_price"),
        }

    def _scrape_jobs(self, params: dict) -> dict:
        keywords = params.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [keywords]
        sites = params.get("sites", ["linkedin", "naukri"])
        if not keywords:
            return {"success": False, "result": "No keywords provided."}
        if not self._scraper:
            return {"success": False, "result": "WebScraper not initialized."}
        jobs = self._scraper.monitor_job_postings(keywords, sites)
        return {
            "success": True,
            "result": f"Found {len(jobs)} job posting(s) matching {keywords}",
            "jobs": jobs,
        }


if __name__ == "__main__":
    print("BrowserAgentSkill module OK")
