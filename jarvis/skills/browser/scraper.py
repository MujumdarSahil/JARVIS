"""
Structured web data extractor built on top of BrowserAgent.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from utils.logger import get_logger

if TYPE_CHECKING:
    from skills.browser.agent import BrowserAgent
    from core.brain import Brain

logger = get_logger(__name__)

_JOB_PATTERNS = {
    "linkedin": "https://www.linkedin.com/jobs/search/?keywords={keywords}&location=India",
    "naukri": "https://www.naukri.com/{keywords}-jobs",
}


class WebScraper:
    """High-level structured scraping helpers."""

    def __init__(self, browser_agent: "BrowserAgent", brain: "Brain | None" = None) -> None:
        self._browser = browser_agent
        self._brain = brain

    def scrape_article(self, url: str) -> dict:
        """Extract article title, author, date, text, and AI summary."""
        try:
            data = self._browser.get_page_structured(url)
            if not data.get("success"):
                return data
            page_text = self._browser.get_page_text(url)
            full_text = page_text.get("text", "")
            summary = ""
            if self._brain and full_text:
                try:
                    messages = [
                        {"role": "system", "content": "You are a concise news summarizer."},
                        {"role": "user", "content": f"Summarize this in 3-4 sentences:\n\n{full_text[:3000]}"},
                    ]
                    summary = self._brain.chat(messages) or ""
                except Exception as e:
                    logger.warning("Article summary failed: %s", e)
            return {
                "success": True,
                "url": url,
                "title": data.get("title", ""),
                "author": "",
                "date": "",
                "text": full_text[:10000],
                "summary": summary,
                "headings": data.get("headings", []),
            }
        except Exception as e:
            logger.error("scrape_article failed: %s", e)
            return {"success": False, "error": str(e)}

    def scrape_price_history(self, product_name: str) -> dict:
        """Search price comparison sites for a product."""
        try:
            sites = [
                f"https://www.google.com/shopping/search?q={product_name.replace(' ', '+')}",
                f"https://www.flipkart.com/search?q={product_name.replace(' ', '+')}",
            ]
            results = {}
            for site in sites:
                try:
                    price_data = self._browser.extract_price(site)
                    if price_data.get("success"):
                        results[site] = price_data.get("prices_found", [])
                except Exception:
                    continue
            return {"success": True, "product": product_name, "prices_by_site": results}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def monitor_job_postings(self, keywords: list, sites: list | None = None) -> list:
        """Scrape job listings matching keywords."""
        sites = sites or ["linkedin", "naukri"]
        all_jobs = []
        kw_str = " ".join(keywords)
        for site in sites:
            try:
                template = _JOB_PATTERNS.get(site.lower())
                if not template:
                    continue
                url = template.format(keywords=kw_str.replace(" ", "+"))
                data = self._browser.get_page_text(url)
                if data.get("success"):
                    text = data.get("text", "")
                    for kw in keywords:
                        positions = [m.start() for m in re.finditer(re.escape(kw), text, re.IGNORECASE)]
                        for pos in positions[:5]:
                            snippet = text[max(0, pos - 50) : pos + 150].strip()
                            if snippet:
                                all_jobs.append({"site": site, "snippet": snippet, "keyword": kw})
            except Exception as e:
                logger.warning("Job scraping failed for %s: %s", site, e)
        return all_jobs

    def get_trending_topics(self, category: str = "tech") -> list:
        """Scrape trending topics from multiple sources."""
        try:
            sources = {
                "tech": "https://trends.google.com/trending?geo=IN",
                "news": "https://news.google.com/news/headlines",
                "finance": "https://economictimes.indiatimes.com/markets",
            }
            url = sources.get(category.lower(), sources["tech"])
            data = self._browser.get_page_text(url)
            if not data.get("success"):
                return []
            text = data.get("text", "")
            lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 10]
            return lines[:20]
        except Exception as e:
            logger.error("get_trending_topics failed: %s", e)
            return []


if __name__ == "__main__":
    print("WebScraper module OK")
