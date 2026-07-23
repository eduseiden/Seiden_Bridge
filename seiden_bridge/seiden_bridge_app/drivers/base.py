"""Contrato comum para drivers de leitores."""
from abc import ABC, abstractmethod
from typing import Any


class ReaderDriver(ABC):
    """Interface mínima implementada por todo driver de leitor."""

    driver_id: str

    @abstractmethod
    def execute(
        self,
        reader: dict[str, Any],
        command: str,
        request_timeout: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Executa um comando e devolve uma resposta normalizada."""
        raise NotImplementedError
