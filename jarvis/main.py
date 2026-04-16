"""
Jarvis entrypoint: load configuration, wire core services, voice stack, and CLI.
"""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path
from typing import Any

# Project root (directory containing this file) must be importable.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import threading
import webbrowser

import yaml
from rich.console import Console

from core.agents import CoderAgent, MemoryAgent, Orchestrator, PlannerAgent, ResearchAgent, VisionAgent
from core.brain import Brain
from core.context import Context
from core.db import db
from core.memory import Memory
from interface.cli import CLI
from skills.code.assistant import CodeAssistant
from skills.notifications import Notifier
from skills.search.websearch import SearchSkill
from skills.system import AppSkill, ClipboardSkill, FileSkill, ShellSkill
from skills.vision import ImageAnalyzer, ScreenSkill
from skills.smarthome import (
    DeviceManager,
    HomeAssistantClient,
    MQTTClient,
    RoutineManager,
    SmarthomeSkill,
)
from skills.tools.registry import ToolRegistry
from utils.logger import get_logger
from utils.generate_icons import generate_icons

logger = get_logger(__name__)


def load_config(path: Path) -> dict:
    """Parse ``config.yaml`` and normalize legacy/alias top-level keys."""
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.error("Could not read config %s: %s", path, e)
        return {}

    if "jarvis" not in data and "yamljarvis" in data:
        data["jarvis"] = data["yamljarvis"]

    if "voice" not in data and "yamlvoice" in data:
        data["voice"] = data["yamlvoice"]

    if "skills" not in data and "yamlskills" in data:
        data["skills"] = data["yamlskills"]

    if "web" not in data and "yamlweb" in data:
        data["web"] = data["yamlweb"]

    if "smarthome" not in data and "yamlsmarthome" in data:
        data["smarthome"] = data["yamlsmarthome"]

    if "database" not in data and "yamldatabase" in data:
        data["database"] = data["yamldatabase"]

    return data


def _build_voice_hardware(config: dict) -> tuple:
    """
    Create Listener / Speaker when voice or wake-word features are enabled.
    Returns (listener, speaker, voice_cfg).
    """
    vc = config.get("voice") or {}
    enabled = bool(vc.get("enabled"))
    wake_on = bool(vc.get("wake_word_enabled"))
    if not (enabled or wake_on):
        return None, None, vc

    listener = None
    speaker = None

    if enabled or wake_on:
        try:
            from skills.voice.listener import Listener

            stt = vc.get("stt") or {}
            listener = Listener(
                model_size=str(stt.get("model", "base")),
                device=str(stt.get("device", "cpu")),
                language=str(stt.get("language", "en")),
            )
            if not listener.available:
                logger.warning("Speech-to-text model did not load; voice input disabled.")
                listener = None
        except Exception as e:
            logger.exception("Listener initialization failed: %s", e)
            listener = None

    if enabled or wake_on:
        try:
            from skills.voice.speaker import Speaker

            tts = vc.get("tts") or {}
            speaker = Speaker(
                voice=str(tts.get("voice", "en-GB-RyanNeural")),
                rate=str(tts.get("rate", "+10%")),
                volume=str(tts.get("volume", "+0%")),
                prefer_engine=str(tts.get("engine", "edge-tts")).lower(),
            )
            if not speaker.is_ready:
                logger.warning("TTS worker did not start.")
                speaker = None
        except Exception as e:
            logger.exception("Speaker initialization failed: %s", e)
            speaker = None

    return listener, speaker, vc


def main() -> None:
    config_path = _ROOT / "config.yaml"
    config = load_config(config_path)

    _boot_console = Console(highlight=False, stderr=False)
    dcfg = config.get("database") or {}
    db_ok = False
    if isinstance(dcfg, dict) and bool(dcfg.get("enabled", True)):
        db_ok = db.connect(
            uri=str(dcfg.get("uri") or "mongodb://localhost:27017"),
            db_name=str(dcfg.get("name") or "jarvis"),
        )
    if db_ok:
        _boot_console.print("[green][DB] Connected to MongoDB — jarvis database ready[/green]")
    else:
        _boot_console.print("[yellow][DB] MongoDB unavailable — using local fallback[/yellow]")

    brain = Brain(config_path)
    # FIXED: startup probe so CLI banner shows a real active provider when keys work
    try:
        _probe = brain.chat([{"role": "user", "content": "reply with exactly: JARVIS_ONLINE"}])
        _ok_probe = bool(
            _probe
            and (
                "JARVIS_ONLINE" in _probe
                or (
                    "all configured AI providers failed" not in _probe
                    and "No model providers are configured" not in _probe
                )
            )
        )
        if _ok_probe and brain.get_active_provider() != "none":
            _boot_console.print(
                f"[green][OK] {brain.get_active_provider()} responding[/green]",
            )
        elif _ok_probe:
            _boot_console.print("[green][OK] AI responding[/green]")
        else:
            _boot_console.print(
                "[red][WARN] No AI provider available — check your API keys in config.yaml[/red]",
            )
    except Exception as e:
        logger.warning("Startup provider probe failed: %s", e)
        _boot_console.print(
            "[red][WARN] No AI provider available — check your API keys in config.yaml[/red]",
        )

    agents_cfg = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    agents_on = bool(agents_cfg.get("enabled", False))
    memory_agent = None
    if agents_on and bool((agents_cfg.get("memory") or {}).get("enabled", True)):
        memory_agent = MemoryAgent(
            "memory",
            brain,
            db,
            dict(agents_cfg.get("memory") or {}),
        )

    memory = Memory(config, base_dir=_ROOT, memory_agent=memory_agent)
    context = Context(config, base_dir=_ROOT)

    file_skill = FileSkill()
    shell_skill = ShellSkill()
    clipboard_skill = ClipboardSkill()
    app_skill = AppSkill(config_path, brain=brain)
    code_assistant = CodeAssistant(brain)
    search_skill = SearchSkill()
    screen_skill = ScreenSkill(str(_ROOT / str((config.get("vision") or {}).get("screenshot_dir", "screenshots"))))
    image_analyzer = ImageAnalyzer(config)
    notifier = Notifier(config, db=db)

    try:
        monitors = screen_skill.get_monitors()
        monitor_count = len([m for m in monitors if isinstance(m, dict) and "index" in m])
        print(f"\033[96m[VISION] Screen capture ready — {monitor_count} monitors detected\033[0m")
    except Exception:
        pass
    try:
        if bool((config.get("notifications") or {}).get("desktop", True)):
            print("\033[96m[NOTIFY] Desktop notifications enabled\033[0m")
    except Exception:
        pass

    try:
        (_ROOT / "screenshots").mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        icon_192 = _ROOT / "interface" / "web" / "static" / "icons" / "icon-192.png"
        if not icon_192.exists():
            generate_icons(_ROOT)
    except Exception as e:
        logger.warning("Icon generation skipped: %s", e)

    device_manager = None
    routine_manager = None
    smarthome_skill = None
    sh = config.get("smarthome") or {}
    if isinstance(sh, dict) and bool(sh.get("enabled")):
        ha_client = None
        prov = str(sh.get("provider") or "homeassistant").lower()
        ha_cfg = sh.get("homeassistant") or {}
        if prov in ("homeassistant", "both") and isinstance(ha_cfg, dict):
            url = str(ha_cfg.get("url") or "").strip()
            token = str(ha_cfg.get("token") or "").strip()
            if url and token:
                ha_client = HomeAssistantClient(url, token)

        mqtt_client = None
        mq = sh.get("mqtt") or {}
        if isinstance(mq, dict) and bool(mq.get("enabled")):
            mqtt_client = MQTTClient(
                str(mq.get("host") or "localhost"),
                int(mq.get("port") or 1883),
                username=str(mq.get("username") or "") or None,
                password=str(mq.get("password") or "") or None,
                client_id=str(mq.get("client_id") or "jarvis"),
            )
            try:
                mqtt_client.connect()
            except Exception as e:
                logger.warning("MQTT connect failed: %s", e)

        device_manager = DeviceManager(ha_client, mqtt_client, config, registry_path=_ROOT / "devices.json")
        routine_manager = RoutineManager(device_manager, config, config_path=config_path)
        smarthome_skill = SmarthomeSkill(device_manager, routine_manager, config)
        try:
            disc = device_manager.discover_devices()
            n = 0
            if isinstance(disc.get("data"), dict):
                n = len(disc["data"].get("entities") or [])
            logger.info("Smart home discover: %s entities (%s)", n, disc.get("source", "?"))
            if ha_client and disc.get("success"):
                print(f"\033[96m[SMARTHOME] Connected to Home Assistant — {n} devices found\033[0m")
            elif ha_client:
                print("\033[96m[SMARTHOME] Home Assistant configured but discover failed; check URL/token\033[0m")
            else:
                print("\033[96m[SMARTHOME] Enabled — Home Assistant URL/token not set; smart home commands will ask to configure\033[0m")
        except Exception as e:
            logger.exception("discover_devices on startup: %s", e)

    registry = ToolRegistry(
        brain,
        file_skill,
        shell_skill,
        clipboard_skill,
        app_skill,
        code_assistant,
        search_skill,
        smarthome_skill=smarthome_skill,
        orchestrator=None,
    )

    orchestrator = None
    if agents_on:
        sub: dict[str, Any] = {}
        if bool((agents_cfg.get("research") or {}).get("enabled", True)):
            sub["research"] = ResearchAgent(
                "research",
                brain,
                db,
                dict(agents_cfg.get("research") or {}),
            )
        if bool((agents_cfg.get("coder") or {}).get("enabled", True)):
            sub["coder"] = CoderAgent(
                "coder",
                brain,
                db,
                dict(agents_cfg.get("coder") or {}),
                code_assistant=code_assistant,
            )
        if bool((agents_cfg.get("planner") or {}).get("enabled", True)):
            sub["planner"] = PlannerAgent(
                "planner",
                brain,
                db,
                dict(agents_cfg.get("planner") or {}),
            )
        if memory_agent is not None:
            sub["memory"] = memory_agent
        if bool((config.get("vision") or {}).get("enabled", True)):
            sub["vision"] = VisionAgent(
                brain=brain,
                db=db,
                config=dict(config.get("vision") or {}),
                screen_skill=screen_skill,
                image_analyzer=image_analyzer,
            )
        if bool((config.get("notifications") or {}).get("enabled", True)):
            sub["notifier"] = notifier
        orch_cfg = dict(agents_cfg.get("orchestrator") or {})
        orchestrator = Orchestrator(
            brain,
            db,
            orch_cfg,
            sub,
            registry=registry,
        )
        registry.orchestrator = orchestrator

    n_agents = (len(getattr(orchestrator, "_agents", {}) or {}) + 1) if orchestrator else 0
    mem_stored = 0
    try:
        if db.available:
            mem_stored = db.count("memories", {})
    except Exception:
        mem_stored = 0
    _boot_console.print(
        f"[cyan]Agents active:[/cyan] {n_agents}  [cyan]Memories (DB):[/cyan] {mem_stored}",
    )

    listener, speaker, voice_cfg = _build_voice_hardware(config)

    wake_detector = None
    if voice_cfg.get("wake_word_enabled"):
        try:
            from skills.voice.wakeword import WakeWordDetector

            pk = (voice_cfg.get("porcupine_key") or "").strip()
            if not pk:
                pk = (os.environ.get("PORCUPINE_KEY") or "").strip()
            porcupine_key = pk or None
            wake_detector = WakeWordDetector(
                wake_word=str(voice_cfg.get("wake_word", "hey jarvis")),
                listener=listener,
                porcupine_key=porcupine_key,
            )
        except Exception as e:
            logger.exception("WakeWordDetector setup failed: %s", e)
            wake_detector = None

    cli = CLI(
        brain=brain,
        memory=memory,
        context=context,
        listener=listener,
        speaker=speaker,
        voice_config=voice_cfg,
        wake_detector=wake_detector,
        registry=registry,
    )

    web_iface = None
    web_cfg = config.get("web") or {}
    if bool(web_cfg.get("enabled")):
        try:
            from interface.web.app import WebInterface

            host = str(web_cfg.get("host", "0.0.0.0"))
            port = int(web_cfg.get("port", 5000))
            debug = bool(web_cfg.get("debug", False))
            web_iface = WebInterface(
                brain=brain,
                memory=memory,
                registry=registry,
                speaker=speaker,
                listener=listener,
                config=config,
                context=context,
                device_manager=device_manager,
                routine_manager=routine_manager,
                notifier=notifier,
            )
            web_iface.start(host=host, port=port, debug=debug)
            notifier.set_socketio(web_iface.socketio)
            print(f"\033[96m[WEB] Interface running at http://localhost:{port}\033[0m")
            if bool(web_cfg.get("open_browser", True)):

                def _open_browser() -> None:
                    webbrowser.open(f"http://localhost:{port}")

                threading.Timer(1.5, _open_browser).start()
        except Exception as e:
            logger.exception("Web interface failed to start: %s", e)

    if wake_detector is not None:
        try:
            wake_detector.start(cli.handle_wake_event)
        except Exception as e:
            logger.exception("Could not start wake-word detector: %s", e)

    try:
        cli.run()
    except KeyboardInterrupt:
        print("\nGoodbye, Sir.")
    finally:
        if web_iface is not None:
            try:
                web_iface.stop()
            except Exception as e:
                logger.warning("Web interface stop: %s", e)
        cli.shutdown_voice()
        gc.collect()


# To verify fixes, test these inputs after restart:
# 1. "what is the latest news today"  → should show real news headlines
# 2. "make a plan to learn ML in 30 days"  → should give structured plan, no code tool
# 3. "write python code to add 2 numbers"  → should give clean code output
# 4. python -m core.db  → should show actual MongoDB error reason


if __name__ == "__main__":
    main()
