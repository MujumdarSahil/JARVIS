"""
Gmail Agent — reads inbox, manages email/calendar, confirms before sending.

CRITICAL SAFETY RULE: send_email action ALWAYS returns requires_confirmation=True.
The CLI/web UI must show a confirmation prompt before calling gmail_client.send_email().
Auto-sending without explicit user approval in the same session is FORBIDDEN.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.agents.base_agent import BaseAgent
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.db import Database
    from skills.gmail.client import GmailClient, GmailComposer

logger = get_logger(__name__)


class GmailAgent(BaseAgent):
    """Agent that handles Gmail and Google Calendar actions."""

    def __init__(
        self,
        brain: "Brain",
        db: "Database",
        config: dict,
        gmail_client: "GmailClient | None" = None,
        gmail_composer: "GmailComposer | None" = None,
    ) -> None:
        super().__init__("gmail", brain, db, config)
        self._client = gmail_client
        self._composer = gmail_composer

    def _not_configured(self) -> dict:
        return {
            "success": False,
            "result": "Gmail is not enabled. Set gmail.enabled: true and add gmail_credentials.json.",
            "requires_confirmation": False,
        }

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        def _run():
            action = str(task.get("action") or task.get("type") or "").lower().strip()
            params = task.get("params") or {}

            if self._client is None:
                return self._not_configured()

            if action in ("read_inbox", "inbox"):
                return self._read_inbox(params)
            if action == "read_email":
                return self._read_email(params)
            if action == "send_email":
                return self._send_email(params)
            if action == "draft_email":
                return self._draft_email(params)
            if action == "search_emails":
                return self._search_emails(params)
            if action in ("get_calendar", "calendar"):
                return self._get_calendar(params)
            if action == "create_event":
                return self._create_event(params)
            if action == "summarize_day":
                return self._summarize_day()
            return {"success": False, "result": f"Unknown gmail action: {action}"}

        return self._run_wrapped(task, _run)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _read_inbox(self, params: dict) -> dict:
        emails = self._client.get_inbox(
            max_results=params.get("max_results", 20),
            unread_only=params.get("unread_only", True),
        )
        if not emails:
            return {"success": True, "result": "No unread emails found.", "emails": []}

        summary = ""
        if self._composer:
            summary = self._composer.summarize_inbox(emails)
        else:
            n = len(emails)
            summary = f"You have {n} unread email{'s' if n != 1 else ''}."

        return {"success": True, "result": summary, "emails": emails}

    def _read_email(self, params: dict) -> dict:
        email_id = params.get("id") or params.get("email_id") or ""
        if not email_id:
            # Try to find by subject
            subject = params.get("subject") or ""
            if subject:
                results = self._client.search_emails(f"subject:{subject}", max_results=1)
                if results:
                    email_id = results[0]["id"]
        if not email_id:
            return {"success": False, "result": "No email ID or subject provided."}

        email = self._client.get_email(email_id)
        if not email.get("success"):
            return {"success": False, "result": email.get("error", "Failed to fetch email")}

        summary = ""
        if self._composer:
            summary = self._composer.summarize_email(email)

        return {
            "success": True,
            "result": summary or email.get("body", ""),
            "email": email,
        }

    def _send_email(self, params: dict) -> dict:
        """
        SAFETY: Always returns requires_confirmation=True.
        Caller must explicitly confirm before invoking gmail_client.send_email().
        """
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        confirmed = params.get("confirmed", False)

        if not all([to, subject, body]):
            return {
                "success": False,
                "result": "Missing required fields: to, subject, body",
                "requires_confirmation": True,
            }

        if not confirmed:
            return {
                "success": True,
                "requires_confirmation": True,
                "result": (
                    f"Ready to send email to {to}.\n"
                    f"Subject: {subject}\n"
                    f"Body preview: {body[:200]}...\n\n"
                    "Please confirm before sending (reply 'yes' or 'confirm')."
                ),
                "pending_send": {"to": to, "subject": subject, "body": body},
            }

        # User confirmed
        result = self._client.send_email(to, subject, body, cc=params.get("cc"))
        msg = (
            f"Email sent to {to} — Subject: {subject}"
            if result.get("success")
            else f"Send failed: {result.get('error')}"
        )
        return {"success": result.get("success", False), "result": msg, "requires_confirmation": True}

    def _draft_email(self, params: dict) -> dict:
        instruction = params.get("instruction", "")
        to = params.get("to", "")
        subject = params.get("subject", "")
        body = params.get("body", "")

        if instruction and self._composer:
            drafted = self._composer.draft_from_instruction(instruction, params)
            to = to or drafted.get("to", "")
            subject = subject or drafted.get("subject", "")
            body = body or drafted.get("body", "")

        if not subject or not body:
            return {"success": False, "result": "Could not draft email — missing subject or body."}

        result = self._client.draft_email(to, subject, body)
        msg = (
            f"Draft saved — Subject: {subject}"
            if result.get("success")
            else f"Draft failed: {result.get('error')}"
        )
        return {"success": result.get("success", False), "result": msg, "draft": result}

    def _search_emails(self, params: dict) -> dict:
        query = params.get("query", "")
        if not query:
            return {"success": False, "result": "No search query provided."}
        emails = self._client.search_emails(query, max_results=params.get("max_results", 10))
        return {
            "success": True,
            "result": f"Found {len(emails)} email(s) for query: {query}",
            "emails": emails,
        }

    def _get_calendar(self, params: dict) -> dict:
        events = self._client.get_calendar_events(days_ahead=params.get("days_ahead", 7))
        if not events:
            return {"success": True, "result": "No upcoming calendar events.", "events": []}
        lines = []
        for ev in events:
            lines.append(f"- {ev.get('title')} @ {ev.get('start', '')[:16]}")
        summary = f"Upcoming {len(events)} event(s):\n" + "\n".join(lines)
        return {"success": True, "result": summary, "events": events}

    def _create_event(self, params: dict) -> dict:
        title = params.get("title", "")
        start = params.get("start_datetime", "")
        end = params.get("end_datetime", "")
        if not all([title, start, end]):
            return {"success": False, "result": "Missing title, start_datetime, or end_datetime."}
        result = self._client.create_event(
            title, start, end,
            description=params.get("description", ""),
            attendees=params.get("attendees", []),
        )
        msg = (
            f"Event '{title}' created for {start}"
            if result.get("success")
            else f"Create event failed: {result.get('error')}"
        )
        return {"success": result.get("success", False), "result": msg, "event": result}

    def _summarize_day(self) -> dict:
        inbox = self._client.get_inbox(max_results=10)
        events = self._client.get_calendar_events(days_ahead=1)

        inbox_summary = self._composer.summarize_inbox(inbox) if self._composer else f"{len(inbox)} unread emails."
        event_lines = [f"- {ev.get('title')} @ {ev.get('start', '')[:16]}" for ev in events]
        events_summary = "Today's events:\n" + "\n".join(event_lines) if event_lines else "No events today."

        daily_brief = f"{inbox_summary}\n\n{events_summary}"
        return {"success": True, "result": daily_brief, "emails": inbox, "events": events}

    def get_email_brief(self) -> str:
        """Formatted brief for morning summary: unread count + urgent + today's events."""
        try:
            emails = self._client.get_inbox(max_results=10) if self._client else []
            urgent = []
            if self._composer:
                for e in emails[:5]:
                    cat = self._composer.classify_email(e)
                    if cat == "urgent":
                        urgent.append(e.get("subject", ""))
            events = self._client.get_calendar_events(days_ahead=1) if self._client else []

            lines = [f"[GMAIL] {len(emails)} unread emails"]
            if urgent:
                lines.append(f"  Urgent: {', '.join(urgent[:3])}")
            if events:
                event_strs = [f"{ev.get('title')} at {ev.get('start', '')[:16]}" for ev in events[:3]]
                lines.append(f"[CALENDAR] Today: {'; '.join(event_strs)}")
            else:
                lines.append("[CALENDAR] No events today")
            return "\n".join(lines)
        except Exception as e:
            return f"[GMAIL] Brief unavailable: {e}"


if __name__ == "__main__":
    print("GmailAgent module OK")
