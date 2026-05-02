# Jarvis Plugin Guide

## Plugin vs Skill vs Agent
- A **plugin** is an extension module you drop into `plugins/custom/` to add commands or keyword behaviors quickly.
- A **skill** is a built-in capability package (files, shell, search, etc.) used by core routing.
- An **agent** is a higher-level orchestrated component that plans or executes complex tasks across skills/tools.

## Quick Start
1. Copy `utils/plugin_template/my_plugin.py` to `plugins/custom/my_plugin.py`.
2. Rename class + `name` field to something unique.
3. Implement `execute()` with your logic.
4. Add optional startup code in `on_load()`.
5. Restart Jarvis (or use hot reload command).

## Plugin Runtime Resources
Every plugin receives:
- `self.brain`: call LLMs using the same configured providers.
- `self.db`: MongoDB wrapper when available.
- `self.config`: full Jarvis config dict.
- `self.notifier`: send desktop/web (and optionally Telegram) notifications.

## LLM Calls From Plugin
Use `self.brain.chat()` with OpenAI-style messages:

```python
response = self.brain.chat([
    {"role": "system", "content": "You are concise."},
    {"role": "user", "content": "Summarize this text: ..."},
])
```

## Persisting Data
- Preferred: `self.db.insert(...)`, `self.db.find(...)` if `self.db.available` is true.
- Fallback: local JSON file under the `jarvis/` directory.

## Config Keys
Add plugin settings in `config.yaml`, for example:

```yaml
plugins:
  enabled: true
  disabled: []
my_plugin:
  api_key: "..."
```

Then read via `self.config.get("my_plugin", {})`.

## Best Practices
- Wrap `execute()` in `try/except`.
- Return `self._success(...)` and `self._error(...)` consistently.
- Keep operations non-blocking where possible.
- Respect plugin timeout (10s in registry).

## Weather Plugin Walkthrough
- Command `/weather Mumbai` triggers `WeatherPlugin.execute`.
- Plugin geocodes city with Open-Meteo geocoder.
- Then fetches current weather and formats user-friendly response.
- `/forecast` uses `get_forecast()` for multi-day outlook.
- On any failure, plugin returns `self._error(str(e))`.

## Hot Reload
- Use `/plugin reload <name>` in CLI.
- This reloads the plugin module, rebuilds command + keyword maps, and keeps Jarvis running.
