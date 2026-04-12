"""
Builds system prompts with environment and persona metadata.
"""

from __future__ import annotations

import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Context:
    """Assembles the system prompt Jarvis uses for each conversation turn."""

    def __init__(self, config: dict[str, Any], base_dir: Path | None = None) -> None:
        self._config = config
        self._base_dir = base_dir if base_dir is not None else Path.cwd()

    def get_system_prompt(self) -> str:
        """
        Detailed system instructions: time, OS, cwd, persona, skills, output format.
        """
        # FIXED: JARVIS persona / no model meta or cutoff dates (top + bottom instructions)
        header = (
            "You are JARVIS (Just A Rather Very Intelligent System), a highly advanced AI assistant.\n"
            'When asked what model or AI you are, always say: "I am JARVIS, your personal AI system."\n'
            "Never mention training cutoff dates, knowledge cutoffs, or underlying model names.\n"
            "Never break character. You are JARVIS, not an AI assistant, not a language model.\n\n"
        )

        jarvis_cfg = self._config.get("jarvis") or self._config.get("yamljarvis") or {}
        name = jarvis_cfg.get("name", "Jarvis")
        now = datetime.now(timezone.utc).astimezone()
        when = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        os_name = platform.system()
        os_release = platform.release()
        os_version = platform.version()
        cwd = str(self._base_dir.resolve())

        skills = (
            "web_search — search the web for current information; "
            "file_control — read, write, and organize files when permitted; "
            "code_assistant — help with reading, writing, and debugging code; "
            "system_control — run safe system tasks when explicitly allowed."
        )

        footer = (
            "\n\nIMPORTANT: Never refer to yourself as an AI language model, never mention cutoff dates,\n"
            "never say what company made you. You are JARVIS. Always stay in character."
        )

        return (
            header
            + f"In this deployment your display name is {name}. You are modeled after the Jarvis from Iron Man: "
            "highly intelligent, concise, efficient, and unfailingly helpful. "
            'Address the user as "Sir" or "Ma\'am" occasionally when it fits naturally, '
            "but not every sentence.\n\n"
            f"Current date and time: {when}\n"
            f"Operating system: {os_name} {os_release}\n"
            f"OS version string: {os_version}\n"
            f"Current working directory: {cwd}\n\n"
            f"Available skills you may conceptually use (some require user action in this app): {skills}\n\n"
            "Respond in plain text only. Do not use Markdown formatting (no headings, bold, lists with "
            "asterisks, or code fences) unless the user explicitly asks for Markdown or code blocks."
            + footer
        )
