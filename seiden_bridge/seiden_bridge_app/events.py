"""Modelo canônico de eventos do Seiden Bridge 0.7.0."""
from datetime import datetime
from typing import Any
from uuid import uuid4
EVENT_SCHEMA_VERSION = "2.0"

def create_presence_event(*, reader: dict[str, Any], record: dict[str, Any], operational: dict[str, Any]) -> dict[str, Any]:
    event_time = record.get("time") or datetime.now().isoformat(timespec="seconds")
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
            "id": connection_id, "name": reader["name"],
            "type": "device", "connector": reader.get("connector", "evo"),
            "endpoint": {"host": reader.get("host") or reader.get("ip")},
        },
        "context": {"interaction_type": interaction_type, "direction": direction},
        "subject": {"type": "person", "external_id": user_id, "name": user_name},
        "result": "authorized",
        "operation": operational,
        "raw": record,
        # Objetos 0.6.x preservados durante a transição.
        "reader": {
            "id": connection_id, "name": reader["name"], "ip": reader.get("ip"),
            "driver": reader.get("connector", "evo"), "direction": direction,
        },
        "person": {"id": user_id, "name": user_name, "authorized": True},
    }
    payload.update({
        "connection_id": connection_id, "connector": reader.get("connector", "evo"),
        "interaction_type": interaction_type, "reader_id": connection_id,
        "driver": reader.get("connector", "evo"), "reader_name": reader["name"],
        "reader_ip": reader.get("ip"), "direction": direction,
        "user_id": user_id, "user_name": user_name, "authorized": True,
        "time": event_time, **operational,
    })
    return payload
