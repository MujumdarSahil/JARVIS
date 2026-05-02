"""Built-in Jarvis plugins."""

from plugins.builtin.jokes_plugin import JokesPlugin
from plugins.builtin.notes_plugin import NotesPlugin
from plugins.builtin.weather_plugin import WeatherPlugin

__all__ = ["WeatherPlugin", "JokesPlugin", "NotesPlugin"]


if __name__ == "__main__":
    print("builtin plugins ready")
