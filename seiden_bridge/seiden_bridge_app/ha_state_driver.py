"""Home Assistant State Driver for Seiden Bridge 0.15.0.

Event-driven integration using the Home Assistant WebSocket API through the
Supervisor proxy. There is no polling loop. The driver registers Home Assistant
state triggers only for explicitly configured entities, so unrelated state
changes are filtered inside Home Assistant before reaching the Bridge.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed

LOGGER = logging.getLogger("seiden_bridge")
WS_URL = "ws://supervisor/core/websocket"


def _parse_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,;]+", value) if item.strip()]
    return []


def normalize_ha_state_driver(config: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(config.get("ha_state_driver_enabled", False))
    entities = list(dict.fromkeys(_parse_lines(config.get("ha_state_driver_entities"))))
    ignore_states = {
        item.lower() for item in _parse_lines(config.get("ha_state_driver_ignore_states"))
    }
    if not ignore_states:
        ignore_states = {"unknown", "unavailable"}

    if enabled and not entities:
        LOGGER.warning(
            "[HA STATE] Driver habilitado sem entidades; desabilitado por segurança."
        )
        enabled = False

    return {
        "enabled": enabled,
        "entities": entities,
        "entity_set": set(entities),
        "ignore_states": ignore_states,
    }


def _state_obj(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _process_trigger_message(
    message: dict[str, Any],
    driver: dict[str, Any],
    emit: Callable[[str, Any, Any, str | None, dict[str, Any]], None],
) -> None:
    """Processa somente mensagens do subscribe_trigger já filtradas pelo HA."""
    if message.get("type") != "event":
        return
    event = message.get("event")
    if not isinstance(event, dict):
        return
    variables = event.get("variables")
    if not isinstance(variables, dict):
        return
    trigger = variables.get("trigger")
    if not isinstance(trigger, dict) or trigger.get("platform") != "state":
        return

    entity_id = str(trigger.get("entity_id") or "").strip()
    if not entity_id or entity_id not in driver["entity_set"]:
        return

    old_obj = _state_obj(trigger.get("from_state"))
    new_obj = _state_obj(trigger.get("to_state"))
    if old_obj is None or new_obj is None:
        LOGGER.debug("[HA STATE] %s lifecycle ignorado.", entity_id)
        return

    previous_state = old_obj.get("state")
    current_state = new_obj.get("state")
    if previous_state is None or current_state is None or previous_state == current_state:
        return

    if str(previous_state).lower() in driver["ignore_states"] or str(current_state).lower() in driver["ignore_states"]:
        LOGGER.debug(
            "[HA STATE] %s transição de disponibilidade ignorada: %s → %s",
            entity_id,
            previous_state,
            current_state,
        )
        return

    attributes = new_obj.get("attributes") if isinstance(new_obj.get("attributes"), dict) else {}
    friendly_name = attributes.get("friendly_name") if isinstance(attributes, dict) else None
    context = new_obj.get("context") if isinstance(new_obj.get("context"), dict) else {}
    emit(entity_id, previous_state, current_state, friendly_name, context)


def _run_forever(
    driver: dict[str, Any],
    supervisor_token: str,
    emit: Callable[[str, Any, Any, str | None, dict[str, Any]], None],
) -> None:
    retry = 1
    while True:
        try:
            LOGGER.info(
                "[HA STATE] conectando ao Home Assistant WebSocket | entidades=%d",
                len(driver["entities"]),
            )
            with connect(
                WS_URL,
                open_timeout=10,
                close_timeout=5,
                ping_interval=30,
                ping_timeout=20,
                max_size=2 * 1024 * 1024,
            ) as ws:
                hello = json.loads(ws.recv())
                if hello.get("type") != "auth_required":
                    raise RuntimeError(f"handshake inesperado: {hello.get('type')}")

                ws.send(json.dumps({"type": "auth", "access_token": supervisor_token}))
                auth = json.loads(ws.recv())
                if auth.get("type") != "auth_ok":
                    raise RuntimeError(f"autenticação recusada: {auth.get('message') or auth.get('type')}")

                triggers = [
                    {"platform": "state", "entity_id": entity_id}
                    for entity_id in driver["entities"]
                ]
                ws.send(json.dumps({"id": 1, "type": "subscribe_trigger", "trigger": triggers}))
                subscribed = json.loads(ws.recv())
                if subscribed.get("type") != "result" or not subscribed.get("success"):
                    raise RuntimeError(f"subscribe_trigger recusado: {subscribed}")

                retry = 1
                LOGGER.info(
                    "[HA STATE] conectado; filtros de estado registrados no HA para %d entidade(s).",
                    len(driver["entities"]),
                )
                for raw in ws:
                    try:
                        message = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    _process_trigger_message(message, driver, emit)

        except (ConnectionClosed, OSError, RuntimeError, TimeoutError) as exc:
            LOGGER.warning("[HA STATE] conexão interrompida: %s | retry=%ss", exc, retry)
        except Exception as exc:  # defensive: driver must never terminate Bridge
            LOGGER.exception("[HA STATE] erro inesperado; driver será reconectado: %s", exc)

        time.sleep(retry)
        retry = min(retry * 2, 60)


def start_ha_state_driver(
    *,
    config: dict[str, Any],
    supervisor_token: str,
    emit: Callable[[str, Any, Any, str | None, dict[str, Any]], None],
) -> threading.Thread | None:
    driver = normalize_ha_state_driver(config)
    if not driver["enabled"]:
        LOGGER.info("[HA STATE] driver inativo.")
        return None

    thread = threading.Thread(
        target=_run_forever,
        args=(driver, supervisor_token, emit),
        name="seiden-ha-state-driver",
        daemon=True,
    )
    thread.start()
    LOGGER.info(
        "[HA STATE] driver iniciado | entidades=%s",
        ", ".join(driver["entities"]),
    )
    return thread
