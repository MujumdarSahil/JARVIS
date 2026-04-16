"""Vision-focused Jarvis sub-agent."""

from __future__ import annotations

import time
from typing import Any

from core.agents.base_agent import BaseAgent
from skills.vision.image_analyzer import ImageAnalyzer
from skills.vision.screen import ScreenSkill


class VisionAgent(BaseAgent):
    """Handles screenshot capture and image understanding requests."""

    def __init__(
        self,
        brain: Any,
        db: Any,
        config: dict[str, Any],
        screen_skill: ScreenSkill,
        image_analyzer: ImageAnalyzer,
    ) -> None:
        super().__init__("vision", brain, db, config)
        self.screen_skill = screen_skill
        self.image_analyzer = image_analyzer

    def describe_screen(self) -> dict[str, Any]:
        shot = self.screen_skill.capture_screen()
        if not shot.get("success"):
            return shot
        return self.image_analyzer.analyze(str(shot["path"]), question="Describe what is currently visible on this screen.")

    def answer_about_screen(self, question: str) -> dict[str, Any]:
        shot = self.screen_skill.capture_screen()
        if not shot.get("success"):
            return shot
        return self.image_analyzer.analyze(str(shot["path"]), question=question or "Describe this screen.")

    def monitor_for_change(self, description: str, timeout: int = 120) -> dict[str, Any]:
        try:
            first = self.screen_skill.capture_screen()
            if not first.get("success"):
                return first
            start = time.time()
            while time.time() - start < timeout:
                time.sleep(2)
                current = self.screen_skill.capture_screen()
                if not current.get("success"):
                    continue
                cmp = self.screen_skill.compare_screens(str(first["path"]), str(current["path"]))
                if cmp.get("success") and (100.0 - float(cmp.get("similarity_percent", 100.0))) > 20.0:
                    analysis = self.image_analyzer.analyze(
                        str(current["path"]),
                        question=f"The user is monitoring this condition: {description}. Describe what changed.",
                    )
                    return {
                        "success": True,
                        "change_detected": True,
                        "comparison": cmp,
                        "description": analysis.get("description") if isinstance(analysis, dict) else str(analysis),
                    }
            return {"success": True, "change_detected": False, "message": "No significant change detected in timeout window."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            action = str((task.get("params") or {}).get("action") or "").strip().lower()
            params = task.get("params") if isinstance(task.get("params"), dict) else {}
            if action == "screenshot":
                return self.describe_screen()
            if action == "analyze_image":
                image_path = str(params.get("path") or "").strip()
                image_url = str(params.get("url") or "").strip()
                question = str(params.get("question") or "Describe this image in detail")
                if image_url:
                    return self.image_analyzer.analyze_url(image_url, question=question)
                if not image_path:
                    return {"success": False, "error": "Missing 'path' or 'url' for analyze_image action"}
                return self.image_analyzer.analyze(image_path, question=question)
            if action == "read_screen":
                return self.screen_skill.get_screen_text(int(params.get("monitor") or 0))
            if action == "watch":
                desc = str(params.get("description") or "screen change")
                timeout = int(params.get("timeout") or 120)
                return self.monitor_for_change(desc, timeout=timeout)
            if action == "read_image_text":
                image_path = str(params.get("path") or "").strip()
                if not image_path:
                    return {"success": False, "error": "Missing image path for read_image_text action"}
                return self.image_analyzer.extract_text_from_image(image_path)
            if action == "answer_screen":
                return self.answer_about_screen(str(params.get("question") or "What's on screen?"))
            return {"success": False, "error": f"Unknown vision action: {action}"}

        try:
            return self._run_wrapped(task, _run)
        except Exception as e:
            return {"success": False, "result": str(e), "agent": self.name, "task_id": str(task.get("id") or ""), "duration_ms": 0}


if __name__ == "__main__":
    from core.db import db

    class _DummyBrain:
        def get_active_provider(self) -> str:
            return "none"

        def estimate_tokens(self, text: str) -> int:
            return max(1, len(text) // 4)

    vision = VisionAgent(_DummyBrain(), db, {}, ScreenSkill(), ImageAnalyzer({}))
    print(vision.execute({"id": "test", "params": {"action": "screenshot"}}))
