"""Conector EVO — primeira implementação da Connector Foundation."""
import logging
from typing import Any
import requests
from .base import BaseConnector
LOGGER = logging.getLogger("seiden_bridge")

class EvoConnector(BaseConnector):
    connector_id = "evo"
    def execute(self, connection: dict[str, Any], command: str, request_timeout: int, **kwargs: Any) -> dict[str, Any]:
        payload = {"password": connection["password"], "cmd": command}
        payload.update(kwargs)
        host = connection.get("host") or connection.get("ip")
        scheme = (connection.get("endpoint") or {}).get("scheme", "http")
        path = (connection.get("endpoint") or {}).get("path", "/api")
        url = f"{scheme}://{host}{path}"
        LOGGER.debug("[CONNECTION][%s] Requisição para %s: %s", connection["name"], url, {k:v for k,v in payload.items() if k != "password"})
        response = requests.post(url, json=payload, timeout=request_timeout)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("A API do EVO retornou uma resposta inválida")
        return data
