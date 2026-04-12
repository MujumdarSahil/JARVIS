"""Voice: local STT, TTS, and optional wake-word detection."""

from .listener import Listener
from .speaker import Speaker
from .wakeword import WakeWordDetector

__all__ = ["Listener", "Speaker", "WakeWordDetector"]
