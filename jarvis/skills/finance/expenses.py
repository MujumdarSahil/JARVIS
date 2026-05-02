"""
Personal expense tracker.

Primary storage: MongoDB expenses collection.
Fallback: expenses.json flat file (when MongoDB unavailable).
Default currency: INR (Mumbai).
"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TYPE_CHECKING

from utils.logger import get_logger

if TYPE_CHECKING:
    from core.db import Database

logger = get_logger(__name__)

CATEGORIES = frozenset([
    "food", "transport", "entertainment", "utilities",
    "shopping", "health", "education", "investment", "other",
])
_FALLBACK_FILE = Path("expenses.json")


class ExpenseTracker:
    """Track personal expenses with MongoDB-first, JSON-fallback storage."""

    def __init__(self, db: "Database", finance_config: dict | None = None) -> None:
        self._db = db
        self._cfg = finance_config or {}
        self._currency = self._cfg.get("currency", "INR")
        self._budgets: dict[str, float] = dict(self._cfg.get("budgets") or {})
        self._use_db = bool(getattr(db, "available", False))
        self._json_expenses: list[dict] = []
        self._json_path = Path(__file__).resolve().parents[2] / "expenses.json"
        if not self._use_db:
            self._json_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._json_path.exists():
                self._json_path.write_text("[]", encoding="utf-8")
            self._load_json()

    # ------------------------------------------------------------------
    # JSON fallback helpers
    # ------------------------------------------------------------------

    def _load_json(self) -> None:
        try:
            if self._json_path.exists():
                self._json_expenses = json.loads(self._json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not load expenses.json: %s", e)
            self._json_expenses = []

    def _save_json(self) -> None:
        try:
            self._json_path.write_text(
                json.dumps(self._json_expenses, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Could not save expenses.json: %s", e)

    def _load_expenses(self) -> list[dict]:
        """Always load fresh expenses from active backend."""
        if self._use_db:
            try:
                return list(self._db.find("expenses", {}, sort=[("timestamp", -1)]) or [])
            except Exception as e:
                logger.warning("MongoDB load failed, falling back to JSON: %s", e)
                self._load_json()
                return list(self._json_expenses)
        self._load_json()
        return list(self._json_expenses)

    # ------------------------------------------------------------------
    # Date range helpers
    # ------------------------------------------------------------------

    def _period_range(self, period: str) -> tuple[float, float]:
        now = datetime.now()
        if period == "this_month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start.timestamp(), now.timestamp()
        if period == "last_month":
            first_this = now.replace(day=1, hour=0, minute=0, second=0)
            last_month_end = first_this - timedelta(seconds=1)
            last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0)
            return last_month_start.timestamp(), last_month_end.timestamp()
        if period == "this_week":
            start = now - timedelta(days=now.weekday())
            start = start.replace(hour=0, minute=0, second=0)
            return start.timestamp(), now.timestamp()
        if period == "today":
            start = now.replace(hour=0, minute=0, second=0)
            return start.timestamp(), now.timestamp()
        # Default: this_month
        start = now.replace(day=1, hour=0, minute=0, second=0)
        return start.timestamp(), now.timestamp()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_expense(
        self,
        amount: float,
        category: str,
        description: str,
        date: str | None = None,
        currency: str | None = None,
    ) -> dict:
        """Add an expense entry."""
        try:
            cat = category.lower() if category.lower() in CATEGORIES else "other"
            ts = time.time()
            if date:
                try:
                    ts = datetime.strptime(date, "%Y-%m-%d").timestamp()
                except ValueError:
                    pass

            doc: dict = {
                "amount": float(amount),
                "category": cat,
                "description": description,
                "currency": currency or self._currency,
                "timestamp": ts,
                "date": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
            }

            if self._use_db:
                try:
                    self._db.insert("expenses", doc)
                    return {"success": True, "expense": doc}
                except Exception as e:
                    logger.warning("MongoDB insert failed, falling back to JSON: %s", e)
                    self._load_json()

            self._json_expenses.append(doc)
            self._save_json()
            logger.info("Expense saved to %s: %s %s", self._json_path, amount, category)
            return {"success": True, "expense": doc}
        except Exception as e:
            logger.error("add_expense failed: %s", e)
            return {"success": False, "error": str(e)}

    def get_expenses(self, period: str = "this_month", category: str | None = None) -> list:
        """Return expenses for a period, optionally filtered by category."""
        try:
            start_ts, end_ts = self._period_range(period)

            expenses = self._load_expenses()
            filtered = [
                e for e in expenses
                if start_ts <= e.get("timestamp", 0) <= end_ts
                and (not category or e.get("category") == category.lower())
            ]
            return sorted(filtered, key=lambda x: x.get("timestamp", 0), reverse=True)
        except Exception as e:
            logger.error("get_expenses failed: %s", e)
            return []

    def get_summary(self, period: str = "this_month") -> dict:
        """Return total and per-category breakdown."""
        try:
            expenses = self.get_expenses(period)
            total = sum(e.get("amount", 0) for e in expenses)
            by_category: dict[str, float] = {}
            for e in expenses:
                cat = e.get("category", "other")
                by_category[cat] = by_category.get(cat, 0) + e.get("amount", 0)
            return {
                "success": True,
                "period": period,
                "total": round(total, 2),
                "currency": self._currency,
                "by_category": {k: round(v, 2) for k, v in sorted(by_category.items(), key=lambda x: -x[1])},
                "count": len(expenses),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_monthly_report(self) -> str:
        """Return natural language monthly report."""
        try:
            summary = self.get_summary("this_month")
            total = summary.get("total", 0)
            cur = summary.get("currency", "INR")
            sym = "₹" if cur == "INR" else cur
            by_cat = summary.get("by_category", {})
            top = list(by_cat.items())[:3]
            top_str = ", ".join(f"{cat} ({sym}{amt:,.0f})" for cat, amt in top)
            return (
                f"This month you spent {sym}{total:,.0f}. "
                f"Top categories: {top_str}. "
                f"Total {summary.get('count', 0)} transactions."
            )
        except Exception as e:
            return f"Could not generate monthly report: {e}"

    def set_budget(self, category: str, amount: float, period: str = "monthly") -> dict:
        """Set a budget limit for a category."""
        try:
            self._budgets[category.lower()] = float(amount)
            return {"success": True, "category": category, "budget": amount, "period": period}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def check_budget_status(self) -> dict:
        """Return which categories are over/under budget."""
        try:
            summary = self.get_summary("this_month")
            by_cat = summary.get("by_category", {})
            status: dict[str, Any] = {}
            for cat, budget in self._budgets.items():
                spent = by_cat.get(cat, 0)
                over = spent > budget
                status[cat] = {
                    "budget": budget,
                    "spent": round(spent, 2),
                    "remaining": round(budget - spent, 2),
                    "pct_used": round(spent / budget * 100, 1) if budget else 0,
                    "over_budget": over,
                }
            return {"success": True, "status": status, "currency": self._currency}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def export_csv(self, period: str = "this_month") -> str:
        """Export expenses to CSV. Returns file path."""
        try:
            expenses = self.get_expenses(period)
            path = f"expenses_{period}_{int(time.time())}.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["date", "category", "amount", "currency", "description"])
                writer.writeheader()
                for e in expenses:
                    writer.writerow({
                        "date": e.get("date", ""),
                        "category": e.get("category", ""),
                        "amount": e.get("amount", ""),
                        "currency": e.get("currency", self._currency),
                        "description": e.get("description", ""),
                    })
            return path
        except Exception as e:
            logger.error("export_csv failed: %s", e)
            return ""


if __name__ == "__main__":
    print("ExpenseTracker module OK")
