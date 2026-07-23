"""Modelo canônico de eventos operacionais do Seiden Bridge."""
from datetime import datetime
from typing import Any
from uuid import uuid4

EVENT_SCHEMA_VERSION = "1.0"


def create_presence_event(
    *,
    reader: dict[str, Any],
    record: dict[str, Any],
    operational: dict[str, Any],
) -> dict[str, Any]:
    """Cria o envelope estável consumido por HA, FLOW e integrações futuras."""
    event_time = record.get("time") or datetime.now().isoformat(timespec="seconds")
    user_id = str(record.get("enrollid"))
    user_name = record.get("name") or user_id

    payload = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "event_type": "person_authenticated",
        "source": "seiden_bridge",
        "timestamp": event_time,
        "reader": {
            "id": operational["reader_id"],
            "name": reader["name"],
            "ip": reader["ip"],
            "driver": reader.get("driver", "evo"),
            "direction": reader["direction"],
        },
        "person": {
            "id": user_id,
            "name": user_name,
            "authorized": True,
        },
        "operation": operational,
        "raw": record,
    }

    # Campos planos preservados para compatibilidade com automações 0.6.0.
    payload.update(
        {
            "driver": reader.get("driver", "evo"),
            "reader_name": reader["name"],
            "reader_ip": reader["ip"],
            "direction": reader["direction"],
            "action": operational["action"],
            "user_id": user_id,
            "user_name": user_name,
            "authorized": True,
            "event_code": record.get("event"),
            "mode": record.get("mode"),
            "inout": record.get("inout"),
            "time": event_time,
            "photo_url": operational.get("photo_url"),
            "photo_filename": operational.get("photo_filename"),
            "was_already_inside": operational["was_already_inside"],
            "exit_without_entry": operational["exit_without_entry"],
            "is_first_entry": operational["is_first_entry"],
            "is_last_exit": operational["is_last_exit"],
            "people_inside_count": operational["people_inside_count"],
            "building_occupied": operational["building_occupied"],
            "people_inside": operational["people_inside"],
            "first_entry_today": operational.get("first_entry_today"),
            "last_exit_today": operational.get("last_exit_today"),
        }
    )
    return payload
