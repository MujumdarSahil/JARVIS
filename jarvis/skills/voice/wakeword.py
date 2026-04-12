"""
Wake-word detection: Picovoice Porcupine when an access key is configured,
otherwise periodic faster-whisper transcription on 2-second audio windows.
"""

from __future__ import annotations

import array
import threading
import time
from pathlib import Path
from typing import Callable

from utils.logger import get_logger

logger = get_logger(__name__)


def play_wake_beep() -> None:
    """Short attention tone using pygame + numpy (never raises)."""
    try:
        import numpy as np
        import pygame

        pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
        duration = 0.12
        sr = 22050
        t = np.linspace(0.0, duration, int(sr * duration), endpoint=False, dtype=np.float64)
        wave = (np.sin(2.0 * np.pi * 880.0 * t) * 0.35 * 32767.0).astype(np.int16)
        arr = np.column_stack([wave]).astype(np.int16)
        snd = pygame.sndarray.make_sound(arr)
        ch = snd.play()
        while ch.get_busy():
            pygame.time.wait(30)
    except Exception as e:
        logger.warning("play_wake_beep failed: %s", e)


class WakeWordDetector:
    """
    Invokes ``callback`` (no arguments) when the wake phrase is detected.
    """

    def __init__(
        self,
        wake_word: str = "hey jarvis",
        listener: "object | None" = None,
        porcupine_key: str | None = None,
    ) -> None:
        self._wake = (wake_word or "hey jarvis").strip().lower()
        self._listener = listener
        self._porcupine_key = (porcupine_key or "").strip() or None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mode = "inactive"
        self._porcupine = None

    @property
    def mode(self) -> str:
        """Human-readable active backend: ``porcupine``, ``whisper``, or ``inactive``."""
        return self._mode

    def _detect_with_whisper(self, callback: Callable[[], None]) -> None:
        from .listener import Listener

        lst = self._listener
        if lst is None or not getattr(lst, "available", False):
            try:
                lst = Listener(model_size="tiny", device="cpu", language="en")
            except Exception as e:
                logger.warning("Whisper wake fallback: could not create Listener: %s", e)
                return
            self._listener = lst

        if not lst.available:
            logger.warning("Whisper wake fallback: STT unavailable")
            return

        chunk_sec = 2.0
        logger.info("Wake word: using Whisper fallback (%.1fs chunks, phrase=%r)", chunk_sec, self._wake)

        try:
            import numpy as np
            import sounddevice as sd
        except Exception as e:
            logger.warning("Whisper wake: audio deps missing: %s", e)
            return

        while not self._stop.is_set():
            try:
                frames = int(16000 * chunk_sec)
                audio = sd.rec(
                    frames,
                    samplerate=16000,
                    channels=1,
                    dtype="float32",
                    blocking=True,
                )
                audio = np.squeeze(audio)
                text = lst.transcribe_numpy(audio)
                if not text:
                    continue
                low = text.lower()
                if self._wake in low or "jarvis" in low:
                    try:
                        callback()
                    except Exception as e:
                        logger.exception("Wake callback error: %s", e)
            except Exception as e:
                logger.warning("Whisper wake loop error: %s", e)
                time.sleep(0.5)

    def _detect_with_porcupine(self, callback: Callable[[], None]) -> None:
        try:
            import pvporcupine
            import sounddevice as sd
        except Exception as e:
            logger.warning("Porcupine wake: missing dependency: %s", e)
            self._mode = "whisper"
            self._detect_with_whisper(callback)
            return

        try:
            porcupine = pvporcupine.create(
                access_key=self._porcupine_key,
                keywords=["jarvis"],
            )
            self._porcupine = porcupine
        except Exception as e:
            logger.warning("Porcupine init failed (%s); falling back to Whisper wake.", e)
            self._porcupine = None
            self._mode = "whisper"
            self._detect_with_whisper(callback)
            return

        logger.info("Wake word: using Picovoice Porcupine (keyword=jarvis)")

        try:
            import numpy as np

            with sd.RawInputStream(
                samplerate=porcupine.sample_rate,
                blocksize=porcupine.frame_length,
                dtype="int16",
                channels=1,
            ) as stream:
                while not self._stop.is_set():
                    pcm, _overflowed = stream.read(porcupine.frame_length)
                    if pcm is None:
                        time.sleep(0.01)
                        continue
                    nbytes = porcupine.frame_length * 2
                    if isinstance(pcm, bytes):
                        chunk = pcm[:nbytes]
                        if len(chunk) < nbytes:
                            time.sleep(0.01)
                            continue
                        samples = array.array("h")
                        samples.frombytes(chunk)
                    else:
                        arr = np.asarray(pcm)
                        if arr.shape[0] < porcupine.frame_length:
                            time.sleep(0.01)
                            continue
                        flat = np.ascontiguousarray(arr[:porcupine.frame_length]).reshape(-1)
                        samples = array.array("h")
                        samples.frombytes(flat.astype(np.int16).tobytes())
                    keyword_index = porcupine.process(samples)
                    if keyword_index >= 0:
                        try:
                            callback()
                        except Exception as e:
                            logger.exception("Wake callback error: %s", e)
        finally:
            try:
                porcupine.delete()
            except Exception:
                pass
            self._porcupine = None

    def start(self, callback: Callable[[], None]) -> None:
        """Start background detection; ``callback`` runs on each detection."""
        if self._thread and self._thread.is_alive():
            logger.warning("WakeWordDetector.start: already running")
            return

        self._stop.clear()

        def _run() -> None:
            try:
                if self._porcupine_key:
                    self._mode = "porcupine"
                    self._detect_with_porcupine(callback)
                else:
                    self._mode = "whisper"
                    self._detect_with_whisper(callback)
            except Exception as e:
                logger.exception("Wake detector thread crashed: %s", e)
            finally:
                self._mode = "inactive"

        self._thread = threading.Thread(target=_run, name="WakeWordDetector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._mode = "inactive"


if __name__ == "__main__":
    import logging
    import os
    import sys

    _root = Path(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    logging.basicConfig(level=logging.DEBUG)

    hits = {"n": 0}

    def on_wake() -> None:
        hits["n"] += 1
        print(f"Wake #{hits['n']}")

    d = WakeWordDetector(wake_word="hey jarvis", listener=None, porcupine_key=os.environ.get("PORCUPINE_KEY"))
    print("Starting detector; Ctrl+C to stop. Mode will be logged.")
    d.start(on_wake)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    d.stop()
    print("Stopped.")
