"""
Centralized logging: WARNING+ to console, DEBUG+ to jarvis.log.
"""

from __future__ import annotations

import logging
from pathlib import Path


_LOG_DIR = Path(__file__).resolve().parent.parent
_LOG_FILE = _LOG_DIR / "jarvis.log"

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger that writes WARNING+ to stderr and DEBUG+ to jarvis.log.

    Root configuration is applied once so handlers are not duplicated.
    """
    global _CONFIGURED
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not _CONFIGURED:
        _CONFIGURED = True
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)

        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(fmt)

        file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)

        root.handlers.clear()
        root.addHandler(console)
        root.addHandler(file_handler)

    return logger
