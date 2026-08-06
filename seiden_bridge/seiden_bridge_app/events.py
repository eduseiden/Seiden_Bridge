"""Modelo canônico de eventos do Seiden Bridge 0.13.0."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

EVENT_SCHEMA_VERSION = "2.0"


def utc_now() -> str:
    """Retorna timestamp canônico UTC no formato ISO 8601 com Z."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_timestamp(value: Any, source_timezone: str = "UTC") -> str:
    """Converte timestamps com ou sem offset para UTC.

    Valores sem offset são interpretados no fuso configurado da operação.
    """
    if value in (None, ""):
        return utc_now()
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return utc_now()
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
        except Exception:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def create_presence_event(*, reader: dict[str, Any], record: dict[str, Any], operational: dict[str, Any], source_timezone: str = "UTC") -> dict[str, Any]:
    event_time = normalize_timestamp(record.get("time"), source_timezone)
    user_id = str(record.get("enrollid"))
    user_name = record.get("name") or user_id
    connection_id = reader.get("connection_id") or operational["reader_id"]
    direction = reader.get("direction")
    interaction_type = reader.get("interaction_type", "passage")
    payload = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "event_type": "person_authenticated",
        "source": "seiden_bridge",
        "timestamp": event_time,
        "connection": {
            "id": connection_id,
            "name": reader["name"],
            "type": "device",
            "connector": reader.get("connector", "evo"),
            "endpoint": {"host": reader.get("host") or reader.get("ip")},
        },
        "context": {"interaction_type": interaction_type, "direction": direction},
        "subject": {"type": "person", "external_id": user_id, "name": user_name},
        "result": "authorized",
        "operation": operational,
        "raw": record,
        "reader": {
            "id": connection_id,
            "name": reader["name"],
            "ip": reader.get("ip"),
            "driver": reader.get("connector", "evo"),
            "direction": direction,
        },
        "person": {"id": user_id, "name": user_name, "authorized": True},
    }
    payload.update({
        "connection_id": connection_id,
        "connector": reader.get("connector", "evo"),
        "interaction_type": interaction_type,
        "reader_id": connection_id,
        "driver": reader.get("connector", "evo"),
        "reader_name": reader["name"],
        "reader_ip": reader.get("ip"),
        "direction": direction,
        "user_id": user_id,
        "user_name": user_name,
        "authorized": True,
        "time": event_time,
        **operational,
    })
    return payload


def create_mqtt_event(
    *,
    connection: dict[str, Any],
    topic: str,
    payload: Any,
    environment_source: dict[str, Any] | None = None,
    measurements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cria evento MQTT e, quando aplicável, acrescenta identidade ambiental.

    O payload original permanece em ``data`` e ``raw`` para compatibilidade.
    A identidade amigável e as medições normalizadas são campos adicionais.
    """
    event_type = str(connection.get("event_type") or "mqtt.message_received")
    timestamp = utc_now()
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "event_type": event_type,
        "source": "seiden_bridge",
        "timestamp": timestamp,
        "connection": {
            "id": connection["id"],
            "name": connection["name"],
            "type": "message_broker",
            "connector": "mqtt",
            "endpoint": {
                "host": connection.get("host"),
                "port": connection.get("port", 1883),
            },
        },
        "context": connection.get("context") or {"interaction_type": "message", "direction": None},
        "data": payload,
        "raw": {"topic": topic, "payload": payload},
        "connection_id": connection["id"],
        "connector": "mqtt",
        "topic": topic,
    }

    if environment_source is None:
        return event

    normalized = measurements or {}
    source_id = environment_source["id"]
    source_name = environment_source["name"]
    environment = {
        "source_id": source_id,
        "source_name": source_name,
        "description": environment_source.get("description"),
        "location_id": environment_source.get("location_id"),
        "location_name": environment_source.get("location_name"),
        "asset_id": environment_source.get("asset_id"),
        "asset_name": environment_source.get("asset_name"),
        "profile_id": environment_source.get("profile_id", "custom"),
        "measurements": normalized,
    }
    event["environment"] = environment
    event["source_id"] = source_id
    event["source_name"] = source_name
    event["description"] = environment_source.get("description")
    event["location_id"] = environment_source.get("location_id")
    event["location_name"] = environment_source.get("location_name")
    event["asset_id"] = environment_source.get("asset_id")
    event["asset_name"] = environment_source.get("asset_name")
    event["profile_id"] = environment_source.get("profile_id", "custom")
    event.update(normalized)
    return event
