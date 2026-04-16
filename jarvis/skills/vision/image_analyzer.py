"""Vision-capable image analyzer with provider fallbacks."""

from __future__ import annotations

import base64
import io
import os
import tempfile
from pathlib import Path
from typing import Any

import requests
from openai import OpenAI
from PIL import Image

try:
    import pytesseract

    OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    OCR_AVAILABLE = False

try:
    import google.generativeai as genai

    GEMINI_AVAILABLE = True
except Exception:
    genai = None
    GEMINI_AVAILABLE = False


class ImageAnalyzer:
    """Analyze images using Groq/Gemini/Ollama, then OCR fallback."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config or {}

    def _provider_cfg(self, name: str) -> dict[str, Any]:
        models = self.config.get("models") or {}
        providers = models.get("providers") if isinstance(models, dict) else {}
        cfg = providers.get(name) if isinstance(providers, dict) else {}
        return cfg if isinstance(cfg, dict) else {}

    def _error(self, message: str) -> dict[str, Any]:
        return {"success": False, "error": message}

    def _encode_image(self, path: str) -> str:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _resize_image(self, path: str, max_size: int = 1024) -> str:
        img = Image.open(path)
        w, h = img.size
        longest = max(w, h)
        if longest <= max_size:
            return path
        ratio = max_size / float(longest)
        resized = img.resize((int(w * ratio), int(h * ratio)))
        fd, out_path = tempfile.mkstemp(prefix="jarvis_vision_", suffix=".png")
        os.close(fd)
        resized.save(out_path, format="PNG")
        return out_path

    def _analyze_groq(self, image_path: str, question: str) -> str | None:
        cfg = self._provider_cfg("groq")
        api_key = str(cfg.get("api_key") or "").strip()
        if not api_key:
            return None
        client = OpenAI(api_key=api_key, base_url=str(cfg.get("base_url") or "https://api.groq.com/openai/v1"))
        b64 = self._encode_image(image_path)
        res = client.chat.completions.create(
            model="llava-v1.5-7b-4096-preview",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": question},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ],
                }
            ],
            max_tokens=1024,
        )
        return (res.choices[0].message.content or "").strip()

    def _analyze_gemini(self, image_path: str, question: str) -> str | None:
        if not GEMINI_AVAILABLE:
            return None
        cfg = self._provider_cfg("gemini")
        api_key = str(cfg.get("api_key") or "").strip()
        if not api_key:
            return None
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        img = Image.open(image_path)
        rsp = model.generate_content([question, img])
        return (getattr(rsp, "text", "") or "").strip()

    def _analyze_ollama(self, image_path: str, question: str) -> str | None:
        cfg = self._provider_cfg("ollama")
        base_url = str(cfg.get("base_url") or "http://localhost:11434/v1").rstrip("/")
        if "/v1" in base_url:
            base_url = base_url.rsplit("/v1", 1)[0]
        b64 = self._encode_image(image_path)
        rsp = requests.post(
            f"{base_url}/api/generate",
            json={"model": "llava", "prompt": question, "images": [b64], "stream": False},
            timeout=45,
        )
        if rsp.ok:
            data = rsp.json()
            return str(data.get("response") or "").strip()
        return None

    def _ocr_fallback(self, image_path: str) -> str:
        if not OCR_AVAILABLE:
            return "Vision features require additional setup: configure Groq/Gemini/Ollama or install Tesseract OCR."
        text = pytesseract.image_to_string(Image.open(image_path))
        return f"OCR fallback result:\n{text.strip()}"

    def analyze(self, image_path: str, question: str = "Describe this image in detail") -> dict[str, Any]:
        temp_resized: str | None = None
        try:
            if not Path(image_path).exists():
                return self._error(f"Image not found: {image_path}")
            temp_resized = self._resize_image(image_path, max_size=1024)

            for name, fn in (
                ("groq:llava-v1.5-7b-4096-preview", self._analyze_groq),
                ("gemini:gemini-1.5-flash", self._analyze_gemini),
                ("ollama:llava", self._analyze_ollama),
            ):
                try:
                    desc = fn(temp_resized, question)
                    if desc:
                        return {
                            "success": True,
                            "description": desc,
                            "model_used": name,
                            "question_asked": question,
                            "image_path": image_path,
                        }
                except Exception:
                    continue

            return {
                "success": True,
                "description": self._ocr_fallback(temp_resized),
                "model_used": "ocr_fallback",
                "question_asked": question,
                "image_path": image_path,
            }
        except Exception as e:
            return self._error(str(e))
        finally:
            if temp_resized and temp_resized != image_path:
                try:
                    os.remove(temp_resized)
                except Exception:
                    pass

    def analyze_url(self, url: str, question: str = "Describe this image") -> dict[str, Any]:
        temp_path: str | None = None
        try:
            rsp = requests.get(url, timeout=30)
            rsp.raise_for_status()
            img = Image.open(io.BytesIO(rsp.content))
            fd, temp_path = tempfile.mkstemp(prefix="jarvis_url_", suffix=".png")
            os.close(fd)
            img.save(temp_path)
            return self.analyze(temp_path, question)
        except Exception as e:
            return self._error(str(e))
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def extract_text_from_image(self, image_path: str) -> dict[str, Any]:
        return self.analyze(
            image_path,
            question="Extract ALL text visible in this image exactly as written. Return only the text, nothing else.",
        )

    def detect_objects(self, image_path: str) -> dict[str, Any]:
        return self.analyze(
            image_path,
            question="List all objects, people, and items visible in this image as a simple comma-separated list.",
        )

    def read_chart(self, image_path: str) -> dict[str, Any]:
        return self.analyze(
            image_path,
            question="This is a chart or graph. Describe the data, trends, and key insights you can see.",
        )


if __name__ == "__main__":
    analyzer = ImageAnalyzer({})
    print(analyzer.analyze("example.png"))
