"""
LLM router: tries the primary model provider, then fallbacks, with logging.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from rich.console import Console

from utils.logger import get_logger

logger = get_logger(__name__)


class Brain:
    """
    Loads configuration and routes chat completions through OpenAI-compatible
    endpoints or the Gemini SDK, falling back on errors or missing keys.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        base = Path(__file__).resolve().parent.parent
        self._config_path = Path(config_path) if config_path else base / "config.yaml"
        self._config: dict[str, Any] = {}
        self._active_provider: str = "none"
        # FIXED: provider fallback visibility + get_status() counters
        self._console = Console(highlight=False, stderr=False)
        self._total_calls: int = 0
        self._failed_calls: int = 0
        self._last_providers_tried: list[str] = []
        self._last_error: str | None = None
        self.reload_config()

    def reload_config(self) -> None:
        """Reload YAML from disk into ``self._config``."""
        try:
            text = self._config_path.read_text(encoding="utf-8")
            self._config = yaml.safe_load(text) or {}
        except (OSError, yaml.YAMLError) as e:
            logger.error("Failed to load config %s: %s", self._config_path, e)
            self._config = {}

    def get_active_provider(self) -> str:
        """Name of the last provider that returned a successful reply."""
        return self._active_provider

    # FIXED: expose routing health for UI / diagnostics
    def get_status(self) -> dict[str, Any]:
        return {
            "active_provider": self._active_provider,
            "providers_tried": list(self._last_providers_tried),
            "last_error": self._last_error,
            "total_calls": self._total_calls,
            "failed_calls": self._failed_calls,
        }

    def get_configured_providers(self) -> list[str]:
        """Ordered list of provider names (primary first, then fallbacks, deduplicated)."""
        return self._provider_chain()

    def _provider_chain(self) -> list[str]:
        models = self._config.get("models") or {}
        primary = (models.get("primary") or "groq").strip().lower()
        fallbacks = models.get("fallback") or []
        if not isinstance(fallbacks, list):
            fallbacks = []
        chain = [primary] + [str(p).strip().lower() for p in fallbacks]
        seen: set[str] = set()
        ordered: list[str] = []
        for p in chain:
            if p and p not in seen:
                seen.add(p)
                ordered.append(p)
        return ordered

    def _get_provider_config(self, name: str) -> dict[str, Any]:
        models = self._config.get("models") or {}
        providers = models.get("providers") or {}
        cfg = providers.get(name)
        return cfg if isinstance(cfg, dict) else {}

    def _openai_compatible_chat(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
    ) -> tuple[str | None, str | None]:
        prov = self._get_provider_config(provider_name)
        api_key = (prov.get("api_key") or "").strip()
        base_url = (prov.get("base_url") or "").strip()
        model = (prov.get("model") or "").strip()

        if not model:
            logger.warning("Provider %s: missing model id", provider_name)
            return None, "missing model id"

        # Ollama and some local servers accept a placeholder key.
        if provider_name == "ollama" and not api_key:
            api_key = "ollama"

        if not api_key:
            logger.warning("Provider %s: no API key configured; skipping", provider_name)
            return None, "no API key"

        if not base_url:
            logger.warning("Provider %s: missing base_url", provider_name)
            return None, "missing base_url"

        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
            )
            choice = response.choices[0].message
            content = (choice.content or "").strip()
            return (content if content else None), None
        except RateLimitError as e:
            logger.warning("Provider %s rate limited: %s", provider_name, e)
            return None, f"{type(e).__name__}: {e}"
        except (APIConnectionError, APITimeoutError) as e:
            logger.warning("Provider %s connection error: %s", provider_name, e)
            return None, f"{type(e).__name__}: {e}"
        except APIStatusError as e:
            logger.warning("Provider %s API error: %s", provider_name, e)
            return None, f"{type(e).__name__}: {e}"
        except Exception as e:
            logger.exception("Provider %s unexpected error: %s", provider_name, e)
            return None, f"{type(e).__name__}: {e}"

    def _gemini_chat(self, provider_name: str, messages: list[dict[str, str]]) -> tuple[str | None, str | None]:
        prov = self._get_provider_config(provider_name)
        api_key = (prov.get("api_key") or "").strip()
        model_name = (prov.get("model") or "").strip()

        if not api_key:
            logger.warning("Provider %s: no API key configured; skipping", provider_name)
            return None, "no API key"
        if not model_name:
            logger.warning("Provider %s: missing model id", provider_name)
            return None, "missing model id"

        system_chunks: list[str] = []
        conv: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            content = (m.get("content") or "").strip()
            if role == "system":
                if content:
                    system_chunks.append(content)
            elif role == "user":
                conv.append({"role": "user", "parts": [content]})
            elif role == "assistant":
                conv.append({"role": "model", "parts": [content]})

        system_instruction = "\n\n".join(system_chunks) if system_chunks else None

        try:
            # Lazy import so OpenAI-only setups avoid google.generativeai deprecation noise at startup.
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name, system_instruction=system_instruction)

            if not conv:
                return None, "no user messages"

            last = conv[-1]
            history = conv[:-1]

            if last.get("role") != "user":
                logger.warning("Gemini: last message must be user; got %s", last.get("role"))
                return None, "invalid message order"

            # Normalize history: Gemini expects strict user/model alternation starting with user.
            chat = model.start_chat(history=history)
            user_text = last["parts"][0] if last.get("parts") else ""
            response = chat.send_message(user_text)
            text = (response.text or "").strip() if response else ""
            return (text if text else None), None
        except Exception as e:
            logger.warning("Provider %s error: %s", provider_name, e)
            return None, f"{type(e).__name__}: {e}"

    def chat(self, messages: list[dict[str, str]]) -> str:
        """
        Send ``messages`` to the first working provider in the configured chain.

        ``messages`` must be OpenAI-style dicts with ``role`` and ``content``.
        """
        self._total_calls += 1
        tried: list[str] = []

        if not messages:
            self._active_provider = "none"
            self._last_providers_tried = []
            self._last_error = "empty messages"
            return "I did not receive any messages to process, Sir."

        chain = self._provider_chain()
        if not chain:
            self._active_provider = "none"
            self._last_providers_tried = []
            self._last_error = "no providers configured"
            return (
                "No model providers are configured. Please set primary and providers in config.yaml."
            )

        had_failure = False
        last_err: str | None = None

        # FIXED: console visibility when falling back across the provider chain
        for i, name in enumerate(chain):
            tried.append(name)
            if name == "gemini":
                result, err = self._gemini_chat(name, messages)
            else:
                result, err = self._openai_compatible_chat(name, messages)

            if result:
                self._active_provider = name
                self._last_providers_tried = tried
                self._last_error = None
                if had_failure:
                    self._console.print(
                        f"[green][BRAIN] Connected via {name}[/green]",
                    )
                return result

            self._failed_calls += 1
            had_failure = True
            last_err = err or "no response"
            self._last_error = last_err
            nxt = chain[i + 1] if i + 1 < len(chain) else None
            if nxt:
                self._console.print(
                    f"[yellow][BRAIN] {name} failed ({last_err}), trying {nxt}...[/yellow]",
                )

        self._active_provider = "none"
        self._last_providers_tried = tried
        self._last_error = last_err
        return (
            "I apologize - all configured AI providers failed or are unavailable "
            "(missing keys, rate limits, or network issues). Please check your config and try again."
        )

    def set_primary_provider(self, name: str) -> tuple[bool, str]:
        """
        Set ``models.primary`` to a configured provider name and persist ``config.yaml``.

        Returns ``(True, "")`` on success, or ``(False, error_message)``.
        """
        key = (name or "").strip().lower()
        if not key:
            return False, "Provider name is required."
        models = self._config.get("models")
        if not isinstance(models, dict):
            return False, "Invalid config: missing models section."
        providers = models.get("providers")
        if not isinstance(providers, dict) or key not in providers:
            return False, f"Unknown provider: {key}"
        models["primary"] = key
        try:
            self._config_path.write_text(
                yaml.safe_dump(self._config, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("Could not save config after provider switch: %s", e)
            return False, str(e)
        return True, ""
