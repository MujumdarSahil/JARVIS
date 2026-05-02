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

from core.agents import (
    AutonomousAgent,
    BrowserAgentSkill,
    CoderAgent,
    EmotionAgent,
    FinanceAgent,
    GithubAgent,
    GmailAgent,
    MemoryAgent,
    Orchestrator,
    PlannerAgent,
    ResearchAgent,
    SelfImprovementAgent,
    VisionAgent,
)
from core.brain import Brain
from core.context import Context
from core.db import db
from core.identity import JarvisSelf, RelationshipTracker
from core.learning import PatternEngine, SuggestionEngine, UsageAnalyzer, WeeklyLearner
from core.memory import Memory
from core.emotion import MoodTracker, SentimentAnalyzer, ToneAdapter
from core.improvement import LessonStore, PromptOptimizer, ResponseEvaluator
from core.autonomy import ActivityReporter, ProactiveMonitor, TaskScheduler
from core.knowledge.personal_kb import PersonalKB
from interface.cli import CLI
from plugins import PluginLoader, PluginRegistry
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


def _debug_log(location: str, message: str, data: dict[str, Any], run_id: str, hypothesis_id: str) -> None:
    try:
        import json
        import time as _t

        payload = {
            "sessionId": "92b104",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(_t.time() * 1000),
        }
        with open("debug-92b104.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
    identity_cfg = config.get("identity") or {}
    learning_cfg = config.get("learning") or {}
    jarvis_self = JarvisSelf(db, None, config)
    jarvis_self.load()
    if bool(identity_cfg.get("show_startup_greeting", True)):
        _boot_console.print(f"[bold cyan]{jarvis_self.get_startup_greeting()}[/bold cyan]")

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
    jarvis_self.brain = brain
    jarvis_self.update_stats_async()
    relationship = RelationshipTracker(db, jarvis_self)
    relationship.record_interaction(0, "neutral", 1.0)
    if bool(identity_cfg.get("milestone_notifications", True)):
        for m in relationship.check_milestones():
            _boot_console.print(f"[bold yellow]{relationship.get_milestone_message(m)}[/bold yellow]")
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

    # ── Personal Knowledge Base ──────────────────────────────────────────────
    personal_kb = None
    kb_cfg = config.get("personal_kb") or {}
    if bool(kb_cfg.get("enabled", True)):
        personal_kb = PersonalKB(db, brain)
        n_contacts = len(personal_kb.get("contacts"))
        n_goals = len(personal_kb.get("goals"))
        n_prefs = len(personal_kb.get("preferences"))
        _boot_console.print(
            f"[cyan][KB][/cyan] Personal profile loaded — "
            f"{n_contacts} contacts, {n_goals} goals, {n_prefs} preferences"
        )

    context = Context(
        config,
        base_dir=_ROOT,
        personal_kb=personal_kb if bool(kb_cfg.get("inject_into_prompt", True)) else None,
        jarvis_self=jarvis_self,
        relationship=relationship,
    )

    file_skill = FileSkill()
    shell_skill = ShellSkill()
    clipboard_skill = ClipboardSkill()
    app_skill = AppSkill(config_path, brain=brain)
    code_assistant = CodeAssistant(brain)
    search_skill = SearchSkill()
    screen_skill = ScreenSkill(str(_ROOT / str((config.get("vision") or {}).get("screenshot_dir", "screenshots"))))
    image_analyzer = ImageAnalyzer(config)
    notifier = Notifier(config, db=db)
    telegram_channel = None
    api_server = None
    webhook_receiver = None

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

    # ── Gmail / Calendar ────────────────────────────────────────────────────
    gmail_client = None
    gmail_composer = None
    gmail_agent = None
    gmail_cfg = config.get("gmail") or {}
    if bool(gmail_cfg.get("enabled", False)):
        try:
            from skills.gmail.client import GmailClient, GmailComposer
            gmail_client = GmailClient(
                credentials_path=str(gmail_cfg.get("credentials_path", "gmail_credentials.json")),
                token_path=str(gmail_cfg.get("token_path", "gmail_token.json")),
            )
            if gmail_client.authenticate():
                gmail_composer = GmailComposer(brain)
                gmail_agent = GmailAgent(brain, db, dict(gmail_cfg), gmail_client, gmail_composer)
                inbox = gmail_client.get_inbox(max_results=1)
                n_unread = len(gmail_client.get_inbox(max_results=50))
                _boot_console.print(f"[cyan][GMAIL][/cyan] Connected — {n_unread} unread emails")
            else:
                _boot_console.print("[yellow][GMAIL][/yellow] Auth failed — check gmail_credentials.json")
                gmail_client = None
        except Exception as e:
            logger.warning("Gmail init failed: %s", e)
            _boot_console.print(f"[yellow][GMAIL][/yellow] Disabled — {e}")
    else:
        _boot_console.print("[dim][GMAIL][/dim] Disabled — add gmail_credentials.json and set gmail.enabled: true")

    # ── Browser / Playwright ────────────────────────────────────────────────
    browser_agent_skill = None
    browser_agent_raw = None
    web_scraper = None
    browser_cfg = config.get("browser") or {}
    if bool(browser_cfg.get("enabled", True)):
        try:
            from skills.browser.agent import BrowserAgent as _BrowserAgent
            from skills.browser.scraper import WebScraper
            browser_agent_raw = _BrowserAgent(
                headless=bool(browser_cfg.get("headless", True)),
                timeout_seconds=int(browser_cfg.get("timeout_seconds", 30)),
            )
            web_scraper = WebScraper(browser_agent_raw, brain)
            browser_agent_skill = BrowserAgentSkill(
                brain, db, dict(browser_cfg), browser_agent_raw, web_scraper
            )
            mode = "headless" if browser_cfg.get("headless", True) else "visible"
            _boot_console.print(f"[cyan][BROWSER][/cyan] Chromium ready ({mode})")
        except Exception as e:
            logger.warning("Browser init failed: %s", e)
            _boot_console.print(f"[yellow][BROWSER][/yellow] Disabled — {e} (run: playwright install chromium)")

    # ── GitHub ──────────────────────────────────────────────────────────────
    github_agent = None
    gh_cfg = config.get("github") or {}
    gh_token = str(gh_cfg.get("token") or "").strip()
    if bool(gh_cfg.get("enabled", False)) and gh_token:
        try:
            from skills.github.client import GitHubClient
            gh_client = GitHubClient(gh_token)
            repos = gh_client.list_repos()
            github_agent = GithubAgent(brain, db, dict(gh_cfg), gh_client)
            _boot_console.print(f"[cyan][GITHUB][/cyan] Connected — {len(repos)} repos")
        except Exception as e:
            logger.warning("GitHub init failed: %s", e)
            _boot_console.print(f"[yellow][GITHUB][/yellow] Disabled — {e}")
    else:
        _boot_console.print("[dim][GITHUB][/dim] Disabled — set github.enabled: true and github.token")

    # ── Finance ─────────────────────────────────────────────────────────────
    finance_agent = None
    fin_cfg = config.get("finance") or {}
    if bool(fin_cfg.get("enabled", True)):
        try:
            from skills.finance.markets import MarketTracker
            from skills.finance.expenses import ExpenseTracker
            market_tracker = MarketTracker()
            expense_tracker = ExpenseTracker(db, dict(fin_cfg))
            finance_agent = FinanceAgent(brain, db, config, market_tracker, expense_tracker)
            _boot_console.print("[cyan][FINANCE][/cyan] Markets ready — fetching prices on first request")
        except Exception as e:
            logger.warning("Finance init failed: %s", e)
            _boot_console.print(f"[yellow][FINANCE][/yellow] Disabled — {e} (pip install yfinance)")

    custom_plugins_dir = _ROOT / "plugins" / "custom"
    custom_plugins_dir.mkdir(parents=True, exist_ok=True)
    plugin_loader = PluginLoader(brain=brain, db=db, config=config, notifier=notifier)
    plugin_registry = PluginRegistry(plugin_loader)
    plugin_count = plugin_registry.initialize()

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
        emotion_agent=None,
        self_improvement_agent=None,
        lesson_store=None,
        prompt_optimizer=None,
        autonomous_agent=None,
        autonomy_reporter=None,
        finance_agent=finance_agent,
        personal_kb=personal_kb,
        plugin_registry=plugin_registry,
        notifier=notifier,
    )

    emotion_cfg = config.get("emotion") or {}
    improve_cfg = config.get("self_improvement") or {}
    autonomy_cfg = config.get("autonomy") or {}

    sentiment = SentimentAnalyzer()
    mood = MoodTracker(db)
    tone = ToneAdapter()
    emotion_agent = EmotionAgent(brain, db, emotion_cfg, sentiment, mood, tone) if bool(emotion_cfg.get("enabled", True)) else None

    evaluator = ResponseEvaluator()
    lesson_store = LessonStore(db)
    optimizer = PromptOptimizer(brain, lesson_store)
    self_improvement_agent = (
        SelfImprovementAgent(brain, db, improve_cfg, evaluator, lesson_store, optimizer)
        if bool(improve_cfg.get("enabled", True))
        else None
    )

    reporter = ActivityReporter(db, notifier=notifier)
    scheduler = TaskScheduler(db, registry, notifier, reporter=reporter, config=autonomy_cfg) if bool(autonomy_cfg.get("enabled", True)) else None
    monitor = (
        ProactiveMonitor(db, registry, notifier, shell_skill, reporter=reporter)
        if bool(autonomy_cfg.get("enabled", True))
        else None
    )
    autonomous_agent = (
        AutonomousAgent(brain, db, autonomy_cfg, scheduler, monitor, reporter)
        if bool(autonomy_cfg.get("enabled", True)) and scheduler is not None and monitor is not None
        else None
    )

    registry.emotion_agent = emotion_agent
    registry.self_improvement_agent = self_improvement_agent
    registry.lesson_store = lesson_store
    registry.prompt_optimizer = optimizer
    registry.autonomous_agent = autonomous_agent
    registry.autonomy_reporter = reporter
    # #region agent log
    _debug_log(
        "main.py:agent_init",
        "self-aware systems initialized",
        {
            "emotion_enabled": emotion_agent is not None,
            "self_improvement_enabled": self_improvement_agent is not None,
            "autonomy_enabled": autonomous_agent is not None,
            "scheduler_enabled": scheduler is not None,
            "monitor_enabled": monitor is not None,
        },
        "run1",
        "H3",
    )
    # #endregion

    usage_analyzer = UsageAnalyzer(db, registry)
    pattern_engine = PatternEngine(usage_analyzer, db)
    suggestion_engine = SuggestionEngine(
        pattern_engine,
        personal_kb,
        db,
        brain,
        config=learning_cfg,
    )
    weekly_learner = WeeklyLearner(
        brain,
        db,
        usage_analyzer,
        pattern_engine,
        suggestion_engine,
        lesson_store,
        optimizer,
        personal_kb,
    )
    registry.usage_analyzer = usage_analyzer
    registry.pattern_engine = pattern_engine
    registry.suggestion_engine = suggestion_engine
    registry.weekly_learner = weekly_learner

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
        if emotion_agent is not None:
            sub["emotion"] = emotion_agent
        if self_improvement_agent is not None:
            sub["self_improvement"] = self_improvement_agent
        if autonomous_agent is not None:
            sub["autonomous"] = autonomous_agent
        # Phase 2 agents
        if gmail_agent is not None:
            sub["gmail"] = gmail_agent
        if browser_agent_skill is not None:
            sub["browser"] = browser_agent_skill
        if github_agent is not None:
            sub["github"] = github_agent
        if finance_agent is not None:
            sub["finance"] = finance_agent
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
    if emotion_agent is not None:
        _boot_console.print(f"[cyan][EMOTION][/cyan] Mood tracking active — current mood: {mood.get_current_mood()}")
    if self_improvement_agent is not None:
        stats = self_improvement_agent.get_performance_stats()
        lessons = len(lesson_store.get_top_failures(50))
        _boot_console.print(
            f"[cyan][IMPROVE][/cyan] Self-improvement active — avg score: {stats.get('avg_score_all_time')} | lessons: {lessons}",
        )
    if scheduler is not None:
        _boot_console.print(f"[cyan][AUTONOMY][/cyan] Scheduler active — {len(scheduler.list_tasks())} tasks scheduled")
    if monitor is not None:
        _boot_console.print(f"[cyan][AUTONOMY][/cyan] Monitor active — {len(monitor.list_monitors())} conditions watching")

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
        plugin_registry=plugin_registry,
        suggestion_engine=suggestion_engine,
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
                suggestion_engine=suggestion_engine,
                jarvis_self=jarvis_self,
                relationship=relationship,
                pattern_engine=pattern_engine,
                weekly_learner=weekly_learner,
            )
            web_iface.start(host=host, port=port, debug=debug)
            if bool((config.get("webhooks") or {}).get("enabled", False)):
                from channels.webhook import WebhookReceiver

                webhook_receiver = WebhookReceiver(web_iface.app, registry=registry, notifier=notifier, config=config, db=db)
                webhook_receiver.register_routes()
            notifier.set_socketio(web_iface.socketio)
            print(f"\033[96m[WEB] Interface running at http://localhost:{port}\033[0m")
            if bool(web_cfg.get("open_browser", True)):

                def _open_browser() -> None:
                    webbrowser.open(f"http://localhost:{port}")

                threading.Timer(1.5, _open_browser).start()
        except Exception as e:
            logger.exception("Web interface failed to start: %s", e)

    tg_cfg = config.get("telegram") or {}
    if bool(tg_cfg.get("enabled", False)) and str(tg_cfg.get("token") or "").strip():
        try:
            from channels.telegram_bot import TelegramChannel

            telegram_channel = TelegramChannel(
                token=str(tg_cfg.get("token")),
                registry=registry,
                brain=brain,
                memory=memory,
                notifier=notifier,
                allowed_user_ids=list(tg_cfg.get("allowed_user_ids") or []),
            )
            telegram_channel.start()
            if bool(tg_cfg.get("send_notifications", True)):
                notifier.register_channel_callback(telegram_channel.send_notification_sync)
            print("\033[96m[TELEGRAM] Bot active\033[0m")
        except Exception as e:
            logger.warning("Telegram channel failed: %s", e)
    else:
        print("\033[90m[TELEGRAM] disabled\033[0m")

    api_cfg = config.get("api_server") or {}
    if bool(api_cfg.get("enabled", False)):
        try:
            from channels.rest_api import JarvisAPIServer

            api_server = JarvisAPIServer(
                registry=registry,
                brain=brain,
                memory=memory,
                plugin_registry=plugin_registry,
                config=config,
            )
            api_host = str(api_cfg.get("host") or "0.0.0.0")
            api_port = int(api_cfg.get("port") or 8000)
            api_server.start(host=api_host, port=api_port)
            print(f"\033[96m[API] REST API running at http://{api_host}:{api_port}\033[0m")
        except Exception as e:
            logger.warning("API server failed: %s", e)
    else:
        print("\033[90m[API] disabled\033[0m")

    try:
        loaded_names = ", ".join([p["name"] for p in plugin_registry.list_plugins()]) or "none"
        print(f"\033[96m[PLUGINS] {plugin_count} plugins loaded: {loaded_names}\033[0m")
    except Exception:
        pass

    if wake_detector is not None:
        try:
            wake_detector.start(cli.handle_wake_event)
        except Exception as e:
            logger.exception("Could not start wake-word detector: %s", e)

    if scheduler is not None:
        scheduler.start()
        weekly_learner.scheduler = scheduler
        if bool(learning_cfg.get("weekly_study", True)):
            weekly_learner.schedule_weekly_study()
    if monitor is not None:
        monitor.start()

    try:
        loaded_prompts = optimizer.load_optimized_prompts()
        if loaded_prompts:
            _boot_console.print("[cyan][IMPROVE][/cyan] Loaded optimized prompts from MongoDB")
    except Exception:
        pass

    try:
        if bool((autonomy_cfg or {}).get("morning_brief", True)):
            now = __import__("datetime").datetime.now()
            hhmm = str((autonomy_cfg or {}).get("brief_time", "08:00"))
            h, m = [int(x) for x in hhmm.split(":")]
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            delta = abs((now - target).total_seconds())
            if delta <= 1800:
                _boot_console.print(f"[cyan][AUTONOMY][/cyan] Morning brief: {reporter.generate_morning_brief()}")
    except Exception:
        pass

    try:
        if bool(learning_cfg.get("enabled", True)):
            found = pattern_engine.find_patterns()
            _boot_console.print(f"[cyan][LEARNING][/cyan] {len(found)} patterns discovered - usage analysis active")
        if bool(identity_cfg.get("enabled", True)):
            _boot_console.print(
                f"[cyan][IDENTITY][/cyan] Trust level: {relationship.get_rapport_level()} | Streak: {relationship.relationship_data.get('streak_days', 0)} days"
            )
        cli.run()
    except KeyboardInterrupt:
        print("\nGoodbye, Sir.")
    finally:
        if scheduler is not None:
            scheduler.stop()
        if monitor is not None:
            monitor.stop()
        if web_iface is not None:
            try:
                web_iface.stop()
            except Exception as e:
                logger.warning("Web interface stop: %s", e)
        if telegram_channel is not None:
            try:
                telegram_channel.stop()
            except Exception as e:
                logger.warning("Telegram stop: %s", e)
        if api_server is not None:
            try:
                api_server.stop()
            except Exception as e:
                logger.warning("API stop: %s", e)
        cli.shutdown_voice()
        try:
            session_message_count = len(memory.get_history())
        except Exception:
            session_message_count = 0
        final_mood = "neutral"
        try:
            if emotion_agent is not None:
                final_mood = mood.get_current_mood()
        except Exception:
            pass
        success_rate = 1.0
        try:
            perf = usage_analyzer.get_tool_performance()
            vals = [float(v.get("success_rate", 0.0)) for v in perf.values() if isinstance(v, dict)]
            success_rate = sum(vals) / len(vals) if vals else 1.0
        except Exception:
            pass
        relationship.record_interaction(session_message_count, final_mood, success_rate)
        jarvis_self.save()
        gc.collect()


# To verify fixes, test these inputs after restart:
# 1. "what is the latest news today"  → should show real news headlines
# 2. "make a plan to learn ML in 30 days"  → should give structured plan, no code tool
# 3. "write python code to add 2 numbers"  → should give clean code output
# 4. python -m core.db  → should show actual MongoDB error reason


if __name__ == "__main__":
    main()
