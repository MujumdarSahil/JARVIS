# JARVIS

**Just A Rather Very Intelligent System** — a local AI assistant with a terminal CLI, optional web HUD, tool routing (files, shell, code, search, smart home), and optional voice I/O.

Repository: [github.com/MujumdarSahil/JARVIS](https://github.com/MujumdarSahil/JARVIS)

---

## Features

| Area | What it does |
|------|----------------|
| **Chat & routing** | LLM-powered replies with a JSON tool router (code, search, files, shell, apps, clipboard, smart home). |
| **Providers** | Primary model plus fallbacks: Groq, Gemini, OpenRouter, Ollama (OpenAI-compatible APIs). |
| **CLI** | Rich-based terminal UI, slash commands (`/help`, `/run`, `/search`, …), optional markdown stripping for plain text. |
| **Web UI** | Flask + Socket.IO HUD: chat, system stats, file browser, smart home panel (when enabled). |
| **Code assistant** | Generate, explain, debug, review, convert, run snippets via the shared brain. |
| **Search** | DuckDuckGo-backed `web_search` skill. |
| **System** | File ops, shell commands, clipboard, app launcher (aliases in config). |
| **Smart home** (optional) | Home Assistant REST client, MQTT, natural-language device control, routines, web API + live updates. |
| **Voice** (optional) | Faster-Whisper STT, Edge TTS / pyttsx3, optional Porcupine wake word. |
| **Learning layer** | Usage analytics, recurring pattern discovery, proactive suggestions, weekly self-study reports. |
| **Identity layer** | Jarvis self-model, relationship tracking, trust/rapport milestones, personalized startup greetings. |

---

## Requirements

### System

- **OS**: Windows, macOS, or Linux (paths in `config.example.yaml` show Windows-style app aliases; adjust for your OS).
- **Python**: **3.10+** recommended (3.11 or 3.12 ideal). Must match wheels for `faster-whisper`, `sounddevice`, etc., on your platform.
- **Network**: Required for cloud LLMs and web search; Ollama can run fully local.
- **Hardware** (optional): Microphone for voice input; GPU helps Whisper but CPU works.

### Python packages

Install everything from the app directory:

```bash
cd jarvis
pip install -r requirements.txt
```

| Package | Purpose |
|---------|---------|
| `openai` | OpenAI-compatible clients (Groq, OpenRouter, Ollama, etc.). |
| `google-generativeai` | Google Gemini provider. |
| `duckduckgo-search` | Web search skill. |
| `rich` | Terminal formatting and CLI UX. |
| `pyyaml` | `config.yaml` loading. |
| `requests` | HTTP (e.g. Home Assistant). |
| `faster-whisper`, `sounddevice`, `soundfile`, `numpy` | Speech-to-text and audio capture. |
| `edge-tts`, `pyttsx3`, `pygame` | Text-to-speech and playback. |
| `pvporcupine` | Optional wake word. |
| `psutil` | System metrics (`/sysinfo`, web stats). |
| `pyperclip`, `send2trash` | Clipboard and safer file delete. |
| `flask`, `flask-socketio`, `flask-cors`, `python-socketio`, `python-engineio` | Web UI + realtime. |
| `paho-mqtt`, `schedule` | Smart home MQTT and scheduled routines. |

> **Tip**: Use a virtual environment: `python -m venv .venv` then activate it before `pip install`.

---

## Quick start

### 1. Clone and enter the app folder

```bash
git clone https://github.com/MujumdarSahil/JARVIS.git
cd JARVIS/jarvis
```

### 2. Configuration (required)

`config.yaml` is **gitignored** so secrets are not committed.

```bash
copy config.example.yaml config.yaml    # Windows
# cp config.example.yaml config.yaml    # macOS / Linux
```

Edit **`jarvis/config.yaml`**:

1. **`models.primary`** — e.g. `groq`, `gemini`, `openrouter`, `ollama`.
2. **`models.providers.*.api_key`** — add keys for each provider you use (Groq, Google AI, OpenRouter; Ollama often uses a placeholder key).
3. **`models.providers.*.base_url` / `model`** — match the provider docs.

Optional:

- **`yamlweb.enabled: true`** — start the web interface (default port **5000**).
- **`yamlvoice`** — enable mic / TTS / wake word as needed.
- **`yamlsmarthome`** — Home Assistant URL + long-lived token; MQTT if used.

### 3. Run

```bash
python main.py
```

- **CLI**: type messages at `[You] >`. Use **`/help`** for slash commands.
- **Web**: with `yamlweb.enabled: true`, open **`http://localhost:5000`** (or your host/port).

---

## Configuration reference (summary)

Config uses legacy **`yaml*`** top-level keys that the loader maps to canonical names (e.g. `yamlweb` → `web`).

| Section | Role |
|---------|------|
| `interface` | e.g. `markdown_in_terminal` — if `true`, CLI does not strip markdown from replies. |
| `yamljarvis` | Display name, wake phrase hints. |
| `yamlweb` | Web server `enabled`, `host`, `port`, `debug`, `open_browser`. |
| `models` | `primary`, `fallback[]`, `providers` with `api_key`, `model`, `base_url`. |
| `memory` | History length, persistence, memory file name. |
| `yamlvoice` | STT/TTS engines, wake word, Porcupine key. |
| `skills` | Toggle `web_search`, `file_control`, `shell_control`, `clipboard`, `app_launcher`, `code_assistant`, `smarthome`. |
| `yamlsmarthome` | HA URL/token, MQTT, room aliases, routines. |
| `apps.aliases` | Short names → executables for `open_app`. |

Full template: **`jarvis/config.example.yaml`**.

---

## Smart home (optional)

1. Set **`yamlsmarthome.enabled: true`** in `config.yaml`.
2. Fill **`homeassistant.url`** and **`homeassistant.token`** (HA profile → long-lived access tokens).
3. Optionally enable **`mqtt`** for broker integration.
4. Restart Jarvis. The web UI **house** panel lists rooms/devices when HA is reachable.

Device control from chat uses regex-based parsing (no LLM inside the control path for speed). A cached registry is stored locally as **`devices.json`** (gitignored by default).

---

## Voice (optional)

Enabling **`yamlvoice.enabled`** (and related flags) pulls in Whisper, audio devices, and TTS. On Windows you may need working microphone drivers and, for wake word, a **Porcupine** key in config or `PORCUPINE_KEY` env var.

If you only use text chat, leave voice disabled to avoid loading heavy STT models.

---

## Project layout

```
JARVIS/
├── README.md                 # This file
├── .gitignore
└── jarvis/
    ├── main.py               # Entry point
    ├── config.yaml           # Local only — create from config.example.yaml
    ├── config.example.yaml   # Template (safe to commit)
    ├── requirements.txt
    ├── core/                 # Brain, context, memory
    │   ├── learning/         # Usage analyzer, patterns, suggestions, weekly learner
    │   └── identity/         # Jarvis self-model and relationship tracker
    ├── interface/            # CLI + web (Flask / Socket.IO)
    ├── skills/               # Code, search, system, voice, smarthome, tools/registry
    └── utils/                # Logging
```

Run all commands from **`jarvis/`** so imports resolve correctly.

---

## Intelligence Layer

Jarvis now includes a final intelligence layer with:

- `core/learning/usage_analyzer.py`: usage heatmaps, top requests, tool success rates, session patterns, user vocabulary.
- `core/learning/pattern_engine.py`: time-based, sequence, preference, frustration, and success patterns (minimum 5 data points).
- `core/learning/suggestion_engine.py`: proactive suggestions with strict anti-spam limits (max/session + cooldown).
- `core/learning/weekly_learner.py`: weekly background self-study loop and long-term improvement trajectory.
- `core/identity/jarvis_self.py`: Jarvis self-description, strengths/weaknesses, achievements, and time-aware greetings.
- `core/identity/relationship.py`: trust score EMA, rapport level, communication style, streaks, milestones.

### New CLI commands

- `/me`, `/patterns`, `/suggestions`, `/weekly`, `/trajectory`, `/milestone`

### New web/API capabilities

- `/api/identity`, `/api/relationship`, `/api/patterns`, `/api/suggestions`, `/api/weekly-report`, `/api/trajectory`
- Socket event `proactive_suggestion` for floating suggestion cards with accept/dismiss actions.

---

## Security

- **Never commit `config.yaml`** — it contains API keys.
- Rotate keys if they were ever shared or committed by mistake.
- Smart home and shell skills can affect real devices and the OS; use on trusted networks and review **`skills`** toggles.

---

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| `Active provider: none` / probe warning | Keys, network, quotas; try switching `models.primary` or fixing `base_url`. |
| Web UI won’t start | Port in use? Firewall? `yamlweb.enabled` true? |
| Voice import errors | Install optional deps; GPU/CPU Whisper model download on first run. |
| HA / smart home | URL reachable from this machine, valid token, `yamlsmarthome.enabled`. |

Logs: **`jarvis.log`** (or as configured in `utils/logger.py`) for router JSON and provider messages.

---

## License

Add a `LICENSE` file to the repo if you want to specify terms; until then, all rights reserved unless you state otherwise.

---

## Author

**MujumdarSahil** — [github.com/MujumdarSahil](https://github.com/MujumdarSahil)
