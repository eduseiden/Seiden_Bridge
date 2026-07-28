"""Registro e seleção de conectores."""
from typing import Any
from .base import BaseConnector
from .evo import EvoConnector
from .mqtt import MqttConnector
_CONNECTORS: dict[str, BaseConnector] = {"evo": EvoConnector(), "mqtt": MqttConnector()}

def get_connector(connector_id: str) -> BaseConnector:
    normalized = str(connector_id or "evo").strip().lower()
    try:
        return _CONNECTORS[normalized]
    except KeyError as error:
        raise RuntimeError(f"Conector '{normalized}' ainda não implementado nesta versão") from error

def execute_connection_command(connection: dict[str, Any], command: str, request_timeout: int, **kwargs: Any) -> dict[str, Any]:
    connector = get_connector(str(connection.get("connector", connection.get("driver", "evo"))))
    return connector.execute(connection, command, request_timeout, **kwargs)
