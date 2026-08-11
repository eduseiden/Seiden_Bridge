"""Conector Redfish para telemetria de infraestrutura do Seiden Bridge."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import requests

from .base import BaseConnector

LOGGER = logging.getLogger("seiden_bridge")

DEFAULT_SENSOR_IDS = (
    "CPU1Temp",
    "IntakeTemp",
    "ExhaustTemp",
    "AmbientTemp",
    "CPUFan1",
    "CPUFan2",
    "TotalPower",
)


class RedfishConnector(BaseConnector):
    """Lê sensores Redfish diretamente, sem depender do Home Assistant."""

    connector_id = "redfish"

    def __init__(self) -> None:
        self._sensor_paths: dict[str, list[str]] = {}
        self._asset_meta: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _base_url(connection: dict[str, Any]) -> str:
        endpoint = connection.get("endpoint") or {}
        scheme = str(endpoint.get("scheme") or "https").strip().lower()
        host = str(endpoint.get("host") or connection.get("host") or "").strip()
        port = endpoint.get("port")
        path = str(endpoint.get("path") or "/redfish/v1").strip() or "/redfish/v1"
        if not path.startswith("/"):
            path = "/" + path
        authority = host if not port else f"{host}:{int(port)}"
        return f"{scheme}://{authority}{path.rstrip('/')}/"

    @staticmethod
    def _session(connection: dict[str, Any]) -> requests.Session:
        session = requests.Session()
        username = connection.get("username")
        password = connection.get("password")
        if username:
            session.auth = (str(username), "" if password is None else str(password))
        return session

    @staticmethod
    def _verify_tls(connection: dict[str, Any]) -> bool:
        endpoint = connection.get("endpoint") or {}
        return bool(endpoint.get("verify_tls", connection.get("verify_tls", True)))

    def _get_json(
        self,
        session: requests.Session,
        base_url: str,
        path: str,
        timeout: int,
        verify_tls: bool,
    ) -> dict[str, Any]:
        url = urljoin(base_url, str(path))
        response = session.get(url, timeout=timeout, verify=verify_tls)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Resposta Redfish inválida em {path}")
        return payload

    @staticmethod
    def _member_paths(payload: dict[str, Any]) -> list[str]:
        members = payload.get("Members") or []
        paths: list[str] = []
        for item in members:
            if isinstance(item, dict) and item.get("@odata.id"):
                paths.append(str(item["@odata.id"]))
        return paths

    @staticmethod
    def _selected_sensor_ids(connection: dict[str, Any]) -> set[str]:
        configured = connection.get("sensor_ids")
        if isinstance(configured, str):
            items = [part.strip() for part in configured.replace(";", "\n").replace(",", "\n").splitlines()]
            selected = {item for item in items if item}
            return selected or set(DEFAULT_SENSOR_IDS)
        if isinstance(configured, list):
            selected = {str(item).strip() for item in configured if str(item).strip()}
            return selected or set(DEFAULT_SENSOR_IDS)
        return set(DEFAULT_SENSOR_IDS)

    def _discover(
        self,
        connection: dict[str, Any],
        session: requests.Session,
        base_url: str,
        timeout: int,
        verify_tls: bool,
    ) -> tuple[list[str], dict[str, Any]]:
        root = self._get_json(session, base_url, "/redfish/v1/", timeout, verify_tls)
        systems_path = ((root.get("Systems") or {}).get("@odata.id"))
        chassis_path = ((root.get("Chassis") or {}).get("@odata.id"))
        if not chassis_path:
            raise RuntimeError("Service Root Redfish não expõe coleção Chassis")

        system_id = None
        system_name = None
        if systems_path:
            systems = self._get_json(session, base_url, systems_path, timeout, verify_tls)
            system_members = self._member_paths(systems)
            if system_members:
                system = self._get_json(session, base_url, system_members[0], timeout, verify_tls)
                system_id = system.get("Id") or system_members[0].rstrip("/").split("/")[-1]
                system_name = system.get("Name") or system.get("HostName") or system_id

        chassis = self._get_json(session, base_url, chassis_path, timeout, verify_tls)
        chassis_members = self._member_paths(chassis)
        if not chassis_members:
            raise RuntimeError("Nenhum chassis encontrado no endpoint Redfish")

        selected_ids = self._selected_sensor_ids(connection)
        sensor_paths: list[str] = []
        chassis_ids: list[str] = []
        for chassis_member in chassis_members:
            chassis_obj = self._get_json(session, base_url, chassis_member, timeout, verify_tls)
            chassis_id = str(chassis_obj.get("Id") or chassis_member.rstrip("/").split("/")[-1])
            chassis_ids.append(chassis_id)
            sensors_path = ((chassis_obj.get("Sensors") or {}).get("@odata.id"))
            if not sensors_path:
                continue
            sensors = self._get_json(session, base_url, sensors_path, timeout, verify_tls)
            for sensor_path in self._member_paths(sensors):
                sensor_id = sensor_path.rstrip("/").split("/")[-1]
                if sensor_id in selected_ids:
                    sensor_paths.append(sensor_path)

        if not sensor_paths:
            raise RuntimeError(
                "Nenhum dos sensores Redfish configurados foi encontrado: "
                + ", ".join(sorted(selected_ids))
            )

        meta = {
            "system_id": system_id,
            "system_name": system_name,
            "chassis_ids": chassis_ids,
        }
        return sensor_paths, meta

    @staticmethod
    def _normalize_sensor(sensor: dict[str, Any]) -> dict[str, Any]:
        thresholds: dict[str, Any] = {}
        raw_thresholds = sensor.get("Thresholds") or {}
        if isinstance(raw_thresholds, dict):
            for name, value in raw_thresholds.items():
                if isinstance(value, dict) and value.get("Reading") is not None:
                    thresholds[name] = {
                        "reading": value.get("Reading"),
                        "activation": value.get("Activation"),
                        "dwell_time": value.get("DwellTime"),
                        "hysteresis_duration": value.get("HysteresisDuration"),
                        "hysteresis_reading": value.get("HysteresisReading"),
                    }

        status = sensor.get("Status") if isinstance(sensor.get("Status"), dict) else {}
        related = []
        for item in sensor.get("RelatedItem") or []:
            if isinstance(item, dict) and item.get("@odata.id"):
                related.append(str(item["@odata.id"]))

        return {
            "id": sensor.get("Id"),
            "name": sensor.get("Name"),
            "physical_context": sensor.get("PhysicalContext"),
            "reading": sensor.get("Reading"),
            "units": sensor.get("ReadingUnits"),
            "range_min": sensor.get("ReadingRangeMin"),
            "range_max": sensor.get("ReadingRangeMax"),
            "health": status.get("Health"),
            "state": status.get("State"),
            "thresholds": thresholds,
            "related_items": related,
            "odata_id": sensor.get("@odata.id"),
        }

    def execute(
        self,
        connection: dict[str, Any],
        command: str,
        request_timeout: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if command not in {"snapshot", "get_sensors"}:
            raise RuntimeError(f"Comando Redfish não suportado: {command}")

        connection_id = str(connection.get("id") or "redfish")
        base_url = self._base_url(connection)
        session = self._session(connection)
        verify_tls = self._verify_tls(connection)

        sensor_paths = self._sensor_paths.get(connection_id)
        meta = self._asset_meta.get(connection_id)
        if not sensor_paths or meta is None:
            sensor_paths, meta = self._discover(
                connection, session, base_url, request_timeout, verify_tls
            )
            self._sensor_paths[connection_id] = sensor_paths
            self._asset_meta[connection_id] = meta
            LOGGER.info(
                "[REDFISH][%s] Descobertos %d sensores selecionados (%s)",
                connection.get("name", connection_id),
                len(sensor_paths),
                ", ".join(path.rstrip("/").split("/")[-1] for path in sensor_paths),
            )

        sensors: list[dict[str, Any]] = []
        try:
            for sensor_path in sensor_paths:
                sensor = self._get_json(
                    session, base_url, sensor_path, request_timeout, verify_tls
                )
                sensors.append(self._normalize_sensor(sensor))
        except requests.HTTPError as error:
            # Pode ter ocorrido mudança de inventário após reboot/troca de hardware.
            if error.response is not None and error.response.status_code == 404:
                self._sensor_paths.pop(connection_id, None)
                self._asset_meta.pop(connection_id, None)
            raise

        return {
            "result": True,
            "asset": meta,
            "sensors": sensors,
            "base_url": base_url.rstrip("/"),
        }
