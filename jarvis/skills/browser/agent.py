"""
Playwright-based browser controller (sync wrapper around async API).

Requires: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

# Simple hostname → HTTPS URL resolver
_COMMON_SITES: dict[str, str] = {
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "github": "https://www.github.com",
    "twitter": "https://www.twitter.com",
    "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com",
    "naukri": "https://www.naukri.com",
    "netflix": "https://www.netflix.com",
    "wikipedia": "https://www.wikipedia.org",
}


def _resolve_url(url: str) -> str:
    """Convert short names like 'amazon' to 'https://www.amazon.in'."""
    url = url.strip()
    if re.match(r"^https?://", url):
        return url
    lower = url.lower()
    for key, full in _COMMON_SITES.items():
        if lower == key or lower == f"www.{key}.com":
            return full
    # Assume it's a bare hostname
    return f"https://{url}"


def _run_sync(coro) -> Any:
    """Run an async coroutine synchronously, creating an event loop if needed."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=60)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


class BrowserAgent:
    """Playwright Chromium controller. Browser instance is reused across calls."""

    def __init__(self, headless: bool = True, timeout_seconds: int = 30) -> None:
        self._headless = headless
        self._timeout_ms = timeout_seconds * 1000
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_browser(self) -> None:
        """Launch browser lazily on first use."""
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright

            self._pw_ctx = sync_playwright().__enter__()
            self._browser = self._pw_ctx.chromium.launch(headless=self._headless)
            self._page = self._browser.new_page()
            self._page.set_default_timeout(self._timeout_ms)
            logger.info("Playwright Chromium launched (headless=%s)", self._headless)
        except Exception as e:
            logger.error("Browser launch failed: %s", e)
            raise

    def close(self) -> None:
        """Close browser gracefully."""
        try:
            if self._browser:
                self._browser.close()
                self._browser = None
                self._page = None
            if hasattr(self, "_pw_ctx") and self._pw_ctx:
                self._pw_ctx.__exit__(None, None, None)
                self._pw_ctx = None
        except Exception as e:
            logger.warning("Browser close error: %s", e)

    # ------------------------------------------------------------------
    # Navigation helpers
    # ------------------------------------------------------------------

    def navigate(self, url: str) -> dict:
        """Navigate to URL and return basic page info."""
        try:
            self._ensure_browser()
            url = _resolve_url(url)
            t0 = time.perf_counter()
            self._page.goto(url, wait_until="domcontentloaded")
            load_ms = int((time.perf_counter() - t0) * 1000)
            return {
                "success": True,
                "title": self._page.title(),
                "url": self._page.url,
                "load_time_ms": load_ms,
            }
        except Exception as e:
            logger.error("navigate failed: %s", e)
            return {"success": False, "error": str(e)}

    def get_page_text(self, url: str) -> dict:
        """Navigate to URL and return all visible text (HTML stripped)."""
        try:
            nav = self.navigate(url)
            if not nav.get("success"):
                return nav
            text = self._page.inner_text("body") or ""
            text = re.sub(r"\s+", " ", text).strip()
            return {
                "success": True,
                "url": self._page.url,
                "title": self._page.title(),
                "text": text[:50000],
            }
        except Exception as e:
            logger.error("get_page_text failed: %s", e)
            return {"success": False, "error": str(e)}

    def get_page_structured(self, url: str) -> dict:
        """Navigate to URL and extract structured content."""
        try:
            nav = self.navigate(url)
            if not nav.get("success"):
                return nav

            title = self._page.title()
            headings = [h.inner_text() for h in self._page.query_selector_all("h1, h2, h3")]
            paragraphs = [p.inner_text() for p in self._page.query_selector_all("p")][:20]
            links = [
                {"text": a.inner_text(), "href": a.get_attribute("href") or ""}
                for a in self._page.query_selector_all("a[href]")
            ][:30]
            images = [
                img.get_attribute("alt") or ""
                for img in self._page.query_selector_all("img[alt]")
            ][:20]

            return {
                "success": True,
                "url": self._page.url,
                "title": title,
                "headings": headings[:10],
                "paragraphs": [p for p in paragraphs if len(p) > 30][:10],
                "links": links,
                "images_alt": [i for i in images if i],
            }
        except Exception as e:
            logger.error("get_page_structured failed: %s", e)
            return {"success": False, "error": str(e)}

    def click(self, selector: str) -> dict:
        """Click an element by CSS selector or visible text."""
        try:
            self._ensure_browser()
            try:
                self._page.click(selector)
            except Exception:
                self._page.get_by_text(selector).first.click()
            return {"success": True, "clicked": selector}
        except Exception as e:
            logger.error("click failed: %s", e)
            return {"success": False, "error": str(e)}

    def fill_form(self, url: str, fields: dict) -> dict:
        """Navigate to URL, fill form fields (selector: value), and submit."""
        try:
            nav = self.navigate(url)
            if not nav.get("success"):
                return nav
            for selector, value in (fields or {}).items():
                self._page.fill(selector, str(value))
            self._page.keyboard.press("Enter")
            self._page.wait_for_load_state("domcontentloaded")
            return {"success": True, "final_url": self._page.url}
        except Exception as e:
            logger.error("fill_form failed: %s", e)
            return {"success": False, "error": str(e)}

    def screenshot(self, url: str | None = None, save_path: str | None = None) -> dict:
        """Take a screenshot, optionally navigating first."""
        try:
            self._ensure_browser()
            if url:
                self.navigate(url)
            path = save_path or f"screenshots/browser_{int(time.time())}.png"
            import os
            os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
            self._page.screenshot(path=path, full_page=True)
            return {"success": True, "path": path, "url": self._page.url}
        except Exception as e:
            logger.error("screenshot failed: %s", e)
            return {"success": False, "error": str(e)}

    def search_google(self, query: str, num_results: int = 5) -> list:
        """Navigate to Google and extract organic search results."""
        try:
            self._ensure_browser()
            self._page.goto(
                f"https://www.google.com/search?q={query.replace(' ', '+')}&num={num_results}",
                wait_until="domcontentloaded",
            )
            results = []
            # Extract organic results (avoid ads — they have class 'uEierd')
            for item in self._page.query_selector_all("div.g"):
                try:
                    link_el = item.query_selector("a")
                    title_el = item.query_selector("h3")
                    snippet_el = item.query_selector("div.VwiC3b, span.st")
                    if not link_el or not title_el:
                        continue
                    results.append({
                        "title": title_el.inner_text(),
                        "url": link_el.get_attribute("href") or "",
                        "snippet": snippet_el.inner_text() if snippet_el else "",
                    })
                    if len(results) >= num_results:
                        break
                except Exception:
                    continue
            return results
        except Exception as e:
            logger.error("search_google failed: %s", e)
            return []

    def extract_price(self, url: str) -> dict:
        """Extract product price from a page using regex patterns."""
        try:
            page_data = self.get_page_text(url)
            if not page_data.get("success"):
                return page_data
            text = page_data.get("text", "")
            # Match common price patterns: ₹1,234, $1,234, Rs. 1234, USD 1,234
            patterns = [
                r"[₹\$€£][\s]?[\d,]+(?:\.\d{1,2})?",
                r"Rs\.?\s*[\d,]+(?:\.\d{1,2})?",
                r"USD\s*[\d,]+(?:\.\d{1,2})?",
                r"INR\s*[\d,]+(?:\.\d{1,2})?",
            ]
            prices = []
            for pat in patterns:
                matches = re.findall(pat, text)
                prices.extend(matches)

            return {
                "success": True,
                "url": url,
                "title": page_data.get("title", ""),
                "prices_found": prices[:10],
                "primary_price": prices[0] if prices else None,
            }
        except Exception as e:
            logger.error("extract_price failed: %s", e)
            return {"success": False, "error": str(e)}

    def login(
        self,
        url: str,
        username_selector: str,
        password_selector: str,
        username: str,
        password: str,
    ) -> dict:
        """Fill login form. Credentials come from caller; NEVER stored internally."""
        try:
            nav = self.navigate(url)
            if not nav.get("success"):
                return nav
            self._page.fill(username_selector, username)
            self._page.fill(password_selector, password)
            self._page.keyboard.press("Enter")
            self._page.wait_for_load_state("domcontentloaded")
            return {"success": True, "final_url": self._page.url}
        except Exception as e:
            logger.error("login failed: %s", e)
            return {"success": False, "error": str(e)}

    def monitor_url(
        self, url: str, check_selector: str, interval_minutes: int = 60
    ) -> dict:
        """Register a URL monitor via ProactiveMonitor (placeholder — wired in main.py)."""
        return {
            "success": True,
            "message": (
                f"URL monitor for {url} registered. "
                "Use ProactiveMonitor to watch for changes."
            ),
            "url": url,
            "selector": check_selector,
            "interval_minutes": interval_minutes,
        }


if __name__ == "__main__":
    print("BrowserAgent module OK — run: playwright install chromium")
