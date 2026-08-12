"""Conector Linux via SSH para telemetria de infraestrutura do Seiden Bridge."""
from __future__ import annotations

import base64
import hashlib
import logging
import shlex
from pathlib import Path
from typing import Any

import paramiko

from .base import BaseConnector

LOGGER = logging.getLogger("seiden_bridge")


class _FingerprintPolicy(paramiko.MissingHostKeyPolicy):
    """Valida uma host key desconhecida contra fingerprint SHA256 configurado."""

    def __init__(self, expected: str) -> None:
        self.expected = self._normalize(expected)

    @staticmethod
    def _normalize(value: str) -> str:
        text = str(value or "").strip()
        if text.lower().startswith("sha256:"):
            text = text[7:]
        return text.rstrip("=")

    @staticmethod
    def fingerprint(key: paramiko.PKey) -> str:
        digest = hashlib.sha256(key.asbytes()).digest()
        return base64.b64encode(digest).decode("ascii").rstrip("=")

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        actual = self.fingerprint(key)
        if actual != self.expected:
            raise paramiko.SSHException(
                f"Host key SSH inesperada para {hostname}: SHA256:{actual}"
            )
        client.get_host_keys().add(hostname, key.get_name(), key)


class LinuxConnector(BaseConnector):
    """Coleta telemetria de um Linux remoto por SSH sem instalar agente."""

    connector_id = "linux"

    @staticmethod
    def _endpoint(connection: dict[str, Any]) -> tuple[str, int]:
        endpoint = connection.get("endpoint") or {}
        host = str(endpoint.get("host") or connection.get("host") or "").strip()
        port = int(endpoint.get("port") or connection.get("port") or 22)
        return host, port

    @staticmethod
    def _key_path(connection: dict[str, Any]) -> str | None:
        path = str(connection.get("key_path") or "").strip()
        return path or None

    @staticmethod
    def _load_private_key(path: str) -> paramiko.PKey:
        key_path = Path(path)
        if not key_path.is_file():
            raise RuntimeError(f"Chave SSH não encontrada: {path}")
        errors: list[str] = []
        for key_cls in (
            paramiko.Ed25519Key,
            paramiko.RSAKey,
            paramiko.ECDSAKey,
        ):
            try:
                return key_cls.from_private_key_file(str(key_path))
            except Exception as error:  # pragma: no cover - depende do tipo de chave
                errors.append(str(error))
        raise RuntimeError(
            "Não foi possível carregar a chave SSH privada. " + " | ".join(errors[-2:])
        )

    @staticmethod
    def _script(mountpoint: str) -> str:
        mount_q = shlex.quote(mountpoint or "/")
        return f"""
set -eu
export LC_ALL=C
hostname_value=$(hostname 2>/dev/null || printf unknown)
machine_id=$(cat /etc/machine-id 2>/dev/null || printf '%s' "$hostname_value")
os_name=$(awk -F= '/^PRETTY_NAME=/ {{v=$2; gsub(/^\"|\"$/, \"\", v); print v; exit}}' /etc/os-release 2>/dev/null || true)
load_line=$(cat /proc/loadavg)
set -- $load_line
load1=$1; load5=$2; load15=$3
cpu_count=$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 1)
mem_total=$(awk '/^MemTotal:/ {{print $2*1024}}' /proc/meminfo)
mem_available=$(awk '/^MemAvailable:/ {{print $2*1024}}' /proc/meminfo)
swap_total=$(awk '/^SwapTotal:/ {{print $2*1024}}' /proc/meminfo)
swap_free=$(awk '/^SwapFree:/ {{print $2*1024}}' /proc/meminfo)
uptime_seconds=$(awk '{{print $1}}' /proc/uptime)
process_count=$(ps -e --no-headers 2>/dev/null | wc -l | tr -d ' ')
disk_line=$(df -Pk {mount_q} | awk 'NR==2 {{print $2" "$3" "$4" "$5}}')
set -- $disk_line
disk_total_kb=$1; disk_used_kb=$2; disk_avail_kb=$3; disk_used_pct=${{4%%%}}
network_line=$(awk -F':' 'NR>2 {{iface=$1; gsub(/ /, "", iface); data=$2; gsub(/^ +/, "", data); split(data,a,/ +/); if(iface!="lo") {{rx+=a[1]; tx+=a[9]}}}} END {{printf "%.0f %.0f", rx, tx}}' /proc/net/dev)
set -- $network_line
network_rx=$1; network_tx=$2
printf 'HOSTNAME=%s\n' "$hostname_value"
printf 'MACHINE_ID=%s\n' "$machine_id"
printf 'OS_NAME=%s\n' "$os_name"
printf 'LOAD1=%s\n' "$load1"
printf 'LOAD5=%s\n' "$load5"
printf 'LOAD15=%s\n' "$load15"
printf 'CPU_COUNT=%s\n' "$cpu_count"
printf 'MEM_TOTAL_BYTES=%.0f\n' "$mem_total"
printf 'MEM_AVAILABLE_BYTES=%.0f\n' "$mem_available"
printf 'SWAP_TOTAL_BYTES=%.0f\n' "$swap_total"
printf 'SWAP_FREE_BYTES=%.0f\n' "$swap_free"
printf 'UPTIME_SECONDS=%s\n' "$uptime_seconds"
printf 'PROCESS_COUNT=%s\n' "$process_count"
printf 'DISK_TOTAL_BYTES=%s\n' "$((disk_total_kb * 1024))"
printf 'DISK_USED_BYTES=%s\n' "$((disk_used_kb * 1024))"
printf 'DISK_AVAILABLE_BYTES=%s\n' "$((disk_avail_kb * 1024))"
printf 'DISK_USED_PCT=%s\n' "$disk_used_pct"
printf 'NETWORK_RX_BYTES=%s\n' "$network_rx"
printf 'NETWORK_TX_BYTES=%s\n' "$network_tx"
""".strip()

    @staticmethod
    def _parse_output(text: str) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in str(text).splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        return values

    @staticmethod
    def _float(values: dict[str, str], key: str, default: float = 0.0) -> float:
        try:
            return float(values.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _measurement(
        sensor_id: str,
        name: str,
        reading: int | float,
        units: str,
        physical_context: str,
    ) -> dict[str, Any]:
        return {
            "id": sensor_id,
            "name": name,
            "physical_context": physical_context,
            "reading": reading,
            "units": units,
            "range_min": None,
            "range_max": None,
            "health": "OK",
            "state": "Enabled",
            "thresholds": {},
            "related_items": [],
            "odata_id": None,
        }

    def _normalize(self, values: dict[str, str], connection: dict[str, Any]) -> dict[str, Any]:
        mem_total = self._float(values, "MEM_TOTAL_BYTES")
        mem_available = self._float(values, "MEM_AVAILABLE_BYTES")
        swap_total = self._float(values, "SWAP_TOTAL_BYTES")
        swap_free = self._float(values, "SWAP_FREE_BYTES")
        mem_used_pct = 0.0 if mem_total <= 0 else (mem_total - mem_available) * 100.0 / mem_total
        swap_used_pct = 0.0 if swap_total <= 0 else (swap_total - swap_free) * 100.0 / swap_total

        measurements = [
            self._measurement("Load1", "System Load 1m", round(self._float(values, "LOAD1"), 3), "load", "CPU"),
            self._measurement("Load5", "System Load 5m", round(self._float(values, "LOAD5"), 3), "load", "CPU"),
            self._measurement("Load15", "System Load 15m", round(self._float(values, "LOAD15"), 3), "load", "CPU"),
            self._measurement("MemoryUsed", "Memory Used", round(mem_used_pct, 1), "%", "Memory"),
            self._measurement("MemoryAvailable", "Memory Available", int(mem_available), "By", "Memory"),
            self._measurement("SwapUsed", "Swap Used", round(swap_used_pct, 1), "%", "Memory"),
            self._measurement("DiskUsed", "Disk Used", round(self._float(values, "DISK_USED_PCT"), 1), "%", "Storage"),
            self._measurement("DiskAvailable", "Disk Available", int(self._float(values, "DISK_AVAILABLE_BYTES")), "By", "Storage"),
            self._measurement("Uptime", "System Uptime", round(self._float(values, "UPTIME_SECONDS"), 1), "s", "System"),
            self._measurement("ProcessCount", "Process Count", int(self._float(values, "PROCESS_COUNT")), "count", "System"),
            self._measurement("NetworkRx", "Network Received", int(self._float(values, "NETWORK_RX_BYTES")), "By", "Network"),
            self._measurement("NetworkTx", "Network Transmitted", int(self._float(values, "NETWORK_TX_BYTES")), "By", "Network"),
        ]

        system_id = values.get("MACHINE_ID") or values.get("HOSTNAME") or str(connection.get("id"))
        system_name = values.get("HOSTNAME") or str(connection.get("name") or system_id)
        return {
            "asset": {
                "system_id": system_id,
                "system_name": system_name,
                "chassis_ids": [],
                "os_name": values.get("OS_NAME") or None,
                "cpu_count": int(self._float(values, "CPU_COUNT", 1)),
                "telemetry_profile": "linux_system",
                "capabilities": {"thermal": False, "compute": True},
            },
            "measurements": measurements,
        }

    def execute(
        self,
        connection: dict[str, Any],
        command: str,
        request_timeout: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if command not in {"snapshot", "get_metrics"}:
            raise RuntimeError(f"Comando Linux não suportado: {command}")

        host, port = self._endpoint(connection)
        username = str(connection.get("username") or "").strip()
        if not host or not username:
            raise RuntimeError("Conexão Linux exige endpoint.host e username")

        client = paramiko.SSHClient()
        fingerprint = str(connection.get("host_key_fingerprint") or "").strip()
        if fingerprint:
            client.set_missing_host_key_policy(_FingerprintPolicy(fingerprint))
        else:
            # Adequado ao laboratório; produção deve configurar fingerprint.
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict[str, Any] = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": request_timeout,
            "banner_timeout": request_timeout,
            "auth_timeout": request_timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        key_path = self._key_path(connection)
        password = connection.get("password")
        if key_path:
            connect_kwargs["pkey"] = self._load_private_key(key_path)
        elif password:
            connect_kwargs["password"] = str(password)
        else:
            raise RuntimeError("Conexão Linux exige key_path ou password")

        try:
            client.connect(**connect_kwargs)
            stdin, stdout, stderr = client.exec_command(
                "sh -s",
                timeout=request_timeout,
            )
            stdin.write(self._script(str(connection.get("mountpoint") or "/")))
            stdin.channel.shutdown_write()
            output = stdout.read().decode("utf-8", errors="replace")
            error_text = stderr.read().decode("utf-8", errors="replace").strip()
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                raise RuntimeError(error_text or f"Coleta Linux terminou com código {exit_status}")
            values = self._parse_output(output)
            normalized = self._normalize(values, connection)
            return {
                "result": True,
                "asset": normalized["asset"],
                "measurements": normalized["measurements"],
                "sensors": normalized["measurements"],
            }
        finally:
            client.close()
