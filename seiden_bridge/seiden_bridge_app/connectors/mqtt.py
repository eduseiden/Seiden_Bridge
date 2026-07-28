"""Conector MQTT de entrada do Seiden Bridge 0.8.2.2."""
from __future__ import annotations

import json
import logging
import ssl
from typing import Any, Callable

import paho.mqtt.client as mqtt

from .base import BaseConnector

LOGGER = logging.getLogger("seiden_bridge")


class MqttConnector(BaseConnector):
    """Assina tópicos MQTT e entrega mensagens ao núcleo do Bridge."""

    connector_id = "mqtt"

    def execute(
        self,
        connection: dict[str, Any],
        command: str,
        request_timeout: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise RuntimeError("O conector MQTT opera por assinatura, não por polling")

    def start(
        self,
        connection: dict[str, Any],
        on_event: Callable[[dict[str, Any], str, Any, bytes], None],
    ) -> mqtt.Client:
        endpoint = connection.get("endpoint") or {}
        host = endpoint.get("host") or connection.get("host")
        port = int(endpoint.get("port", 1883))
        keepalive = int(endpoint.get("keepalive", 60))
        client_id = str(connection.get("client_id") or f"seiden_bridge_{connection['id']}")
        clean_session = bool(connection.get("clean_session", True))

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=clean_session,
        )

        username = connection.get("username")
        password = connection.get("password")
        if username:
            client.username_pw_set(str(username), None if password is None else str(password))

        tls = connection.get("tls") or {}
        if tls.get("enabled", False):
            client.tls_set(
                ca_certs=tls.get("ca_cert"),
                certfile=tls.get("client_cert"),
                keyfile=tls.get("client_key"),
                cert_reqs=ssl.CERT_REQUIRED if tls.get("verify", True) else ssl.CERT_NONE,
            )
            client.tls_insecure_set(not tls.get("verify", True))

        subscriptions = connection.get("subscriptions") or []

        def reason_code_failed(reason_code: Any) -> bool:
            """Compatibiliza códigos de retorno entre Paho MQTT 1.x e 2.x."""
            is_failure = getattr(reason_code, "is_failure", None)
            if is_failure is not None:
                return bool(is_failure)

            value = getattr(reason_code, "value", reason_code)
            try:
                return int(value) != 0
            except (TypeError, ValueError):
                LOGGER.warning(
                    "[MQTT][%s] Código MQTT não reconhecido: %r",
                    connection["name"],
                    reason_code,
                )
                return True

        def on_connect(client_obj: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any) -> None:
            if reason_code_failed(reason_code):
                LOGGER.error("[MQTT][%s] Falha ao conectar: %s", connection["name"], reason_code)
                return
            LOGGER.info("[MQTT][%s] Conectado a %s:%s", connection["name"], host, port)
            for subscription in subscriptions:
                topic = str(subscription["topic"])
                qos = int(subscription.get("qos", 0))
                client_obj.subscribe(topic, qos=qos)
                LOGGER.info("[MQTT][%s] Assinando %s (QoS %d)", connection["name"], topic, qos)

        def on_disconnect(client_obj: mqtt.Client, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any) -> None:
            if reason_code_failed(reason_code):
                LOGGER.warning("[MQTT][%s] Desconectado inesperadamente: %s", connection["name"], reason_code)

        def on_message(client_obj: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
            raw = bytes(message.payload)
            try:
                decoded = raw.decode("utf-8")
                try:
                    payload: Any = json.loads(decoded)
                except json.JSONDecodeError:
                    payload = decoded
                on_event(connection, message.topic, payload, raw)
            except Exception:
                LOGGER.exception("[MQTT][%s] Falha ao processar tópico %s", connection["name"], message.topic)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        client.reconnect_delay_set(min_delay=1, max_delay=int(connection.get("max_retry_interval", 300)))
        client.connect_async(str(host), port=port, keepalive=keepalive)
        client.loop_start()
        return client
