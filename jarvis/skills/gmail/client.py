"""
Gmail + Google Calendar API client.

Authentication: OAuth2 via google-auth-oauthlib.
  1. Place gmail_credentials.json (Desktop App OAuth client) in jarvis/ folder.
  2. First run opens browser for Google consent. Token stored in gmail_token.json.
  3. Subsequent runs use stored token (auto-refreshed).

SECURITY: credentials and token files are NEVER logged.
"""

from __future__ import annotations

import base64
import email as _email_lib
import html
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain

logger = get_logger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _strip_html(html_text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", " ", html_text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_body(payload: dict) -> str:
    """Recursively extract plain text from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    data = body.get("data", "")

    if mime_type == "text/plain" and data:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime_type == "text/html" and data:
        raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        return _strip_html(raw)

    for part in payload.get("parts", []):
        result = _decode_body(part)
        if result:
            return result

    return ""


class GmailClient:
    """Gmail + Google Calendar API client backed by OAuth2."""

    def __init__(
        self,
        credentials_path: str = "gmail_credentials.json",
        token_path: str = "gmail_token.json",
    ) -> None:
        self._credentials_path = Path(credentials_path)
        self._token_path = Path(token_path)
        self._gmail_service: Any = None
        self._calendar_service: Any = None
        self._creds: Any = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> bool:
        """Run OAuth2 flow. Opens browser on first run; uses stored token after."""
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = None
            if self._token_path.exists():
                creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)

            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    if not self._credentials_path.exists():
                        logger.error(
                            "Gmail credentials file not found: %s", self._credentials_path
                        )
                        return False
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self._credentials_path), SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                # Save token (NEVER log its contents)
                self._token_path.write_text(creds.to_json(), encoding="utf-8")

            self._creds = creds
            self._gmail_service = build("gmail", "v1", credentials=creds)
            self._calendar_service = build("calendar", "v3", credentials=creds)
            logger.info("Gmail + Calendar authenticated successfully")
            return True

        except Exception as e:
            logger.error("Gmail authentication failed: %s", e)
            return False

    def is_authenticated(self) -> bool:
        """Return True if a valid token exists and services are ready."""
        try:
            if self._gmail_service is not None:
                return True
            if not self._token_path.exists():
                return False
            from google.oauth2.credentials import Credentials

            creds = Credentials.from_authorized_user_file(str(self._token_path), SCOPES)
            return bool(creds and creds.valid)
        except Exception:
            return False

    def _ensure_auth(self) -> bool:
        if self._gmail_service is None:
            return self.authenticate()
        return True

    # ------------------------------------------------------------------
    # Gmail — inbox / reading
    # ------------------------------------------------------------------

    def get_inbox(self, max_results: int = 20, unread_only: bool = True) -> list:
        """Return list of inbox email summaries."""
        try:
            if not self._ensure_auth():
                return []
            q = "is:unread" if unread_only else ""
            resp = (
                self._gmail_service.users()
                .messages()
                .list(userId="me", maxResults=max_results, q=q)
                .execute()
            )
            messages = resp.get("messages", [])
            result = []
            for msg in messages:
                detail = (
                    self._gmail_service.users()
                    .messages()
                    .get(userId="me", id=msg["id"], format="metadata",
                         metadataHeaders=["Subject", "From", "Date"])
                    .execute()
                )
                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                labels = detail.get("labelIds", [])
                result.append({
                    "id": msg["id"],
                    "subject": headers.get("Subject", "(no subject)"),
                    "sender": headers.get("From", ""),
                    "date": headers.get("Date", ""),
                    "snippet": detail.get("snippet", ""),
                    "is_read": "UNREAD" not in labels,
                    "labels": labels,
                })
            return result
        except Exception as e:
            logger.error("get_inbox failed: %s", e)
            return []

    def get_email(self, email_id: str) -> dict:
        """Return full email dict with plain-text body."""
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated"}
            detail = (
                self._gmail_service.users()
                .messages()
                .get(userId="me", id=email_id, format="full")
                .execute()
            )
            payload = detail.get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            body = _decode_body(payload)
            labels = detail.get("labelIds", [])
            return {
                "success": True,
                "id": email_id,
                "subject": headers.get("Subject", "(no subject)"),
                "sender": headers.get("From", ""),
                "to": headers.get("To", ""),
                "date": headers.get("Date", ""),
                "body": body,
                "snippet": detail.get("snippet", ""),
                "is_read": "UNREAD" not in labels,
                "labels": labels,
            }
        except Exception as e:
            logger.error("get_email failed: %s", e)
            return {"success": False, "error": str(e)}

    def search_emails(self, query: str, max_results: int = 10) -> list:
        """Search emails using Gmail search query syntax."""
        try:
            if not self._ensure_auth():
                return []
            resp = (
                self._gmail_service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            messages = resp.get("messages", [])
            result = []
            for msg in messages:
                detail = (
                    self._gmail_service.users()
                    .messages()
                    .get(userId="me", id=msg["id"], format="metadata",
                         metadataHeaders=["Subject", "From", "Date"])
                    .execute()
                )
                headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                result.append({
                    "id": msg["id"],
                    "subject": headers.get("Subject", "(no subject)"),
                    "sender": headers.get("From", ""),
                    "date": headers.get("Date", ""),
                    "snippet": detail.get("snippet", ""),
                })
            return result
        except Exception as e:
            logger.error("search_emails failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Gmail — sending / drafting
    # ------------------------------------------------------------------

    def send_email(self, to: str, subject: str, body: str, cc: str | None = None) -> dict:
        """
        Send an email.

        IMPORTANT: always returns requires_confirmation=True.
        The calling code MUST obtain explicit user confirmation before calling this.
        """
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated", "requires_confirmation": True}

            import email.mime.text
            import email.mime.multipart

            msg = email.mime.multipart.MIMEMultipart()
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc
            msg.attach(email.mime.text.MIMEText(body, "plain"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            sent = (
                self._gmail_service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
            return {
                "success": True,
                "message_id": sent.get("id"),
                "requires_confirmation": True,
            }
        except Exception as e:
            logger.error("send_email failed: %s", e)
            return {"success": False, "error": str(e), "requires_confirmation": True}

    def draft_email(self, to: str, subject: str, body: str) -> dict:
        """Save email as a draft. Does NOT send."""
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated"}

            import email.mime.text
            import email.mime.multipart

            msg = email.mime.multipart.MIMEMultipart()
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(email.mime.text.MIMEText(body, "plain"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            draft = (
                self._gmail_service.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": raw}})
                .execute()
            )
            return {"success": True, "draft_id": draft.get("id")}
        except Exception as e:
            logger.error("draft_email failed: %s", e)
            return {"success": False, "error": str(e)}

    def mark_read(self, email_id: str) -> dict:
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated"}
            self._gmail_service.users().messages().modify(
                userId="me", id=email_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mark_unread(self, email_id: str) -> dict:
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated"}
            self._gmail_service.users().messages().modify(
                userId="me", id=email_id, body={"addLabelIds": ["UNREAD"]}
            ).execute()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def archive_email(self, email_id: str) -> dict:
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated"}
            self._gmail_service.users().messages().modify(
                userId="me", id=email_id, body={"removeLabelIds": ["INBOX"]}
            ).execute()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_email(self, email_id: str, trash: bool = True) -> dict:
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated"}
            if trash:
                self._gmail_service.users().messages().trash(userId="me", id=email_id).execute()
            else:
                self._gmail_service.users().messages().delete(userId="me", id=email_id).execute()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_labels(self) -> list:
        try:
            if not self._ensure_auth():
                return []
            resp = self._gmail_service.users().labels().list(userId="me").execute()
            return [{"id": l["id"], "name": l["name"]} for l in resp.get("labels", [])]
        except Exception as e:
            logger.error("get_labels failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Google Calendar
    # ------------------------------------------------------------------

    def get_calendar_events(self, days_ahead: int = 7) -> list:
        """Return upcoming calendar events for the next N days."""
        try:
            if not self._ensure_auth():
                return []
            from datetime import datetime, timezone, timedelta

            now = datetime.now(timezone.utc)
            end = now + timedelta(days=days_ahead)
            resp = (
                self._calendar_service.events()
                .list(
                    calendarId="primary",
                    timeMin=now.isoformat(),
                    timeMax=end.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = []
            for ev in resp.get("items", []):
                start = ev.get("start", {})
                end_ev = ev.get("end", {})
                attendees = [a.get("email", "") for a in ev.get("attendees", [])]
                events.append({
                    "id": ev.get("id"),
                    "title": ev.get("summary", "(no title)"),
                    "start": start.get("dateTime") or start.get("date"),
                    "end": end_ev.get("dateTime") or end_ev.get("date"),
                    "location": ev.get("location", ""),
                    "description": ev.get("description", ""),
                    "attendees": attendees,
                })
            return events
        except Exception as e:
            logger.error("get_calendar_events failed: %s", e)
            return []

    def create_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str,
        description: str = "",
        attendees: list | None = None,
    ) -> dict:
        """Create a Google Calendar event."""
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated"}
            attendees = attendees or []
            event_body: dict = {
                "summary": title,
                "description": description,
                "start": {"dateTime": start_datetime, "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": end_datetime, "timeZone": "Asia/Kolkata"},
            }
            if attendees:
                event_body["attendees"] = [{"email": a} for a in attendees]
            ev = (
                self._calendar_service.events()
                .insert(calendarId="primary", body=event_body)
                .execute()
            )
            return {"success": True, "event_id": ev.get("id"), "html_link": ev.get("htmlLink")}
        except Exception as e:
            logger.error("create_event failed: %s", e)
            return {"success": False, "error": str(e)}

    def delete_event(self, event_id: str) -> dict:
        try:
            if not self._ensure_auth():
                return {"success": False, "error": "Not authenticated"}
            self._calendar_service.events().delete(
                calendarId="primary", eventId=event_id
            ).execute()
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


class GmailComposer:
    """Uses Brain to draft and summarize emails."""

    def __init__(self, brain: "Brain") -> None:
        self._brain = brain

    def draft_from_instruction(self, instruction: str, context: dict | None = None) -> dict:
        """Draft an email from a natural language instruction."""
        try:
            ctx = context or {}
            sender_name = ctx.get("sender_name", "")
            recipient = ctx.get("recipient", "")
            tone = ctx.get("tone", "professional")

            prompt = (
                f"Draft an email based on this instruction: {instruction}\n"
                f"Sender name: {sender_name or 'me'}\n"
                f"Recipient: {recipient or 'the recipient'}\n"
                f"Tone: {tone}\n\n"
                "Return JSON with keys: subject, body, to, suggested_send_time.\n"
                "subject and body must be filled. to can be empty string if not known.\n"
                "Return ONLY the JSON object, no markdown."
            )
            messages = [
                {"role": "system", "content": "You are an email drafting assistant. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ]
            raw = self._brain.chat(messages)
            import json, re as _re
            m = _re.search(r"\{[\s\S]*\}", raw or "")
            if m:
                data = json.loads(m.group(0))
                return {
                    "subject": data.get("subject", ""),
                    "body": data.get("body", ""),
                    "to": data.get("to", ""),
                    "suggested_send_time": data.get("suggested_send_time", ""),
                }
            return {"subject": "", "body": raw, "to": "", "suggested_send_time": ""}
        except Exception as e:
            logger.error("draft_from_instruction failed: %s", e)
            return {"subject": "", "body": "", "to": "", "error": str(e)}

    def summarize_email(self, email: dict) -> str:
        """Return a one-paragraph summary of an email."""
        try:
            body = (email.get("body") or email.get("snippet") or "")[:3000]
            subject = email.get("subject", "")
            sender = email.get("sender", "")
            prompt = (
                f"Summarize this email in one short paragraph:\n"
                f"From: {sender}\nSubject: {subject}\nBody:\n{body}"
            )
            messages = [
                {"role": "system", "content": "You are a concise email summarizer."},
                {"role": "user", "content": prompt},
            ]
            return self._brain.chat(messages) or ""
        except Exception as e:
            return f"(summary failed: {e})"

    def summarize_inbox(self, emails: list) -> str:
        """Return a brief inbox overview."""
        try:
            n = len(emails)
            if n == 0:
                return "Your inbox is empty."
            top = emails[:3]
            subjects = ", ".join(
                f'"{e.get("subject", "?")}\" from {e.get("sender", "?").split("<")[0].strip()}'
                for e in top
            )
            return (
                f"You have {n} unread email{'s' if n != 1 else ''}. "
                f"Most important: {subjects}."
            )
        except Exception as e:
            return f"(inbox summary failed: {e})"

    def classify_email(self, email: dict) -> str:
        """Classify email into a category."""
        try:
            body = (email.get("body") or email.get("snippet") or "")[:500]
            subject = email.get("subject", "")
            sender = email.get("sender", "")
            prompt = (
                f"Classify this email into exactly one category from this list:\n"
                "urgent, newsletter, work, personal, spam, financial, notification\n\n"
                f"From: {sender}\nSubject: {subject}\nSnippet: {body}\n\n"
                "Return ONLY the category word, nothing else."
            )
            messages = [
                {"role": "system", "content": "You are an email classifier. Return only the category label."},
                {"role": "user", "content": prompt},
            ]
            result = (self._brain.chat(messages) or "notification").strip().lower()
            valid = {"urgent", "newsletter", "work", "personal", "spam", "financial", "notification"}
            return result if result in valid else "notification"
        except Exception:
            return "notification"


if __name__ == "__main__":
    print("GmailClient module OK — set gmail.enabled: true and add gmail_credentials.json to use.")
