"""
Stock + crypto market tracker.

Uses yfinance (free, no API key) for stocks and CoinGecko API for crypto.
Prices are cached for 5 minutes to avoid rate-limit hammering.
"""

from __future__ import annotations

import time
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

# Simple in-process price cache {symbol -> (timestamp, data)}
_PRICE_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cached(key: str, fetch_fn) -> Any:
    now = time.time()
    if key in _PRICE_CACHE:
        ts, data = _PRICE_CACHE[key]
        if now - ts < _CACHE_TTL:
            return data
    result = fetch_fn()
    _PRICE_CACHE[key] = (now, result)
    return result


class MarketTracker:
    """Market data tracker using yfinance (stocks) and CoinGecko (crypto)."""

    # ------------------------------------------------------------------
    # Stocks
    # ------------------------------------------------------------------

    def get_stock_price(self, symbol: str) -> dict:
        """Return current stock price and basic stats."""
        try:
            def _fetch():
                import yfinance as yf
                ticker = yf.Ticker(symbol)
                info = ticker.fast_info
                hist = ticker.history(period="2d")
                if hist.empty:
                    return {"success": False, "error": f"No data for {symbol}"}
                latest = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2] if len(hist) > 1 else latest
                change = latest - prev
                change_pct = (change / prev * 100) if prev else 0
                return {
                    "success": True,
                    "symbol": symbol.upper(),
                    "price": round(float(latest), 2),
                    "change": round(float(change), 2),
                    "change_pct": round(float(change_pct), 2),
                    "volume": int(hist["Volume"].iloc[-1]) if "Volume" in hist else 0,
                    "market_cap": getattr(info, "market_cap", None),
                    "currency": getattr(info, "currency", "USD"),
                }
            return _cached(f"stock:{symbol}", _fetch)
        except Exception as e:
            logger.error("get_stock_price failed for %s: %s", symbol, e)
            return {"success": False, "error": str(e), "symbol": symbol}

    def get_multiple_stocks(self, symbols: list) -> list:
        return [self.get_stock_price(s) for s in symbols]

    def get_stock_history(self, symbol: str, period: str = "1mo") -> list:
        """Return OHLCV data for a given period."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=period)
            if hist.empty:
                return []
            result = []
            for idx, row in hist.iterrows():
                result.append({
                    "date": str(idx.date()),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"]),
                })
            return result
        except Exception as e:
            logger.error("get_stock_history failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Crypto
    # ------------------------------------------------------------------

    def get_crypto_price(self, coin_id: str) -> dict:
        """Return crypto price from CoinGecko (free API)."""
        try:
            def _fetch():
                import requests
                resp = requests.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={
                        "ids": coin_id,
                        "vs_currencies": "inr,usd",
                        "include_24hr_change": "true",
                        "include_market_cap": "true",
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json().get(coin_id, {})
                if not data:
                    return {"success": False, "error": f"Unknown coin: {coin_id}"}
                return {
                    "success": True,
                    "coin": coin_id,
                    "price_inr": data.get("inr"),
                    "price_usd": data.get("usd"),
                    "change_24h": data.get("usd_24h_change"),
                    "market_cap_usd": data.get("usd_market_cap"),
                }
            return _cached(f"crypto:{coin_id}", _fetch)
        except Exception as e:
            logger.error("get_crypto_price failed for %s: %s", coin_id, e)
            return {"success": False, "error": str(e), "coin": coin_id}

    # ------------------------------------------------------------------
    # Market summary
    # ------------------------------------------------------------------

    def get_market_summary(self) -> dict:
        """Return key market indices."""
        indices = {
            "Nifty 50": "^NSEI",
            "Sensex": "^BSESN",
            "S&P 500": "^GSPC",
            "Nasdaq": "^IXIC",
        }
        summary = {}
        for name, sym in indices.items():
            data = self.get_stock_price(sym)
            summary[name] = {
                "price": data.get("price"),
                "change_pct": data.get("change_pct"),
                "success": data.get("success"),
            }

        btc = self.get_crypto_price("bitcoin")
        eth = self.get_crypto_price("ethereum")
        summary["Bitcoin"] = {
            "price": btc.get("price_usd"),
            "change_pct": btc.get("change_24h"),
            "success": btc.get("success"),
        }
        summary["Ethereum"] = {
            "price": eth.get("price_usd"),
            "change_pct": eth.get("change_24h"),
            "success": eth.get("success"),
        }
        return summary

    def search_stock(self, query: str) -> list:
        """Find stock symbol by company name using yfinance."""
        try:
            import yfinance as yf
            tickers = yf.Tickers(query)
            results = []
            for sym, t in (getattr(tickers, "tickers", None) or {}).items():
                try:
                    info = t.info or {}
                    results.append({
                        "symbol": sym,
                        "name": info.get("longName", sym),
                        "exchange": info.get("exchange", ""),
                    })
                except Exception:
                    pass
            return results[:10]
        except Exception as e:
            logger.error("search_stock failed: %s", e)
            return []

    def set_price_alert(self, symbol: str, target_price: float, direction: str = "above") -> dict:
        """Register a price alert — returns info for ProactiveMonitor to handle."""
        return {
            "success": True,
            "symbol": symbol,
            "target_price": target_price,
            "direction": direction,
            "message": (
                f"Price alert registered: {symbol} {direction} {target_price}. "
                "The autonomy monitor will notify you when the condition is met."
            ),
        }

    def get_news_for_stock(self, symbol: str) -> list:
        """Return recent news items from yfinance."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            news = ticker.news or []
            return [
                {
                    "title": item.get("title", ""),
                    "publisher": item.get("publisher", ""),
                    "link": item.get("link", ""),
                    "published": item.get("providerPublishTime", ""),
                }
                for item in news[:10]
            ]
        except Exception as e:
            logger.error("get_news_for_stock failed: %s", e)
            return []

    def get_portfolio_value(self, holdings: dict) -> dict:
        """
        Calculate portfolio value.
        holdings = {symbol: quantity}
        """
        try:
            total = 0.0
            breakdown = {}
            for symbol, qty in holdings.items():
                data = self.get_stock_price(symbol)
                price = data.get("price") or 0
                value = price * qty
                total += value
                breakdown[symbol] = {
                    "qty": qty,
                    "price": price,
                    "value": round(value, 2),
                    "change_pct": data.get("change_pct"),
                    "currency": data.get("currency", "INR"),
                }
            return {
                "success": True,
                "total_value": round(total, 2),
                "holdings": breakdown,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mt = MarketTracker()
    print("MarketTracker module OK")
    print(mt.get_stock_price("RELIANCE.NS"))
