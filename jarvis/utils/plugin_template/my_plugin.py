"""
JARVIS PLUGIN TEMPLATE
Copy this file to plugins/custom/my_plugin.py and customize.
"""

from plugins.base_plugin import BasePlugin


class MyPlugin(BasePlugin):
    name = "my_plugin"
    version = "1.0.0"
    description = "What my plugin does"
    commands = ["/mycommand"]
    keywords = ["trigger word"]

    def on_load(self) -> bool:
        return True

    def execute(self, command: str, args: str, context: dict) -> dict:
        try:
            _ = (command, context)
            result = f"You said: {args}"
            return self._success(result)
        except Exception as e:
            return self._error(str(e))

    def on_unload(self):
        return None


if __name__ == "__main__":
    print(MyPlugin().execute("/mycommand", "hello", {}))
