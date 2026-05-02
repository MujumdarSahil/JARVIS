"""Weather plugin using Open-Meteo APIs."""

from __future__ import annotations

from typing import Any

import requests

from plugins.base_plugin import BasePlugin


class WeatherPlugin(BasePlugin):
    name = "weather"
    version = "1.0.0"
    description = "Real-time weather forecasts using Open-Meteo (free, no API key)"
    commands = ["/weather", "/forecast"]
    keywords = ["weather", "temperature", "forecast", "rain", "humidity", "will it rain", "hot today", "cold today"]
    enabled_by_default = True

    def _geocode(self, city: str) -> dict[str, Any]:
        geo_url = "https://geocoding-api.open-meteo.com/v1/search"
        r = requests.get(geo_url, params={"name": city, "count": 1}, timeout=10)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if not results:
            raise ValueError(f"Could not find city: {city}")
        return results[0]

    def execute(self, command: str, args: str, context: dict[str, Any]) -> dict[str, Any]:
        _ = context
        try:
            city = args.strip() or "Mumbai"
            if command.strip().lower() == "/forecast":
                fc = self.get_forecast(city, days=3)
                return self._success(fc["response"])
            place = self._geocode(city)
            lat = place["latitude"]
            lon = place["longitude"]
            weather_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "current_weather": True,
                "hourly": "temperature_2m,apparent_temperature,precipitation_probability,windspeed_10m,relative_humidity_2m",
                "forecast_days": 1,
            }
            r = requests.get(weather_url, params=params, timeout=10)
            r.raise_for_status()
            payload = r.json()
            current = payload.get("current_weather") or {}
            hourly = payload.get("hourly") or {}
            rain = (hourly.get("precipitation_probability") or [0])[0]
            feels_like = (hourly.get("apparent_temperature") or [current.get("temperature", "?")])[0]
            humidity = (hourly.get("relative_humidity_2m") or ["?"])[0]
            response = (
                f"{place.get('name', city)}: {current.get('temperature', '?')}°C, wind {current.get('windspeed', '?')} km/h. "
                f"Rain chance: {rain}%. Feels like: {feels_like}°C. Humidity: {humidity}%."
            )
            return self._success(response)
        except Exception as e:
            return self._error(str(e))

    def get_forecast(self, city: str, days: int = 3) -> dict[str, Any]:
        try:
            place = self._geocode(city)
            weather_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": max(1, min(days, 7)),
            }
            r = requests.get(weather_url, params=params, timeout=10)
            r.raise_for_status()
            daily = (r.json().get("daily") or {})
            lines = [f"{place.get('name', city)} forecast:"]
            max_t = daily.get("temperature_2m_max") or []
            min_t = daily.get("temperature_2m_min") or []
            rain = daily.get("precipitation_probability_max") or []
            dates = daily.get("time") or []
            for i, dt in enumerate(dates[:days]):
                lines.append(f"- {dt}: {min_t[i]}°C to {max_t[i]}°C, rain chance {rain[i]}%")
            return {"success": True, "response": "\n".join(lines), "plugin": self.name, "error": None}
        except Exception as e:
            return self._error(str(e))


if __name__ == "__main__":
    p = WeatherPlugin()
    print(p.execute("/weather", "Mumbai", {}))
