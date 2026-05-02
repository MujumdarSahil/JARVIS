"""
Email drafting helpers — re-exported from client.py for backwards compatibility.

The main implementation lives in GmailComposer (skills/gmail/client.py).
This file exists so external code can import from skills.gmail.composer if needed.
"""

from skills.gmail.client import GmailComposer

__all__ = ["GmailComposer"]
