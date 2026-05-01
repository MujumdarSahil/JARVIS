# SELF-AWARE SYSTEM
"""Response tone tuning by mood."""

from __future__ import annotations


class ToneAdapter:
    def __init__(self) -> None:
        self._neutral_idx = 0

    def get_system_prompt_addon(self, mood: str, sentiment: dict) -> str:
        mood = (mood or "neutral").lower()
        if mood == "frustrated":
            return "The user seems frustrated. Be extra concise, direct, and solution-focused. Skip pleasantries. Get straight to the fix. Acknowledge their frustration briefly."
        if mood == "impatient":
            return "The user is impatient. Give the shortest possible answer. One sentence if possible. Offer to elaborate only if needed."
        if mood == "excited":
            return "The user is excited and engaged. Match their energy. Be enthusiastic but focused. Elaborate more than usual."
        if mood == "confused":
            return "The user seems confused. Use simple language. Break your answer into clear numbered steps. Avoid jargon. Offer an analogy."
        if (sentiment or {}).get("sentiment") == "urgent":
            return "The user needs this urgently. Lead with the direct answer immediately. No preamble whatsoever."
        if mood == "satisfied":
            return "The user is happy with progress. You can be warmer and more conversational."
        return ""

    def get_greeting_style(self, mood: str) -> str:
        mood = (mood or "neutral").lower()
        if mood == "frustrated":
            return ""
        if mood == "excited":
            return "Excellent, Sir!"
        if mood == "confused":
            return "Let me clarify that, Sir."
        if mood == "neutral":
            opts = ["Of course, Sir.", "Certainly, Sir."]
            choice = opts[self._neutral_idx % len(opts)]
            self._neutral_idx += 1
            return choice
        return ""

    def should_check_in(self, mood: str, messages_since_last_checkin: int) -> bool:
        return (mood or "").lower() in {"frustrated", "impatient"} and int(messages_since_last_checkin or 0) >= 3


if __name__ == "__main__":
    ta = ToneAdapter()
    print(ta.get_system_prompt_addon("confused", {}))
