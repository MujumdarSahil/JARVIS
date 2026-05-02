"""
Finance Agent — market summaries, stock prices, expenses, and portfolio tracking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.agents.base_agent import BaseAgent
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database
    from skills.finance.markets import MarketTracker
    from skills.finance.expenses import ExpenseTracker

logger = get_logger(__name__)


class FinanceAgent(BaseAgent):
    """Agent that handles market data and personal expense tracking."""

    def __init__(
        self,
        brain: "Brain",
        db: "Database",
        config: dict,
        market_tracker: "MarketTracker | None" = None,
        expense_tracker: "ExpenseTracker | None" = None,
    ) -> None:
        super().__init__("finance", brain, db, config)
        self._markets = market_tracker
        self._expenses = expense_tracker
        self._finance_cfg = config.get("finance") or {}

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        def _run():
            action = str(task.get("action") or task.get("type") or "").lower().strip()
            params = task.get("params") or {}

            if action == "market_summary":
                return self._market_summary()
            if action == "stock_price":
                return self._stock_price(params)
            if action == "crypto_price":
                return self._crypto_price(params)
            if action == "set_alert":
                return self._set_alert(params)
            if action == "add_expense":
                return self._add_expense(params)
            if action == "expense_summary":
                return self._expense_summary(params)
            if action == "monthly_report":
                return self._monthly_report()
            if action == "portfolio":
                return self._portfolio()
            if action == "budget_status":
                return self._budget_status()
            return {"success": False, "result": f"Unknown finance action: {action}"}

        return self._run_wrapped(task, _run)

    # ------------------------------------------------------------------
    # Market actions
    # ------------------------------------------------------------------

    def _market_summary(self) -> dict:
        if not self._markets:
            return {"success": False, "result": "MarketTracker not initialized."}
        summary = self._markets.get_market_summary()
        lines = []
        for name, data in summary.items():
            price = data.get("price")
            chg = data.get("change_pct")
            if price is None:
                lines.append(f"- {name}: N/A")
            else:
                arrow = "▲" if (chg or 0) >= 0 else "▼"
                lines.append(f"- {name}: {price:,.2f} {arrow}{abs(chg or 0):.2f}%")
        return {
            "success": True,
            "result": "Market Summary:\n" + "\n".join(lines),
            "data": summary,
        }

    def _stock_price(self, params: dict) -> dict:
        if not self._markets:
            return {"success": False, "result": "MarketTracker not initialized."}
        symbol = params.get("symbol") or params.get("ticker") or ""
        if not symbol:
            return {"success": False, "result": "No symbol provided."}
        data = self._markets.get_stock_price(symbol)
        if not data.get("success"):
            return {"success": False, "result": data.get("error", "Fetch failed"), "symbol": symbol}
        arrow = "▲" if data.get("change", 0) >= 0 else "▼"
        msg = (
            f"{data['symbol']}: {data['price']:,.2f} {data.get('currency', '')} "
            f"{arrow}{abs(data.get('change_pct', 0)):.2f}%"
        )
        return {"success": True, "result": msg, "data": data}

    def _crypto_price(self, params: dict) -> dict:
        if not self._markets:
            return {"success": False, "result": "MarketTracker not initialized."}
        coin = params.get("coin") or params.get("coin_id") or "bitcoin"
        data = self._markets.get_crypto_price(coin)
        if not data.get("success"):
            return {"success": False, "result": data.get("error", "Fetch failed"), "coin": coin}
        usd = data.get("price_usd")
        inr = data.get("price_inr")
        chg = data.get("change_24h") or 0
        arrow = "▲" if chg >= 0 else "▼"
        msg = f"{coin.title()}: ${usd:,.2f} / ₹{inr:,.0f} {arrow}{abs(chg):.2f}% (24h)"
        return {"success": True, "result": msg, "data": data}

    def _set_alert(self, params: dict) -> dict:
        if not self._markets:
            return {"success": False, "result": "MarketTracker not initialized."}
        symbol = params.get("symbol", "")
        target = float(params.get("target_price") or 0)
        direction = params.get("direction", "above")
        if not symbol or not target:
            return {"success": False, "result": "Missing symbol or target_price."}
        result = self._markets.set_price_alert(symbol, target, direction)
        return {"success": True, "result": result.get("message", "Alert set."), "data": result}

    # ------------------------------------------------------------------
    # Expense actions
    # ------------------------------------------------------------------

    def _add_expense(self, params: dict) -> dict:
        if not self._expenses:
            return {"success": False, "result": "ExpenseTracker not initialized."}
        amount = float(params.get("amount") or 0)
        category = params.get("category") or "other"
        description = params.get("description") or ""
        if not amount:
            return {"success": False, "result": "Amount required."}
        result = self._expenses.add_expense(
            amount, category, description,
            date=params.get("date"),
            currency=params.get("currency"),
        )
        if result.get("success"):
            logger.info("Expense logged: %s %s %s", amount, category, description)
        currency = self._finance_cfg.get("currency", "INR")
        sym = "₹" if currency == "INR" else currency
        msg = (
            f"Logged: {sym}{amount:,.0f} for {category} — {description}"
            if result.get("success")
            else f"Expense log failed: {result.get('error')}"
        )
        return {
            "success": result.get("success", False),
            "result": msg,
            "amount": amount,
            "category": category,
            "description": description,
        }

    def _expense_summary(self, params: dict) -> dict:
        if not self._expenses:
            return {"success": False, "result": "ExpenseTracker not initialized."}
        period = params.get("period", "this_month")
        summary = self._expenses.get_summary(period)
        if not summary.get("success"):
            return {"success": False, "result": summary.get("error", "Summary failed")}
        currency = summary.get("currency", "INR")
        sym = "₹" if currency == "INR" else currency
        total = summary.get("total", 0)
        by_cat = summary.get("by_category", {})
        lines = [f"Total: {sym}{total:,.0f}"]
        for cat, amt in by_cat.items():
            lines.append(f"  {cat}: {sym}{amt:,.0f}")
        return {"success": True, "result": "\n".join(lines), "data": summary}

    def _monthly_report(self) -> dict:
        if not self._expenses:
            return {"success": False, "result": "ExpenseTracker not initialized."}
        report = self._expenses.get_monthly_report()
        return {"success": True, "result": report}

    def _portfolio(self) -> dict:
        if not self._markets:
            return {"success": False, "result": "MarketTracker not initialized."}
        holdings = self._finance_cfg.get("portfolio") or {}
        if not holdings:
            return {"success": True, "result": "No portfolio holdings configured. Add them in config.yaml under finance.portfolio."}
        result = self._markets.get_portfolio_value(holdings)
        if not result.get("success"):
            return {"success": False, "result": result.get("error", "Portfolio fetch failed")}
        total = result.get("total_value", 0)
        lines = [f"Total Portfolio Value: ₹{total:,.2f}"]
        for sym, data in (result.get("holdings") or {}).items():
            lines.append(f"  {sym}: {data['qty']} shares × {data['price']} = ₹{data['value']:,.2f}")
        return {"success": True, "result": "\n".join(lines), "data": result}

    def _budget_status(self) -> dict:
        if not self._expenses:
            return {"success": False, "result": "ExpenseTracker not initialized."}
        status = self._expenses.check_budget_status()
        if not status.get("success"):
            return {"success": False, "result": status.get("error", "Budget check failed")}
        currency = status.get("currency", "INR")
        sym = "₹" if currency == "INR" else currency
        lines = []
        for cat, data in (status.get("status") or {}).items():
            indicator = "🔴 OVER" if data.get("over_budget") else "✅"
            lines.append(
                f"{indicator} {cat}: {sym}{data['spent']:,.0f} / {sym}{data['budget']:,.0f} "
                f"({data['pct_used']}%)"
            )
        return {
            "success": True,
            "result": "Budget Status:\n" + ("\n".join(lines) or "No budgets configured."),
            "data": status,
        }

    def get_daily_financial_brief(self) -> str:
        """Formatted brief for morning summary: market + expense + budget."""
        try:
            lines = []
            if self._markets:
                summary = self._markets.get_market_summary()
                nifty = summary.get("Nifty 50", {})
                btc = summary.get("Bitcoin", {})
                n_price = nifty.get("price") or 0
                n_chg = nifty.get("change_pct") or 0
                b_price = btc.get("price") or 0
                lines.append(
                    f"[MARKETS] Nifty: {n_price:,.0f} ({'+' if n_chg >= 0 else ''}{n_chg:.1f}%) | "
                    f"BTC: ${b_price:,.0f}"
                )
            if self._expenses:
                summary = self._expenses.get_summary("this_month")
                total = summary.get("total", 0)
                currency = summary.get("currency", "INR")
                sym = "₹" if currency == "INR" else currency
                lines.append(f"[EXPENSES] This month: {sym}{total:,.0f}")
                budget_status = self._expenses.check_budget_status()
                over = [k for k, v in (budget_status.get("status") or {}).items() if v.get("over_budget")]
                if over:
                    lines.append(f"[BUDGET] Over budget: {', '.join(over)}")
            return "\n".join(lines) if lines else "[FINANCE] Data unavailable"
        except Exception as e:
            return f"[FINANCE] Brief error: {e}"


if __name__ == "__main__":
    print("FinanceAgent module OK")
