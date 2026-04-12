"""
Local speech-to-text using faster-whisper (no cloud API).
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from utils.logger import get_logger

logger = get_logger(__name__)

# Whisper expects 16 kHz mono for best results.
SAMPLE_RATE = 16000
# Normalized float samples in [-1, 1]; RMS below this counts as silence.
DEFAULT_RMS_THRESHOLD = 0.012
_CHUNK_SEC = 0.1


class Listener:
    """
    Captures microphone audio, applies RMS-based silence gating, transcribes with faster-whisper.
    """

    _download_message_shown = False

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        language: str = "en",
        rms_threshold: float = DEFAULT_RMS_THRESHOLD,
    ) -> None:
        self._language = (language or "en").strip() or "en"
        self._rms_threshold = float(rms_threshold)
        self._model = None
        self._model_size = model_size
        self._device = device
        self._ok = False
        self._continuous_stop = threading.Event()
        self._continuous_thread: threading.Thread | None = None

        try:
            if not Listener._download_message_shown:
                print("Downloading / loading STT model (first run may take a while)...", flush=True)
                Listener._download_message_shown = True

            from faster_whisper import WhisperModel

            compute_type = "float16" if device == "cuda" else "int8"
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
            )
            self._ok = True
            logger.info("faster-whisper model %r loaded on %s", model_size, device)
        except Exception as e:
            logger.exception("Listener init failed (mic/STT unavailable): %s", e)
            self._model = None
            self._ok = False

    @property
    def available(self) -> bool:
        return self._ok and self._model is not None

    def _record_to_numpy(self, duration_sec: float) -> "object | None":
        """Record ``duration_sec`` seconds; returns float32 mono numpy array or None."""
        try:
            import numpy as np
            import sounddevice as sd

            frames = int(max(duration_sec, 0.05) * SAMPLE_RATE)
            audio = sd.rec(
                frames,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocking=True,
            )
            return np.squeeze(audio)
        except Exception as e:
            logger.warning("Recording failed: %s", e)
            return None

    @staticmethod
    def _rms(audio: "object") -> float:
        try:
            import numpy as np

            if audio is None or len(audio) == 0:
                return 0.0
            x = np.asarray(audio, dtype=np.float64)
            return float(np.sqrt(np.mean(np.square(x))))
        except Exception:
            return 0.0

    def _transcribe_file(self, wav_path: Path) -> str | None:
        if not self.available:
            return None
        try:
            import soundfile as sf

            segments, _info = self._model.transcribe(
                str(wav_path),
                language=self._language,
                beam_size=5,
                vad_filter=True,
            )
            parts: list[str] = []
            for seg in segments:
                t = (seg.text or "").strip()
                if t:
                    parts.append(t)
            text = " ".join(parts).strip()
            return text or None
        except Exception as e:
            logger.warning("Transcription failed: %s", e)
            return None

    def listen_once(
        self,
        timeout: float = 10.0,
        phrase_limit: float = 30.0,
    ) -> str | None:
        """
        Wait up to ``timeout`` seconds for speech, then record until silence or ``phrase_limit`` cap.

        Returns transcribed text, or None on silence / errors.
        """
        if not self.available:
            logger.warning("listen_once: STT not available")
            return None

        try:
            import numpy as np
            import sounddevice as sd
            import soundfile as sf
        except Exception as e:
            logger.warning("Audio dependency missing: %s", e)
            return None

        try:
            # Phase 1: wait for voice activity (RMS above threshold).
            deadline = time.monotonic() + max(timeout, 0.5)
            heard = False
            while time.monotonic() < deadline:
                chunk = self._record_to_numpy(_CHUNK_SEC)
                if chunk is None:
                    return None
                if self._rms(chunk) >= self._rms_threshold:
                    heard = True
                    break

            if not heard:
                logger.debug("listen_once: no voice within timeout")
                return None

            # Phase 2: keep recording until silence or phrase limit.
            chunks: list = [chunk]
            silence_run = 0.0
            total_speech = float(_CHUNK_SEC)
            max_total = max(phrase_limit, _CHUNK_SEC)

            while total_speech < max_total:
                nxt = self._record_to_numpy(_CHUNK_SEC)
                if nxt is None:
                    break
                chunks.append(nxt)
                total_speech += _CHUNK_SEC
                if self._rms(nxt) < self._rms_threshold:
                    silence_run += _CHUNK_SEC
                    if silence_run >= 0.45:
                        break
                else:
                    silence_run = 0.0

            audio = np.concatenate(chunks) if chunks else None
            if audio is None or len(audio) == 0:
                return None
            if self._rms(audio) < self._rms_threshold:
                logger.debug("listen_once: aggregate RMS too low")
                return None

            fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            path = Path(tmp_path)
            try:
                sf.write(str(path), audio, SAMPLE_RATE, subtype="PCM_16")
                return self._transcribe_file(path)
            finally:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

        except Exception as e:
            logger.exception("listen_once failed: %s", e)
            return None

    def listen_continuous(self, callback: Callable[[str], None]) -> None:
        """
        Background loop: repeated ``listen_once``; invokes ``callback`` with each non-empty transcript.
        """
        if self._continuous_thread and self._continuous_thread.is_alive():
            logger.warning("listen_continuous: already running")
            return

        self._continuous_stop.clear()

        def _loop() -> None:
            while not self._continuous_stop.is_set():
                try:
                    text = self.listen_once(timeout=10.0, phrase_limit=30.0)
                    if text:
                        try:
                            callback(text)
                        except Exception as e:
                            logger.exception("listen_continuous callback error: %s", e)
                except Exception as e:
                    logger.warning("listen_continuous loop error: %s", e)
                time.sleep(0.2)

        self._continuous_thread = threading.Thread(target=_loop, name="ListenerContinuous", daemon=True)
        self._continuous_thread.start()

    def stop(self) -> None:
        """Stop continuous listening thread."""
        self._continuous_stop.set()
        if self._continuous_thread and self._continuous_thread.is_alive():
            self._continuous_thread.join(timeout=2.0)
        self._continuous_thread = None

    def transcribe_numpy(self, audio_mono_float32: "object") -> str | None:
        """
        Transcribe an in-memory mono float32 buffer at ``SAMPLE_RATE`` Hz (helper for wake-word).
        """
        if not self.available:
            return None
        try:
            import numpy as np
            import soundfile as sf
        except Exception as e:
            logger.warning("transcribe_numpy: missing deps: %s", e)
            return None

        path: Path | None = None
        try:
            x = np.asarray(audio_mono_float32, dtype=np.float32).reshape(-1)
            if x.size == 0:
                return None
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            path = Path(tmp)
            sf.write(str(path), x, SAMPLE_RATE, subtype="PCM_16")
            return self._transcribe_file(path)
        except Exception as e:
            logger.warning("transcribe_numpy failed: %s", e)
            return None
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


if __name__ == "__main__":
    import sys

    _root = Path(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    logging.basicConfig(level=logging.DEBUG)
    L = Listener(model_size="tiny", device="cpu")
    if not L.available:
        print("Listener not available — check logs.")
        raise SystemExit(1)
    print("Speak now (one utterance)...")
    t = L.listen_once(timeout=8, phrase_limit=15)
    print("Heard:", repr(t))
