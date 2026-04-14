"""
Rich-based terminal UI: banner, prompts, slash commands, typed replies, and voice.
"""

from __future__ import annotations

import re
import shlex
import threading
import time
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from skills.search.websearch import web_search
from skills.voice.wakeword import play_wake_beep
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.brain import Brain
    from core.context import Context
    from core.memory import Memory
    from skills.tools.registry import ToolRegistry
    from skills.voice.listener import Listener
    from skills.voice.speaker import Speaker
    from skills.voice.wakeword import WakeWordDetector

logger = get_logger(__name__)

# Iron Man–inspired ASCII banner (cyan / blue accents applied when printing).
BANNER = r"""
     ____.       .___      .__        __    
     \_ |__ __ __|   | ____ |__| ____ |  | __
      | __ \  |  \   |/    \|  |/    \|  |/ /
      | \_\ \  |  /   |   |  |   |   \    < 
      |___  /____/|___|___|  /___|___/ __|_ \
          \/               \/           \/  
"""


class CLI:
    """Interactive console loop for Jarvis."""

    def __init__(
        self,
        brain: Brain,
        memory: Memory,
        context: Context,
        console: Console | None = None,
        listener: Listener | None = None,
        speaker: Speaker | None = None,
        voice_config: dict[str, Any] | None = None,
        wake_detector: WakeWordDetector | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self._brain = brain
        self._memory = memory
        self._context = context
        self._registry = registry
        self._console = console or Console(highlight=False)
        self._listener = listener
        self._speaker = speaker
        self._voice_cfg: dict[str, Any] = dict(voice_config or {})
        self._wake_detector = wake_detector
        self._io_lock = threading.Lock()
        self._shutdown_requested = False

        cfg_on = bool(self._voice_cfg.get("enabled"))
        self._voice_mode: bool = cfg_on and bool(
            listener and getattr(listener, "available", False)
        )
        self._auto_speak: bool = bool(self._voice_cfg.get("auto_speak", True))

        # FIXED: optional skip markdown cleanup when piping to a markdown renderer
        iface = {}
        bc = getattr(self._brain, "_config", None)
        if isinstance(bc, dict):
            iface = bc.get("interface") or {}
        self._markdown_in_terminal: bool = bool(iface.get("markdown_in_terminal", False))

    def _markdown_in_terminal_enabled(self) -> bool:
        return self._markdown_in_terminal

    # FIXED: strip markdown artifacts for plain terminal display
    def _clean_for_terminal(self, text: str) -> str:
        if not text:
            return text
        s = text
        while True:
            m = re.search(r"```[a-zA-Z0-9_-]*\s*\r?\n([\s\S]*?)\r?\n```", s)
            if not m:
                m2 = re.search(r"```[a-zA-Z0-9_-]*\s*([\s\S]*?)```", s)
                if not m2:
                    break
                s = s[: m2.start()] + m2.group(1).strip("\n") + s[m2.end() :]
                continue
            s = s[: m.start()] + m.group(1).strip("\n") + s[m.end() :]
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        s = re.sub(r"(?m)^#+\s*", "", s)
        s = re.sub(r"(?m)^(\s*)-\s+", r"\1• ", s)
        return s

    def _voice_stt_settings(self) -> dict[str, Any]:
        return self._voice_cfg.get("stt") or {}

    def _voice_tts_settings(self) -> dict[str, Any]:
        return self._voice_cfg.get("tts") or {}

    def _ensure_listener(self) -> bool:
        if self._listener and getattr(self._listener, "available", False):
            return True
        try:
            from skills.voice.listener import Listener

            stt = self._voice_stt_settings()
            self._listener = Listener(
                model_size=str(stt.get("model", "base")),
                device=str(stt.get("device", "cpu")),
                language=str(stt.get("language", "en")),
            )
            return bool(self._listener.available)
        except Exception as e:
            logger.exception("Could not create Listener: %s", e)
            self._listener = None
            return False

    def _ensure_speaker(self) -> bool:
        if self._speaker and self._speaker.is_ready:
            return True
        try:
            from skills.voice.speaker import Speaker

            tts = self._voice_tts_settings()
            self._speaker = Speaker(
                voice=str(tts.get("voice", "en-GB-RyanNeural")),
                rate=str(tts.get("rate", "+10%")),
                volume=str(tts.get("volume", "+0%")),
                prefer_engine=str(tts.get("engine", "edge-tts")).lower(),
            )
            return self._speaker.is_ready
        except Exception as e:
            logger.exception("Could not create Speaker: %s", e)
            self._speaker = None
            return False

    def _maybe_speak(self, text: str, *, ignore_voice_mode: bool = False) -> None:
        """
        Speak assistant output when ``auto_speak`` is on and a speaker exists.
        Normally requires interactive ``_voice_mode``; wake-word turns pass
        ``ignore_voice_mode=True`` so replies can still be read aloud.
        """
        if not self._auto_speak or not self._speaker:
            return
        if not ignore_voice_mode and not self._voice_mode:
            return
        try:
            self._speaker.speak_sync(text)
        except Exception as e:
            logger.warning("TTS failed: %s", e)

    def _type_response(self, text: str, delay: float = 0.035) -> None:
        """Print Jarvis reply word-by-word for a subtle typing effect."""
        if not self._markdown_in_terminal_enabled():
            text = self._clean_for_terminal(text)
        tail = text.rstrip()
        if len(tail) > 200:
            last20 = tail[-20:]
            if not re.search(r"[.!?]", last20):
                text = tail + "..."
        prefix = Text("[JARVIS] ", style="bold cyan")
        words = text.split()
        with self._io_lock:
            self._console.print(prefix, end="")
            if not words:
                self._console.print()
                return
        for i, w in enumerate(words):
            with self._io_lock:
                self._console.print(w, end="")
                if i < len(words) - 1:
                    self._console.print(" ", end="")
                self._console.file.flush()
            time.sleep(delay)
        with self._io_lock:
            self._console.print()

    def _build_llm_messages(self) -> list[dict[str, str]]:
        system = self._context.get_system_prompt()
        return [{"role": "system", "content": system}, *self._memory.get_history()]

    def _submit_user_text(
        self,
        line: str,
        *,
        ignore_voice_mode_for_tts: bool = False,
    ) -> bool:
        """
        Handle one user line (voice or typed). Returns False if session should exit.
        """
        user_line = line.strip()
        if not user_line:
            return True

        if user_line.startswith("/"):
            return self._handle_slash(user_line)

        self._memory.add("user", user_line)
        messages = self._build_llm_messages()

        try:
            if self._registry is not None:
                routed = self._registry.route(user_line, messages)
                reply = (routed.get("final_response") or routed.get("response") or "").strip()
                tool_used = str(routed.get("tool_used") or "")
                action = str(routed.get("action") or "")
                if tool_used and tool_used not in ("chat", "direct"):
                    with self._io_lock:
                        self._console.print(Text(f"[TOOL: {tool_used}.{action}]", style="dim yellow"))
                if not reply:
                    reply = self._brain.chat(messages)
            else:
                reply = self._brain.chat(messages)
        except Exception as e:
            logger.exception("Brain.chat failed: %s", e)
            self._console.print(Text(f"I hit an unexpected error: {e}", style="red"))
            self._memory.drop_last()
            return True

        self._memory.add("assistant", reply)
        self._type_response(reply)
        self._maybe_speak(reply, ignore_voice_mode=ignore_voice_mode_for_tts)
        return True

    def handle_wake_event(self) -> None:
        """
        Wake-word path: attention tone, one STT pass, then same pipeline as typed input.
        Runs on the detector thread; console access is locked.
        """
        try:
            play_wake_beep()
        except Exception as e:
            logger.warning("Wake beep failed: %s", e)

        if not self._listener or not getattr(self._listener, "available", False):
            with self._io_lock:
                self._console.print(
                    Text("Wake received but speech input is unavailable.", style="red")
                )
            return

        with self._io_lock:
            self._console.print(Text("🎤 Listening (wake)...", style="yellow"))

        try:
            heard = self._listener.listen_once(timeout=10.0, phrase_limit=30.0)
        except Exception as e:
            logger.exception("Post-wake listen failed: %s", e)
            heard = None

        if not heard:
            with self._io_lock:
                self._console.print(Text("Didn't catch that, Sir.", style="yellow"))
            return

        if not self._submit_user_text(heard, ignore_voice_mode_for_tts=True):
            self._shutdown_requested = True

    def _read_user_line(self) -> str | None:
        """
        Text prompt or one voice utterance. Returns None on EOF-style exit request.
        """
        if self._voice_mode and self._listener and self._listener.available:
            self._console.print(Text.from_markup("[bold green][You 🎤][/bold green] > "))
            self._console.print(Text("🎤 Listening...", style="yellow"))
            try:
                heard = self._listener.listen_once(timeout=10.0, phrase_limit=30.0)
            except Exception as e:
                logger.exception("Voice input failed: %s", e)
                self._console.print(Text(f"Voice input error: {e}", style="red"))
                return ""

            if not heard:
                self._console.print(Text("Didn't catch that, Sir.", style="yellow"))
                return ""
            return heard.strip()

        try:
            return self._console.input("[bold green][You][/bold green] > ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    def _handle_slash(self, line: str) -> bool:
        """
        Process slash commands. Returns True to continue, False to exit.
        """
        parts = shlex.split(line)
        cmd = parts[0].lower() if parts else ""

        if cmd == "/exit":
            self._console.print(Text("Shutting down. Goodbye, Sir.", style="yellow"))
            return False

        if cmd == "/clear":
            self._memory.clear()
            self._console.print(Text("Conversation memory cleared.", style="yellow"))
            return True

        if cmd == "/provider":
            chain = self._brain.get_configured_providers()
            self._console.print(
                Text.from_markup(
                    f"[yellow]Configured order:[/yellow] {' → '.join(chain) if chain else '—'}"
                )
            )
            self._console.print(
                Text.from_markup(
                    f"[yellow]Active (last success):[/yellow] [bold]{self._brain.get_active_provider()}[/bold]"
                )
            )
            return True

        if cmd == "/help":
            help_text = (
                "/help — show this help\n"
                "/clear — clear conversation memory\n"
                "/search <query> — run a web search and print results\n"
                "/tools — list tool-capable skills and actions\n"
                "/run <command> — run a shell command and print output\n"
                "/file <path> — read a text file (truncated)\n"
                "/sysinfo — CPU, RAM, disk, battery, uptime\n"
                "/code <description> — generate Python code via the code assistant\n"
                "/exec <python code> — run a Python snippet (10s timeout)\n"
                "/provider — show configured providers and last active\n"
                "/voice on|off|status — voice input / output\n"
                "/speak <text> — speak text via TTS\n"
                "/listen — one-shot microphone test\n"
                "/voices — list English edge-tts voices\n"
                "/exit — quit Jarvis"
            )
            self._console.print(Text(help_text, style="yellow"))
            return True

        if cmd == "/tools":
            if not self._registry:
                self._console.print(Text("Tool registry not available.", style="red"))
                return True
            desc = self._registry.get_tools_description()
            self._console.print(Text(desc, style="cyan"))
            return True

        if cmd == "/run":
            if not self._registry:
                self._console.print(Text("Tool registry not available.", style="red"))
                return True
            if len(parts) < 2:
                self._console.print(Text("Usage: /run <command>", style="red"))
                return True
            command = " ".join(parts[1:])
            self._console.print(Text("[TOOL: shell.run]", style="dim yellow"))
            try:
                out = self._registry.shell_skill.run(command, timeout=30)
                block = "\n".join(
                    x
                    for x in (
                        out.get("stdout", ""),
                        out.get("stderr", ""),
                        f"(exit {out.get('returncode')}, {out.get('duration')}s)",
                    )
                    if x
                )
                self._type_response(block.strip() or "(no output)")
            except Exception as e:
                logger.exception("/run failed: %s", e)
                self._console.print(Text(f"/run failed: {e}", style="red"))
            return True

        if cmd == "/file":
            if not self._registry:
                self._console.print(Text("Tool registry not available.", style="red"))
                return True
            if len(parts) < 2:
                self._console.print(Text("Usage: /file <path>", style="red"))
                return True
            path = " ".join(parts[1:])
            self._console.print(Text("[TOOL: files.read_file]", style="dim yellow"))
            try:
                res = self._registry.file_skill.read_file(path)
                text = res.get("result", "") if res.get("success") else (res.get("error") or "read failed")
                self._type_response(str(text))
            except Exception as e:
                logger.exception("/file failed: %s", e)
                self._console.print(Text(f"/file failed: {e}", style="red"))
            return True

        if cmd == "/sysinfo":
            if not self._registry:
                self._console.print(Text("Tool registry not available.", style="red"))
                return True
            self._console.print(Text("[TOOL: shell.get_system_info]", style="dim yellow"))
            try:
                out = self._registry.shell_skill.get_system_info()
                self._type_response(out.get("stdout", str(out)))
            except Exception as e:
                logger.exception("/sysinfo failed: %s", e)
                self._console.print(Text(f"/sysinfo failed: {e}", style="red"))
            return True

        if cmd == "/code":
            if not self._registry:
                self._console.print(Text("Tool registry not available.", style="red"))
                return True
            if len(parts) < 2:
                self._console.print(Text("Usage: /code <description>", style="red"))
                return True
            description = " ".join(parts[1:])
            self._console.print(Text("[TOOL: code.generate]", style="dim yellow"))
            try:
                res = self._registry.code_skill.generate(description, language="python")
                text = res.get("result", "") if res.get("success") else (res.get("error") or "generation failed")
                self._type_response(str(text))
            except Exception as e:
                logger.exception("/code failed: %s", e)
                self._console.print(Text(f"/code failed: {e}", style="red"))
            return True

        if cmd == "/exec":
            if not self._registry:
                self._console.print(Text("Tool registry not available.", style="red"))
                return True
            _, _, rest = line.partition(" ")
            code = rest.strip()
            if not code:
                self._console.print(Text("Usage: /exec <python code>", style="red"))
                return True
            self._console.print(Text("[TOOL: code.run_snippet]", style="dim yellow"))
            try:
                res = self._registry.code_skill.run_snippet(code, language="python")
                text = res.get("result", "") if res.get("success") else (res.get("error") or res.get("result") or "exec failed")
                self._type_response(str(text))
            except Exception as e:
                logger.exception("/exec failed: %s", e)
                self._console.print(Text(f"/exec failed: {e}", style="red"))
            return True

        if cmd == "/search":
            if len(parts) < 2:
                self._console.print(Text("Usage: /search <query>", style="red"))
                return True
            query = " ".join(parts[1:])
            try:
                result = web_search(query)
                self._type_response(result)
                self._maybe_speak(result)
            except Exception as e:
                logger.exception("Search command failed: %s", e)
                self._console.print(Text(f"Search failed: {e}", style="red"))
            return True

        if cmd == "/voice":
            return self._cmd_voice(parts)

        if cmd == "/speak":
            if len(parts) < 2:
                self._console.print(Text("Usage: /speak <text>", style="red"))
                return True
            text = " ".join(parts[1:])
            if not self._ensure_speaker():
                self._console.print(Text("TTS unavailable.", style="red"))
                return True
            try:
                self._speaker.speak_sync(text)
            except Exception as e:
                logger.warning("/speak failed: %s", e)
                self._console.print(Text(f"Speak failed: {e}", style="red"))
            return True

        if cmd == "/listen":
            if not self._ensure_listener():
                self._console.print(Text("Listener unavailable.", style="red"))
                return True
            self._console.print(Text("🎤 Listening (one shot)...", style="yellow"))
            try:
                heard = self._listener.listen_once(timeout=10.0, phrase_limit=30.0)
            except Exception as e:
                logger.exception("/listen failed: %s", e)
                self._console.print(Text(f"Listen failed: {e}", style="red"))
                return True
            if heard:
                self._console.print(Text(f"Heard: {heard}", style="cyan"))
            else:
                self._console.print(Text("Heard nothing (silence or error).", style="yellow"))
            return True

        if cmd == "/voices":
            if not self._ensure_speaker():
                self._console.print(Text("Speaker engine unavailable.", style="red"))
                return True
            try:
                voices = self._speaker.list_voices()
            except Exception as e:
                self._console.print(Text(f"Could not list voices: {e}", style="red"))
                return True
            if not voices:
                self._console.print(Text("No voices returned (check edge-tts / network).", style="yellow"))
                return True
            table = Table(title="English edge-tts voices (sample)")
            table.add_column("ShortName", style="cyan")
            table.add_column("Locale")
            table.add_column("Gender")
            for v in voices[:80]:
                table.add_row(
                    str(v.get("ShortName", "")),
                    str(v.get("Locale", "")),
                    str(v.get("Gender", "")),
                )
            self._console.print(table)
            if len(voices) > 80:
                self._console.print(Text(f"... and {len(voices) - 80} more", style="yellow"))
            return True

        self._console.print(Text(f"Unknown command {cmd}. Type /help for a list.", style="red"))
        return True

    def _cmd_voice(self, parts: list[str]) -> bool:
        sub = parts[1].lower() if len(parts) > 1 else "status"

        if sub == "off":
            self._voice_mode = False
            self._console.print(Text("Voice mode off (text input).", style="yellow"))
            return True

        if sub == "on":
            if not self._ensure_listener():
                self._console.print(
                    Text("Could not start voice mode: microphone / STT unavailable.", style="red")
                )
                return True
            self._ensure_speaker()
            self._voice_mode = True
            self._console.print(
                Text(
                    "Voice mode on: microphone input; responses spoken when auto_speak is enabled.",
                    style="yellow",
                )
            )
            return True

        if sub == "status":
            tts = self._voice_tts_settings()
            stt = self._voice_stt_settings()
            self._console.print(
                Text.from_markup(
                    f"[yellow]Voice mode:[/yellow] {'[bold]ON[/bold]' if self._voice_mode else 'off'}\n"
                    f"[yellow]auto_speak:[/yellow] {self._auto_speak}\n"
                    f"[yellow]TTS engine (preferred):[/yellow] {tts.get('engine', 'edge-tts')}\n"
                    f"[yellow]STT model:[/yellow] {stt.get('model', 'base')} on {stt.get('device', 'cpu')}\n"
                    f"[yellow]Listener OK:[/yellow] {bool(self._listener and self._listener.available)}\n"
                    f"[yellow]Speaker OK:[/yellow] {bool(self._speaker and self._speaker.is_ready)}"
                )
            )
            return True

        self._console.print(Text("Usage: /voice on | /voice off | /voice status", style="red"))
        return True

    def _print_banner(self) -> None:
        title = Text(BANNER.strip("\n"), style="bold cyan")
        panel = Panel(
            title,
            border_style="blue",
            subtitle="[bold blue]Just A Rather Very Intelligent System[/bold blue]",
            subtitle_align="center",
        )
        self._console.print(panel)

    def _show_startup_status(self) -> None:
        chain = self._brain.get_configured_providers()
        chain_txt = " → ".join(chain) if chain else "(none configured)"
        self._console.print(Text.from_markup(f"[yellow]Provider order:[/yellow] {chain_txt}"))
        active = self._brain.get_active_provider()
        self._console.print(
            Text.from_markup(
                f"[yellow]Active provider (last successful reply):[/yellow] [bold]{active}[/bold]"
            )
        )
        if self._wake_detector:
            self._console.print(
                Text.from_markup(
                    f"[yellow]Wake detector:[/yellow] [bold]{self._wake_detector.mode}[/bold]"
                )
            )
        skills_txt = self._format_active_skills()
        self._console.print(Text.from_markup(f"[yellow]Active skills:[/yellow] {skills_txt}"))
        self._console.print()

    def _format_active_skills(self) -> str:
        cfg = getattr(self._brain, "_config", {}).get("skills") or {}
        if not isinstance(cfg, dict) or not cfg:
            return "files, shell, clipboard, apps, code, search (defaults on)"
        enabled = [k for k, v in cfg.items() if v]
        disabled = [k for k, v in cfg.items() if not v]
        en = ", ".join(enabled) if enabled else "—"
        if disabled:
            return f"{en}  [dim](off: {', '.join(disabled)})[/dim]"
        return en

    def shutdown_voice(self) -> None:
        """Stop microphone threads, TTS worker, and wake detector."""
        try:
            if self._listener:
                self._listener.stop()
        except Exception as e:
            logger.warning("Listener stop: %s", e)
        try:
            if self._speaker:
                self._speaker.shutdown()
        except Exception as e:
            logger.warning("Speaker shutdown: %s", e)
        try:
            if self._wake_detector:
                self._wake_detector.stop()
        except Exception as e:
            logger.warning("Wake detector stop: %s", e)

    def run(self) -> None:
        """Start the main read-eval-print loop."""
        self._shutdown_requested = False
        self._print_banner()
        self._show_startup_status()

        while not self._shutdown_requested:
            try:
                user_line = self._read_user_line()
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                self._console.print(Text("Interrupted. Goodbye, Sir.", style="yellow"))
                break

            if user_line is None:
                self._console.print()
                self._console.print(Text("Interrupted. Goodbye, Sir.", style="yellow"))
                break

            if not user_line:
                continue

            if not self._submit_user_text(user_line):
                break

        self._console.print()
