"""Driver para leitores EVO."""
import logging
from typing import Any

import requests

from .base import ReaderDriver

LOGGER = logging.getLogger("seiden_bridge")


class EvoDriver(ReaderDriver):
    """Implementação do protocolo HTTP/JSON dos leitores EVO."""

    driver_id = "evo"

    def execute(
        self,
        reader: dict[str, Any],
        command: str,
        request_timeout: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = {"password": reader["password"], "cmd": command}
        payload.update(kwargs)
        url = f"http://{reader['ip']}/api"

        LOGGER.debug(
            "[READER][%s] Requisição para %s: %s",
            reader["name"],
            url,
            {key: value for key, value in payload.items() if key != "password"},
        )

        response = requests.post(url, json=payload, timeout=request_timeout)
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict):
            raise ValueError("A API do EVO retornou uma resposta inválida")

        LOGGER.debug(
            "[READER][%s] Resposta do comando %s: %s",
            reader["name"],
            command,
            data,
        )
        return data
