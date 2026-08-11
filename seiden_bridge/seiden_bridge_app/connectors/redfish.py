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
    """Lê sensores Redfish diretamente, sem depender do Home Assistant.

    A descoberta é multi-system e agnóstica de fabricante. Cada membro da
    coleção ``Systems`` é tratado como um ativo independente e associado aos
    chassis anunciados pelo próprio System. Quando o vínculo não está no
    System, o conector tenta inferi-lo a partir de ``Chassis/Links``.
    """

    connector_id = "redfish"

    def __init__(self) -> None:
        self._assets: dict[str, list[dict[str, Any]]] = {}

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
    def _link_paths(value: Any) -> list[str]:
        """Extrai ``@odata.id`` de um link Redfish unitário ou coleção."""
        if isinstance(value, dict):
            if value.get("@odata.id"):
                return [str(value["@odata.id"])]
            return []
        if isinstance(value, list):
            paths: list[str] = []
            for item in value:
                if isinstance(item, dict) and item.get("@odata.id"):
                    paths.append(str(item["@odata.id"]))
            return paths
        return []

    @staticmethod
    def _selected_sensor_ids(connection: dict[str, Any]) -> set[str]:
        configured = connection.get("sensor_ids")
        if isinstance(configured, str):
            items = [
                part.strip()
                for part in configured.replace(";", "\n").replace(",", "\n").splitlines()
            ]
            selected = {item for item in items if item}
            return selected or set(DEFAULT_SENSOR_IDS)
        if isinstance(configured, list):
            selected = {str(item).strip() for item in configured if str(item).strip()}
            return selected or set(DEFAULT_SENSOR_IDS)
        return set(DEFAULT_SENSOR_IDS)

    @staticmethod
    def _system_identity(system: dict[str, Any], system_path: str) -> tuple[str, str]:
        system_id = str(system.get("Id") or system_path.rstrip("/").split("/")[-1])
        system_name = str(system.get("Name") or system.get("HostName") or system_id)
        return system_id, system_name

    def _discover(
        self,
        connection: dict[str, Any],
        session: requests.Session,
        base_url: str,
        timeout: int,
        verify_tls: bool,
    ) -> list[dict[str, Any]]:
        root = self._get_json(session, base_url, "/redfish/v1/", timeout, verify_tls)
        systems_path = ((root.get("Systems") or {}).get("@odata.id"))
        chassis_path = ((root.get("Chassis") or {}).get("@odata.id"))
        if not systems_path:
            raise RuntimeError("Service Root Redfish não expõe coleção Systems")
        if not chassis_path:
            raise RuntimeError("Service Root Redfish não expõe coleção Chassis")

        systems = self._get_json(session, base_url, systems_path, timeout, verify_tls)
        system_members = self._member_paths(systems)
        if not system_members:
            raise RuntimeError("Nenhum System encontrado no endpoint Redfish")

        chassis_collection = self._get_json(session, base_url, chassis_path, timeout, verify_tls)
        chassis_members = self._member_paths(chassis_collection)
        chassis_objects: dict[str, dict[str, Any]] = {}
        for chassis_member in chassis_members:
            chassis_objects[chassis_member] = self._get_json(
                session, base_url, chassis_member, timeout, verify_tls
            )

        selected_ids = self._selected_sensor_ids(connection)
        assets: list[dict[str, Any]] = []

        for system_path in system_members:
            system = self._get_json(session, base_url, system_path, timeout, verify_tls)
            system_id, system_name = self._system_identity(system, system_path)

            # Preferência: vínculo declarado pelo próprio ComputerSystem.
            links = system.get("Links") if isinstance(system.get("Links"), dict) else {}
            linked_chassis = self._link_paths(links.get("Chassis"))

            # Fallback agnóstico: procurar chassis cujo Links.ComputerSystems
            # contenha o System atual. Alguns fabricantes modelam só esse lado.
            if not linked_chassis:
                for candidate_path, candidate in chassis_objects.items():
                    candidate_links = (
                        candidate.get("Links")
                        if isinstance(candidate.get("Links"), dict)
                        else {}
                    )
                    computer_systems = self._link_paths(candidate_links.get("ComputerSystems"))
                    if system_path in computer_systems:
                        linked_chassis.append(candidate_path)

            # Último fallback para implementações simples com exatamente um
            # System e um Chassis. Nunca associa todos os chassis a todos os
            # sistemas em ambientes multi-system.
            if not linked_chassis and len(system_members) == 1 and len(chassis_members) == 1:
                linked_chassis = list(chassis_members)

            sensor_paths: list[str] = []
            chassis_ids: list[str] = []

            for chassis_member in linked_chassis:
                chassis_obj = chassis_objects.get(chassis_member)
                if chassis_obj is None:
                    chassis_obj = self._get_json(
                        session, base_url, chassis_member, timeout, verify_tls
                    )
                    chassis_objects[chassis_member] = chassis_obj

                chassis_id = str(
                    chassis_obj.get("Id") or chassis_member.rstrip("/").split("/")[-1]
                )
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
                LOGGER.warning(
                    "[REDFISH][%s] System %s ignorado: nenhum sensor selecionado encontrado.",
                    connection.get("name", connection.get("id", "redfish")),
                    system_id,
                )
                continue

            assets.append(
                {
                    "meta": {
                        "system_id": system_id,
                        "system_name": system_name,
                        "chassis_ids": chassis_ids,
                    },
                    "sensor_paths": sensor_paths,
                }
            )

        if not assets:
            raise RuntimeError(
                "Nenhum System Redfish com sensores configurados foi encontrado: "
                + ", ".join(sorted(selected_ids))
            )

        return assets

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

        assets = self._assets.get(connection_id)
        if not assets:
            assets = self._discover(
                connection, session, base_url, request_timeout, verify_tls
            )
            self._assets[connection_id] = assets
            LOGGER.info(
                "[REDFISH][%s] Descobertos %d Systems com %d sensores selecionados.",
                connection.get("name", connection_id),
                len(assets),
                sum(len(item["sensor_paths"]) for item in assets),
            )

        snapshots: list[dict[str, Any]] = []
        try:
            for asset in assets:
                sensors: list[dict[str, Any]] = []
                for sensor_path in asset["sensor_paths"]:
                    sensor = self._get_json(
                        session, base_url, sensor_path, request_timeout, verify_tls
                    )
                    sensors.append(self._normalize_sensor(sensor))
                snapshots.append({"asset": asset["meta"], "sensors": sensors})
        except requests.HTTPError as error:
            # Pode ter ocorrido mudança de inventário após reboot/troca de hardware.
            if error.response is not None and error.response.status_code == 404:
                self._assets.pop(connection_id, None)
            raise

        # Campos asset/sensors são mantidos para compatibilidade com qualquer
        # consumidor interno antigo; multi-system usa ``snapshots``.
        first = snapshots[0] if snapshots else {"asset": {}, "sensors": []}
        return {
            "result": True,
            "asset": first["asset"],
            "sensors": first["sensors"],
            "snapshots": snapshots,
            "base_url": base_url.rstrip("/"),
        }
