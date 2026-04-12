"""
Text-to-speech: edge-tts (primary) with pyttsx3 offline fallback.
Playback and synthesis run on a dedicated worker thread.
"""

from __future__ import annotations

import asyncio
import os
import queue
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_SENTINEL = object()


class Speaker:
    """
    Queues speech jobs on a background thread so callers return quickly.
    ``speak_sync`` blocks the *calling* thread until audio finishes (not the worker enqueue).
    """

    def __init__(
        self,
        voice: str = "en-GB-RyanNeural",
        rate: str = "+10%",
        volume: str = "+0%",
        prefer_engine: str = "edge-tts",
    ) -> None:
        self._voice = voice
        self._rate = rate
        self._volume = volume
        self._prefer_engine = (prefer_engine or "edge-tts").strip().lower()
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._lock = threading.Lock()
        self._pygame_ok = False
        self._pyttsx3_engine = None

        try:
            self._thread = threading.Thread(target=self._worker, name="SpeakerWorker", daemon=True)
            self._thread.start()
        except Exception as e:
            logger.exception("Speaker worker thread failed to start: %s", e)
            self._thread = None

    @property
    def is_ready(self) -> bool:
        """True if the background worker thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def _ensure_pygame(self) -> bool:
        with self._lock:
            if self._pygame_ok:
                return True
            try:
                import pygame

                pygame.mixer.init()
                self._pygame_ok = True
                return True
            except Exception as e:
                logger.warning("pygame mixer init failed: %s", e)
                return False

    def _stop_audio_output(self) -> None:
        try:
            import pygame

            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception as e:
            logger.debug("stop_audio_output: %s", e)

    def _play_mp3_path(self, path: Path) -> None:
        import pygame

        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if self._stop_flag.is_set():
                pygame.mixer.music.stop()
                break
            pygame.time.wait(50)

    def _speak_edge(self, text: str) -> bool:
        try:
            import edge_tts
        except Exception as e:
            logger.warning("edge-tts not available: %s", e)
            return False

        tmp: Path | None = None
        try:
            communicate = edge_tts.Communicate(text, self._voice, rate=self._rate, volume=self._volume)
            fd, tmp_name = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            tmp = Path(tmp_name)

            async def _save() -> None:
                await communicate.save(str(tmp))

            asyncio.run(_save())

            if not self._ensure_pygame():
                return False
            self._play_mp3_path(tmp)
            return True
        except Exception as e:
            logger.warning("edge-tts playback failed: %s", e)
            return False
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def _speak_pyttsx3(self, text: str) -> bool:
        try:
            import pyttsx3
        except Exception as e:
            logger.warning("pyttsx3 not available: %s", e)
            return False

        try:
            if self._pyttsx3_engine is None:
                self._pyttsx3_engine = pyttsx3.init()
            eng = self._pyttsx3_engine
            eng.say(text)
            eng.runAndWait()
            return True
        except Exception as e:
            logger.warning("pyttsx3 failed: %s", e)
            self._pyttsx3_engine = None
            return False

    def _synthesize_and_play(self, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return

        if self._stop_flag.is_set():
            return

        use_edge_first = self._prefer_engine != "pyttsx3"

        if use_edge_first:
            if self._speak_edge(t):
                return
            if self._stop_flag.is_set():
                return
            self._speak_pyttsx3(t)
        else:
            if self._speak_pyttsx3(t):
                return
            if self._stop_flag.is_set():
                return
            self._speak_edge(t)

    def _worker(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is _SENTINEL:
                break

            text, done_event = item
            try:
                self._stop_flag.clear()
                self._synthesize_and_play(text)
            except Exception as e:
                logger.exception("TTS worker error: %s", e)
            finally:
                if done_event is not None:
                    done_event.set()
                self._q.task_done()

    def speak(self, text: str) -> None:
        """
        Enqueue ``text`` for TTS. Returns quickly (typically well under 100 ms).
        """
        if not self._thread or not self._thread.is_alive():
            logger.warning("speak: worker not running")
            return
        try:
            self._q.put((text, None), timeout=0.1)
        except Exception as e:
            logger.warning("speak: queue put failed: %s", e)

    def speak_sync(self, text: str) -> None:
        """Block until ``text`` has been fully spoken (or skipped on failure)."""
        if not self._thread or not self._thread.is_alive():
            logger.warning("speak_sync: worker not running")
            return
        done = threading.Event()
        try:
            self._q.put((text, done), timeout=0.1)
        except Exception as e:
            logger.warning("speak_sync: queue put failed: %s", e)
            return
        done.wait(timeout=600.0)

    def stop(self) -> None:
        """Interrupt current speech and clear queued phrases."""
        self._stop_flag.set()
        self._stop_audio_output()
        try:
            while True:
                self._q.get_nowait()
                self._q.task_done()
        except queue.Empty:
            pass

    def set_voice(self, voice: str) -> None:
        """Change edge-tts voice id for subsequent utterances."""
        self._voice = (voice or "").strip() or self._voice

    def list_voices(self) -> list[dict[str, Any]]:
        """Return edge-tts voices whose locale is English."""
        try:
            import edge_tts
        except Exception as e:
            logger.warning("list_voices: edge-tts missing: %s", e)
            return []

        async def _list() -> list[dict[str, Any]]:
            raw = await edge_tts.list_voices()
            out: list[dict[str, Any]] = []
            for v in raw:
                loc = str(v.get("Locale", "")).lower()
                if loc.startswith("en"):
                    out.append(
                        {
                            "ShortName": v.get("ShortName", ""),
                            "Locale": v.get("Locale", ""),
                            "Gender": v.get("Gender", ""),
                        }
                    )
            return sorted(out, key=lambda x: str(x.get("ShortName", "")))

        try:
            return asyncio.run(_list())
        except Exception as e:
            logger.warning("list_voices failed: %s", e)
            return []

    def shutdown(self) -> None:
        """Stop worker permanently."""
        self.stop()
        if self._thread and self._thread.is_alive():
            try:
                self._q.put(_SENTINEL, timeout=0.5)
            except Exception:
                pass
            self._thread.join(timeout=3.0)
        self._thread = None


if __name__ == "__main__":
    import logging
    import os
    import sys

    _root = Path(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    logging.basicConfig(level=logging.DEBUG)
    S = Speaker()
    print("Speaking test phrase...")
    S.speak_sync("Jarvis online. Good evening, Sir.")
    time.sleep(0.5)
    S.shutdown()
    print("Done.")
