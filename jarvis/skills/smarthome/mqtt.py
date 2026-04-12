"""
MQTT client with optional auto-reconnect (daemon thread) and wildcard subscriptions.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

import paho.mqtt.client as mqtt

from utils.logger import get_logger

logger = get_logger(__name__)


def _topic_match(pattern: str, topic: str) -> bool:
    """MQTT + and # wildcard match (pattern is subscription, topic is incoming)."""
    p_parts = pattern.split("/")
    t_parts = topic.split("/")
    pi = ti = 0
    while pi < len(p_parts):
        seg = p_parts[pi]
        if seg == "#":
            return True
        if ti >= len(t_parts):
            return False
        if seg == "+":
            pi += 1
            ti += 1
            continue
        if seg != t_parts[ti]:
            return False
        pi += 1
        ti += 1
    return pi == len(p_parts) and ti == len(t_parts)


class MQTTClient:
    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_id: str = "jarvis",
    ) -> None:
        self._host = (host or "").strip() or "localhost"
        self._port = int(port)
        self._username = (username or "").strip() or None
        self._password = (password or "").strip() or None
        self._client_id = client_id or "jarvis"
        self._client: mqtt.Client | None = None
        self._lock = threading.RLock()
        self._callbacks: dict[str, list[Callable[[str, Any], None]]] = {}
        self._subscriptions: list[str] = []
        self._connected_flag = False
        self._reconnect_stop = threading.Event()
        self._reconnect_thread: threading.Thread | None = None

    def _on_connect(self, _client: Any, _userdata: Any, _flags: Any, rc: int) -> None:
        with self._lock:
            self._connected_flag = rc == 0
        if rc == 0:
            logger.info("MQTT connected to %s:%s", self._host, self._port)
            with self._lock:
                topics = list(self._subscriptions)
            for t in topics:
                try:
                    self._client.subscribe(t)
                    logger.info("MQTT resubscribed: %s", t)
                except Exception as e:
                    logger.warning("MQTT resubscribe failed %s: %s", t, e)
        else:
            logger.warning("MQTT connect failed rc=%s", rc)

    def _on_disconnect(self, _client: Any, _userdata: Any, rc: int) -> None:
        with self._lock:
            self._connected_flag = False
        logger.info("MQTT disconnected rc=%s", rc)

    def _on_message(self, _client: Any, _userdata: Any, msg: Any) -> None:
        topic = getattr(msg, "topic", "") or ""
        try:
            payload_raw = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload_raw = str(msg.payload)
        parsed: Any = payload_raw
        try:
            parsed = json.loads(payload_raw)
        except (json.JSONDecodeError, TypeError):
            pass
        with self._lock:
            patterns = list(self._callbacks.keys())
            cbs_snapshot = {k: list(v) for k, v in self._callbacks.items()}
        logger.info("MQTT message topic=%s payload_len=%s", topic, len(payload_raw))
        for pat in patterns:
            if _topic_match(pat, topic):
                for cb in cbs_snapshot.get(pat, []):
                    try:
                        cb(topic, parsed)
                    except Exception as e:
                        logger.exception("MQTT callback error: %s", e)

    def connect(self) -> bool:
        try:
            with self._lock:
                if self._client is not None:
                    try:
                        self._client.loop_stop()
                        self._client.disconnect()
                    except Exception:
                        pass
                try:
                    self._client = mqtt.Client(
                        mqtt.CallbackAPIVersion.VERSION1,  # type: ignore[attr-defined]
                        client_id=self._client_id,
                        clean_session=True,
                    )
                except (AttributeError, TypeError):
                    self._client = mqtt.Client(client_id=self._client_id, clean_session=True)
                if self._username:
                    self._client.username_pw_set(self._username, self._password or "")
                self._client.on_connect = self._on_connect
                self._client.on_disconnect = self._on_disconnect
                self._client.on_message = self._on_message
                self._client.connect(self._host, self._port, keepalive=60)
                self._client.loop_start()
            if self._reconnect_thread is None or not self._reconnect_thread.is_alive():
                self._reconnect_stop.clear()
                self._reconnect_thread = threading.Thread(target=self._reconnect_loop, name="mqtt-reconnect", daemon=True)
                self._reconnect_thread.start()
            time.sleep(0.3)
            with self._lock:
                return self._connected_flag
        except Exception as e:
            logger.exception("MQTT connect: %s", e)
            with self._lock:
                self._connected_flag = False
            return False

    def _reconnect_loop(self) -> None:
        while not self._reconnect_stop.wait(timeout=10.0):
            with self._lock:
                ok = self._connected_flag
                cli = self._client
            if ok and cli is not None:
                continue
            try:
                logger.info("MQTT reconnect attempt…")
                with self._lock:
                    if self._client is None:
                        continue
                    self._client.reconnect()
            except Exception as e:
                logger.warning("MQTT reconnect failed: %s", e)

    def disconnect(self) -> None:
        self._reconnect_stop.set()
        try:
            with self._lock:
                if self._client:
                    self._client.loop_stop()
                    self._client.disconnect()
                self._connected_flag = False
        except Exception as e:
            logger.warning("MQTT disconnect: %s", e)

    def publish(self, topic: str, payload: str | dict, qos: int = 0, retain: bool = False) -> dict[str, Any]:
        try:
            if not self.is_connected():
                return {"success": False, "data": None, "error": "MQTT not connected"}
            body = json.dumps(payload) if isinstance(payload, dict) else str(payload)
            logger.info("MQTT publish topic=%s qos=%s retain=%s bytes=%s", topic, qos, retain, len(body))
            with self._lock:
                if not self._client:
                    return {"success": False, "data": None, "error": "MQTT client not initialized"}
                pub = self._client.publish(topic, body, qos=qos, retain=retain)
            if hasattr(pub, "wait_for_publish"):
                pub.wait_for_publish(timeout=5.0)
            return {"success": True, "data": {"mid": getattr(pub, "mid", None)}, "error": None}
        except Exception as e:
            logger.exception("MQTT publish: %s", e)
            return {"success": False, "data": None, "error": str(e)}

    def subscribe(self, topic: str, callback: Callable[[str, Any], None]) -> dict[str, Any]:
        try:
            logger.info("MQTT subscribe topic=%s", topic)
            with self._lock:
                if topic not in self._callbacks:
                    self._callbacks[topic] = []
                self._callbacks[topic].append(callback)
                if topic not in self._subscriptions:
                    self._subscriptions.append(topic)
                cli = self._client
                connected = self._connected_flag
            if cli and connected:
                cli.subscribe(topic)
            return {"success": True, "data": {"topic": topic}, "error": None}
        except Exception as e:
            logger.exception("MQTT subscribe: %s", e)
            return {"success": False, "data": None, "error": str(e)}

    def unsubscribe(self, topic: str) -> None:
        try:
            with self._lock:
                self._callbacks.pop(topic, None)
                if topic in self._subscriptions:
                    self._subscriptions.remove(topic)
                cli = self._client
            if cli:
                cli.unsubscribe(topic)
            logger.info("MQTT unsubscribe topic=%s", topic)
        except Exception as e:
            logger.warning("MQTT unsubscribe: %s", e)

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected_flag and self._client is not None

    def get_subscriptions(self) -> list[str]:
        with self._lock:
            return list(self._subscriptions)


if __name__ == "__main__":
    m = MQTTClient("127.0.0.1", 1883)
    print("connect:", m.connect())
    print(m.publish("jarvis/test", {"ok": True}))
