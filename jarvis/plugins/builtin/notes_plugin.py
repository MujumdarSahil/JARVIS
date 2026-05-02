"""Quick notes plugin with MongoDB + JSON fallback."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from plugins.base_plugin import BasePlugin


class NotesPlugin(BasePlugin):
    name = "notes"
    version = "1.0.0"
    description = "Quick note-taking — save and retrieve notes instantly"
    commands = ["/note", "/notes", "/recall"]
    keywords = ["make a note", "quick note", "note that", "what were my notes", "show notes"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._fallback_path = Path(__file__).resolve().parents[2] / "notes.json"

    def _mongo_available(self) -> bool:
        return bool(getattr(self.db, "available", False))

    def _save_fallback(self, note: dict[str, Any]) -> None:
        existing = []
        if self._fallback_path.exists():
            try:
                existing = json.loads(self._fallback_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.append(note)
        self._fallback_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def _load_all(self) -> list[dict[str, Any]]:
        if self._mongo_available():
            return [dict(x) for x in self.db.find("notes", {}, limit=100, sort=[("ts", -1)])]
        if not self._fallback_path.exists():
            return []
        try:
            rows = json.loads(self._fallback_path.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                return sorted(rows, key=lambda r: float(r.get("ts", 0)), reverse=True)
        except Exception:
            pass
        return []

    def execute(self, command: str, args: str, context: dict[str, Any]) -> dict[str, Any]:
        _ = context
        try:
            cmd = command.strip().lower()
            if cmd == "/note":
                text = args.strip()
                if not text:
                    return self._error("Usage: /note <text>")
                note = {"text": text, "ts": time.time()}
                if self._mongo_available():
                    self.db.insert("notes", note)
                else:
                    self._save_fallback(note)
                return self._success("Note saved.")
            if cmd == "/notes":
                notes = self._load_all()[:20]
                if not notes:
                    return self._success("No notes yet.")
                lines = ["Your notes:"]
                for n in notes:
                    lines.append(f"- {time.strftime('%Y-%m-%d %H:%M', time.localtime(float(n.get('ts', 0))))}: {n.get('text', '')}")
                return self._success("\n".join(lines))
            if cmd == "/recall":
                kw = args.strip().lower()
                if not kw:
                    return self._error("Usage: /recall <keyword>")
                hits = [n for n in self._load_all() if kw in str(n.get("text", "")).lower()]
                if not hits:
                    return self._success(f"No notes matched '{kw}'.")
                lines = [f"Matches for '{kw}':"]
                for n in hits[:20]:
                    lines.append(f"- {n.get('text', '')}")
                return self._success("\n".join(lines))
            if "note" in cmd:
                return self.execute("/notes", args, context)
            return self._error("Unsupported notes command")
        except Exception as e:
            return self._error(str(e))


if __name__ == "__main__":
    p = NotesPlugin()
    print(p.execute("/note", "Buy milk", {}))
