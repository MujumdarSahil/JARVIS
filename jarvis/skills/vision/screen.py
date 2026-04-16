"""Screen capture and OCR helpers with optional dependencies."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

try:
    import mss

    MSS_AVAILABLE = True
except Exception:
    mss = None
    MSS_AVAILABLE = False

try:
    import pygetwindow as gw

    PYGETWINDOW_AVAILABLE = True
except Exception:
    gw = None
    PYGETWINDOW_AVAILABLE = False

try:
    import pytesseract

    OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    OCR_AVAILABLE = False

from PIL import Image, ImageChops


class ScreenSkill:
    """Capture, OCR, and compare desktop screenshots."""

    def __init__(self, screenshot_dir: str = "screenshots") -> None:
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _error(self, message: str) -> dict[str, Any]:
        return {"success": False, "error": message}

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _save_shot(self, raw: bytes, size: tuple[int, int], prefix: str = "screenshot") -> str:
        img = Image.frombytes("RGB", size, raw)
        out = self.screenshot_dir / f"{prefix}_{self._timestamp()}.png"
        img.save(out)
        return str(out)

    def capture_screen(self, monitor: int = 0) -> dict[str, Any]:
        try:
            if not MSS_AVAILABLE:
                return self._error("Screen capture requires 'mss'. Install with: pip install mss")
            with mss.mss() as sct:
                monitors = sct.monitors
                if monitor < 0 or monitor >= len(monitors):
                    monitor = 0
                mon = monitors[monitor]
                shot = sct.grab(mon)
                path = self._save_shot(shot.rgb, shot.size)
                return {
                    "success": True,
                    "path": path,
                    "width": shot.width,
                    "height": shot.height,
                    "timestamp": datetime.now().isoformat(),
                }
        except Exception as e:
            return self._error(str(e))

    def capture_region(self, x: int, y: int, width: int, height: int) -> dict[str, Any]:
        try:
            if not MSS_AVAILABLE:
                return self._error("Region capture requires 'mss'. Install with: pip install mss")
            if width <= 0 or height <= 0:
                return self._error("Width and height must be positive integers")
            with mss.mss() as sct:
                mon = {"left": x, "top": y, "width": width, "height": height}
                shot = sct.grab(mon)
                path = self._save_shot(shot.rgb, shot.size, prefix="region")
                return {
                    "success": True,
                    "path": path,
                    "width": shot.width,
                    "height": shot.height,
                    "timestamp": datetime.now().isoformat(),
                }
        except Exception as e:
            return self._error(str(e))

    def capture_window(self, window_title: str) -> dict[str, Any]:
        try:
            if os.name != "nt" or not PYGETWINDOW_AVAILABLE:
                return self.capture_screen(0)
            windows = gw.getWindowsWithTitle(window_title or "")
            if not windows:
                return self.capture_screen(0)
            win = windows[0]
            return self.capture_region(win.left, win.top, win.width, win.height)
        except Exception as e:
            return self._error(str(e))

    def get_screen_text(self, monitor: int = 0) -> dict[str, Any]:
        try:
            if not OCR_AVAILABLE:
                return self._error(
                    "OCR unavailable. Install Tesseract OCR and pytesseract, then ensure tesseract is in PATH."
                )
            shot = self.capture_screen(monitor=monitor)
            if not shot.get("success"):
                return shot
            text = pytesseract.image_to_string(Image.open(str(shot["path"])))
            return {"success": True, "text": text.strip(), "path": shot["path"]}
        except Exception as e:
            return self._error(str(e))

    def get_monitors(self) -> list[dict[str, Any]]:
        try:
            if not MSS_AVAILABLE:
                return [{"success": False, "error": "mss is not installed"}]
            out: list[dict[str, Any]] = []
            with mss.mss() as sct:
                for i, mon in enumerate(sct.monitors):
                    out.append(
                        {
                            "index": i,
                            "left": mon["left"],
                            "top": mon["top"],
                            "width": mon["width"],
                            "height": mon["height"],
                        }
                    )
            return out
        except Exception as e:
            return [{"success": False, "error": str(e)}]

    def compare_screens(self, path1: str, path2: str) -> dict[str, Any]:
        try:
            img1 = Image.open(path1).convert("RGB")
            img2 = Image.open(path2).convert("RGB")
            if img1.size != img2.size:
                img2 = img2.resize(img1.size)
            diff = ImageChops.difference(img1, img2)
            bbox = diff.getbbox()
            if not bbox:
                return {"success": True, "similarity_percent": 100.0, "differences_found": False, "changed_regions": []}
            hist = diff.histogram()
            sq = sum((i % 256) ** 2 * v for i, v in enumerate(hist))
            rms = (sq / float(img1.size[0] * img1.size[1])) ** 0.5
            similarity = max(0.0, min(100.0, 100.0 - (rms / 255.0 * 100.0)))
            return {
                "success": True,
                "similarity_percent": round(similarity, 2),
                "differences_found": True,
                "changed_regions": [{"x1": bbox[0], "y1": bbox[1], "x2": bbox[2], "y2": bbox[3]}],
            }
        except Exception as e:
            return self._error(str(e))

    def watch_screen(self, callback: Callable[[str], None], interval: int = 5, duration: int = 60) -> dict[str, Any]:
        try:
            if interval <= 0 or duration <= 0:
                return self._error("interval and duration must be positive")

            def _runner() -> None:
                end_at = time.time() + duration
                while time.time() < end_at:
                    shot = self.capture_screen()
                    if shot.get("success"):
                        try:
                            callback(str(shot["path"]))
                        except Exception:
                            pass
                    time.sleep(interval)

            th = threading.Thread(target=_runner, daemon=True)
            th.start()
            return {"success": True, "message": "Screen watcher started in background thread"}
        except Exception as e:
            return self._error(str(e))


if __name__ == "__main__":
    skill = ScreenSkill()
    print(skill.capture_screen())
