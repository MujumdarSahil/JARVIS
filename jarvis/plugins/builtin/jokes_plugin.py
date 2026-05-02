"""Simple joke plugin using JokeAPI."""

from __future__ import annotations

import requests

from plugins.base_plugin import BasePlugin


class JokesPlugin(BasePlugin):
    name = "jokes"
    version = "1.0.0"
    description = "Tells jokes — because even Jarvis needs humor"
    commands = ["/joke"]
    keywords = ["tell me a joke", "make me laugh", "say something funny"]

    def execute(self, command, args, context):
        _ = (command, args, context)
        try:
            url = "https://v2.jokeapi.dev/joke/Programming,Misc"
            r = requests.get(url, params={"blacklistFlags": "nsfw,racist"}, timeout=10)
            r.raise_for_status()
            j = r.json()
            if j.get("type") == "twopart":
                return self._success(f"{j.get('setup', '')}\n{j.get('delivery', '')}")
            return self._success(j.get("joke", "No joke available right now."))
        except Exception as e:
            return self._error(str(e))


if __name__ == "__main__":
    print(JokesPlugin().execute("/joke", "", {}))
