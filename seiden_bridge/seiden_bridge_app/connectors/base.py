"""Contrato comum dos conectores do Seiden Bridge."""
from abc import ABC, abstractmethod
from typing import Any

class BaseConnector(ABC):
    connector_id: str
    @abstractmethod
    def execute(self, connection: dict[str, Any], command: str, request_timeout: int, **kwargs: Any) -> dict[str, Any]:
        """Executa uma operação na fonte externa e devolve resposta normalizada."""
        raise NotImplementedError
