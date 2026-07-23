"""Registro e seleção de drivers."""
from typing import Any

from .base import ReaderDriver
from .evo import EvoDriver

_DRIVERS: dict[str, ReaderDriver] = {
    "evo": EvoDriver(),
}


def get_driver(driver_id: str) -> ReaderDriver:
    normalized = str(driver_id or "evo").strip().lower()
    try:
        return _DRIVERS[normalized]
    except KeyError as error:
        raise RuntimeError(
            f"Driver '{normalized}' ainda não implementado nesta versão"
        ) from error


def execute_reader_command(
    reader: dict[str, Any],
    command: str,
    request_timeout: int,
    **kwargs: Any,
) -> dict[str, Any]:
    driver = get_driver(str(reader.get("driver", "evo")))
    return driver.execute(reader, command, request_timeout, **kwargs)
