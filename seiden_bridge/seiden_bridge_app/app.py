import json
import logging
import base64
import threading
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
import re

import requests

from .connectors import execute_connection_command, get_connector
from .connectors.evo_relay import EvoRelayConnector
from .events import create_presence_event, create_mqtt_event, create_mqtt_state_transition_event


CONFIG_PATH = Path("/data/options.json")
STATE_PATH = Path("/data/occupancy_state.json")

DEFAULT_POLL_INTERVAL = 2
DEFAULT_REQUEST_TIMEOUT = 5
DEFAULT_MAX_RETRY_INTERVAL = 300
DEFAULT_LOG_LEVEL = "INFO"
SUPPORTED_DRIVERS = {"evo", "mqtt"}
KNOWN_DRIVERS = {"evo", "mqtt", "control_id", "hikvision", "intelbras"}
BRIDGE_VERSION = "0.14.2"

LAST_PHOTO_DIR = Path("/config/www/seiden_bridge")
LAST_PHOTO_PATH = LAST_PHOTO_DIR / "latest.jpg"
LAST_PHOTO_PUBLIC_URL = "/local/seiden_bridge/latest.jpg"
DASHBOARD_PUBLISH_INTERVAL = 60

DEFAULT_BRIDGE_EVENT = "seiden_bridge_event"
DEFAULT_CONNECTION_OFFLINE_EVENT = "seiden_connection_offline"
DEFAULT_CONNECTION_ONLINE_EVENT = "seiden_connection_online"

IDLE_SLEEP_SECONDS = 60

STATE_LOCK = threading.RLock()

LOGGER = logging.getLogger("seiden_bridge")


def setup_logging(log_level: str) -> None:
    """Configura o sistema de logs."""
    normalized_level = str(log_level).upper()

    valid_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }

    numeric_level = valid_levels.get(
        normalized_level,
        logging.INFO,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)-7s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(numeric_level)
    LOGGER.propagate = False


def now_iso() -> str:
    """Retorna data e hora local no formato ISO."""
    return datetime.now().isoformat(timespec="seconds")


def today_str() -> str:
    """Retorna a data local atual."""
    return date.today().isoformat()


def load_config() -> dict[str, Any]:
    """Carrega as opções fornecidas pelo Home Assistant."""
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise RuntimeError(
            "A configuração do App não possui um objeto JSON válido"
        )

    return config


def sanitize_config_for_log(
    config: dict[str, Any],
) -> dict[str, Any]:
    """Oculta senhas antes de registrar a configuração."""
    sanitized = dict(config)

    for key in (
        "entry_readers",
        "exit_readers",
        "readers",
        "connections",
        "mqtt_connections",
        "environment_sources",
    ):
        sanitized_items = []
        for item in config.get(key, []):
            if not isinstance(item, dict):
                continue
            safe_item = dict(item)
            if "password" in safe_item:
                safe_item["password"] = "***"
            if "client_key" in safe_item and safe_item.get("client_key"):
                safe_item["client_key"] = "***"
            sanitized_items.append(safe_item)
        sanitized[key] = sanitized_items

    return sanitized


def normalize_connection(connection: dict[str, Any]) -> dict[str, Any]:
    """Normaliza uma conexão para o núcleo do Seiden Bridge.

    O formato interno preserva aliases antigos (reader/driver/ip) para manter
    compatibilidade operacional enquanto o EVO migra para a nova fundação.
    """
    endpoint = connection.get("endpoint") or {}
    context = connection.get("context") or {}
    connector = str(connection.get("connector", connection.get("driver", "evo"))).strip().lower()
    host = str(endpoint.get("host", connection.get("ip", ""))).strip()
    default_interaction = "message" if connector == "mqtt" else "passage"
    interaction_type = str(context.get("interaction_type", connection.get("interaction_type", default_interaction))).strip().lower()
    direction = context.get("direction", connection.get("direction"))
    if direction in ("none", "", None):
        direction = None
    connection_id = str(connection.get("id") or slugify_entity(connection.get("name", "connection")))
    return {
        **connection,
        "id": connection_id,
        "connection_id": connection_id,
        "enabled": connection.get("enabled", True),
        "connector": connector,
        "driver": connector,  # alias legado temporário
        "endpoint": {**endpoint, "host": host},
        "host": host,
        "ip": host,  # alias EVO legado temporário
        "context": {
            **context,
            "interaction_type": interaction_type,
            "direction": direction,
        },
        "interaction_type": interaction_type,
        "direction": direction,
    }


def _legacy_reader_to_connection(reader: dict[str, Any], direction: str | None) -> dict[str, Any]:
    """Converte configurações 0.6.x para o modelo de conexão 0.7.0."""
    return normalize_connection({
        **reader,
        "id": reader.get("id") or slugify_entity(reader.get("name", "reader")),
        "connector": reader.get("driver", "evo"),
        "endpoint": {"host": reader.get("ip", "")},
        "context": {
            "interaction_type": "passage",
            "direction": direction,
        },
    })


def _normalize_environment_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Valida e normaliza o Environmental Source Registry.

    Fontes desabilitadas ou inválidas são ignoradas com log, sem impedir o
    funcionamento das demais conexões do Bridge.
    """
    configured = config.get("environment_sources", [])
    if configured is None:
        return []
    if not isinstance(configured, list):
        LOGGER.error("[ENV] environment_sources deve ser uma lista; registro ignorado.")
        return []

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(configured):
        if not isinstance(item, dict):
            LOGGER.error("[ENV] Fonte #%d inválida; esperado objeto.", index + 1)
            continue
        enabled = bool(item.get("enabled", True))
        source_id = str(item.get("id") or "").strip()
        source_name = str(item.get("name") or source_id).strip()
        connection_id = str(item.get("connection_id") or "").strip()
        topic = str(item.get("topic") or "").strip()

        if not enabled:
            LOGGER.info("[ENV] Fonte desabilitada ignorada: %s", source_name or source_id or index + 1)
            continue

        missing = [name for name, value in (("id", source_id), ("name", source_name), ("connection_id", connection_id), ("topic", topic)) if not value]
        if missing:
            LOGGER.error("[ENV] Fonte #%d ignorada; campos obrigatórios ausentes: %s", index + 1, ", ".join(missing))
            continue
        if source_id in seen_ids:
            LOGGER.error("[ENV] Fonte duplicada ignorada: id=%s", source_id)
            continue

        fields = item.get("fields") or {}
        if not isinstance(fields, dict):
            LOGGER.error("[ENV][%s] fields deve ser um objeto; fonte ignorada.", source_name)
            continue
        normalized_fields = {
            "temperature_c": str(fields.get("temperature_c", "temperature")).strip(),
            "humidity_pct": str(fields.get("humidity_pct", "humidity")).strip(),
            "battery_pct": str(fields.get("battery_pct", "battery")).strip(),
        }
        normalized_fields = {key: value for key, value in normalized_fields.items() if value}
        if not normalized_fields:
            LOGGER.error("[ENV][%s] nenhum campo de medição configurado; fonte ignorada.", source_name)
            continue

        seen_ids.add(source_id)
        normalized.append({
            **item,
            "id": source_id,
            "name": source_name,
            "enabled": True,
            "connection_id": connection_id,
            "topic": topic,
            "qos": int(item.get("qos", 0)),
            "description": str(item.get("description") or "").strip() or None,
            "location_id": str(item.get("location_id") or "").strip() or None,
            "location_name": str(item.get("location_name") or "").strip() or None,
            "asset_id": str(item.get("asset_id") or "").strip() or None,
            "asset_name": str(item.get("asset_name") or "").strip() or None,
            "profile_id": str(item.get("profile_id") or "custom").strip().lower(),
            "fields": normalized_fields,
        })
    return normalized


def _extract_payload_value(payload: Any, path: str) -> Any:
    """Extrai um valor por caminho pontuado de payloads JSON."""
    current = payload
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _coerce_measurement(value: Any) -> int | float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def extract_environment_measurements(source: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Mapeia campos MQTT para nomes ambientais canônicos."""
    measurements: dict[str, Any] = {}
    for canonical_name, payload_path in source.get("fields", {}).items():
        value = _coerce_measurement(_extract_payload_value(payload, payload_path))
        if value is not None:
            measurements[canonical_name] = value
    return measurements


def find_environment_source(connection: dict[str, Any], topic: str) -> dict[str, Any] | None:
    """Localiza a fonte ambiental associada a uma mensagem MQTT."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        mqtt = None
    for source in connection.get("environment_sources", []):
        configured_topic = source["topic"]
        matches = mqtt.topic_matches_sub(configured_topic, topic) if mqtt else configured_topic == topic
        if matches:
            return source
    return None



def _normalize_mqtt_state_driver(mqtt_connection: dict[str, Any]) -> dict[str, Any]:
    """Normaliza a configuração opt-in do MQTT State Driver.

    A partir da 0.14.2 o driver atua **somente** nos filtros declarados em
    ``state_driver_topics``. Isso separa explicitamente os tópicos recebidos
    pela conexão MQTT dos tópicos que representam estados operacionais.

    Se o driver for habilitado sem tópicos explícitos, ele é desabilitado de
    forma segura e o MQTT legado continua publicando os payloads brutos.
    """
    enabled = bool(mqtt_connection.get("state_driver_enabled", False))
    configured_topics = mqtt_connection.get("state_driver_topics")
    name = str(mqtt_connection.get("name") or mqtt_connection.get("id") or "MQTT")

    # Home Assistant Supervisor não oferece sintaxe documentada para uma
    # lista estrutural "verdadeiramente opcional" dentro deste schema aninhado.
    # Para preservar upgrade de instalações existentes, o schema usa `str?`.
    # O usuário pode informar um bloco YAML multilinha, uma linha por tópico.
    # Também aceitamos vírgula ou ponto-e-vírgula como separadores.
    if configured_topics is None:
        topics = []
    elif isinstance(configured_topics, str):
        topics = [
            item.strip()
            for item in re.split(r"[\n,;]+", configured_topics)
            if item.strip()
        ]
    elif isinstance(configured_topics, list):
        # Compatibilidade defensiva fora do Supervisor / testes locais.
        topics = [str(item).strip() for item in configured_topics if str(item).strip()]
    else:
        LOGGER.warning(
            "[MQTT STATE][%s] state_driver_topics inválido; State Driver desabilitado.",
            name,
        )
        topics = []
        enabled = False
    if enabled and not topics:
        LOGGER.warning(
            "[MQTT STATE][%s] habilitado sem state_driver_topics; driver desabilitado por segurança.",
            name,
        )
        enabled = False

    return {
        "enabled": enabled,
        "topics": topics,
        "fields": [],  # vazio = todos os campos com o prefixo configurado
        "field_prefix": str(mqtt_connection.get("state_driver_field_prefix", "state_")).strip(),
        "publish_raw": bool(mqtt_connection.get("state_driver_publish_raw", True)),
    }


def _mqtt_topic_matches(filters: list[str], topic: str) -> bool:
    """Compara um tópico com filtros MQTT sem criar assinaturas extras."""
    if not filters:
        return True
    try:
        import paho.mqtt.client as mqtt
        return any(mqtt.topic_matches_sub(item, topic) for item in filters)
    except Exception:
        # Fallback conservador para filtros exatos.
        return topic in filters


def _state_driver_extract(
    state_driver: dict[str, Any],
    payload: Any,
) -> dict[str, Any]:
    """Extrai somente os campos operacionais acompanhados pelo driver."""
    if not isinstance(payload, dict):
        return {}

    configured_fields = set(state_driver.get("fields") or [])
    prefix = str(state_driver.get("field_prefix") or "state_")

    extracted: dict[str, Any] = {}
    for key, value in payload.items():
        key_str = str(key)
        if configured_fields:
            if key_str not in configured_fields:
                continue
        elif prefix and not key_str.startswith(prefix):
            continue

        if isinstance(value, (str, int, float, bool)) or value is None:
            extracted[key_str] = value

    return extracted


def _state_driver_channel(field: str, prefix: str) -> str:
    """Remove somente o prefixo técnico; não inventa aliases físicos."""
    if prefix and field.startswith(prefix):
        return field[len(prefix):]
    return field


def build_connections_from_config(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Carrega o novo formato `connections` e migra formatos anteriores."""
    configured_connections = config.get("connections")
    all_connections: list[dict[str, Any]] = []
    environment_sources = _normalize_environment_sources(config)
    sources_by_connection: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in environment_sources:
        sources_by_connection[source["connection_id"]].append(source)

    if configured_connections is not None:
        if not isinstance(configured_connections, list):
            raise RuntimeError("connections deve ser uma lista")
        for connection in configured_connections:
            if not isinstance(connection, dict):
                raise RuntimeError("Existe uma conexão inválida")
            all_connections.append(normalize_connection(connection))
    else:
        entry_readers = config.get("entry_readers")
        exit_readers = config.get("exit_readers")
        if entry_readers is not None or exit_readers is not None:
            LOGGER.warning(
                "[CONFIG] Configuração 0.6.x detectada. Migre 'entry_readers' e "
                "'exit_readers' para 'connections'."
            )
            for reader in entry_readers or []:
                if not isinstance(reader, dict):
                    raise RuntimeError("Existe um leitor de entrada inválido")
                all_connections.append(_legacy_reader_to_connection(reader, "in"))
            for reader in exit_readers or []:
                if not isinstance(reader, dict):
                    raise RuntimeError("Existe um leitor de saída inválido")
                all_connections.append(_legacy_reader_to_connection(reader, "out"))
        else:
            legacy_readers = config.get("readers", [])
            if legacy_readers:
                LOGGER.warning(
                    "[CONFIG] Configuração antiga detectada em 'readers'. "
                    "Migre para 'connections'."
                )
            if not isinstance(legacy_readers, list):
                raise RuntimeError("A configuração antiga 'readers' deve ser uma lista")
            for reader in legacy_readers:
                if not isinstance(reader, dict):
                    raise RuntimeError("Existe um leitor inválido na configuração antiga")
                all_connections.append(_legacy_reader_to_connection(reader, reader.get("direction", "in")))

    mqtt_connections = config.get("mqtt_connections", [])
    if not isinstance(mqtt_connections, list):
        raise RuntimeError("mqtt_connections deve ser uma lista")

    for mqtt_connection in mqtt_connections:
        if not isinstance(mqtt_connection, dict):
            raise RuntimeError("Existe uma conexão MQTT inválida")

        connection_id = str(mqtt_connection.get("id") or "").strip()
        topics = mqtt_connection.get("topics", [])
        if not isinstance(topics, list):
            LOGGER.error("[MQTT][%s] topics deve ser uma lista; conexão ignorada.", mqtt_connection.get("name", connection_id or "sem nome"))
            continue
        source_items = sources_by_connection.get(connection_id, [])
        merged_topics = [str(topic).strip() for topic in topics if str(topic).strip()]
        for source in source_items:
            if source["topic"] not in merged_topics:
                merged_topics.append(source["topic"])
        if not merged_topics:
            LOGGER.error("[MQTT][%s] conexão sem tópicos ou fontes ambientais; ignorada.", mqtt_connection.get("name", connection_id or "sem nome"))
            continue

        qos = int(mqtt_connection.get("qos", 0))
        event_type = str(mqtt_connection.get("event_type", "mqtt.message_received"))
        state_driver = _normalize_mqtt_state_driver(mqtt_connection)
        if state_driver.get("enabled"):
            LOGGER.info(
                "[MQTT STATE][%s] ativo em %d tópico(s) explícito(s).",
                mqtt_connection.get("name") or connection_id,
                len(state_driver.get("topics") or []),
            )
        normalized_mqtt = {
            "id": connection_id,
            "name": mqtt_connection.get("name"),
            "connector": "mqtt",
            "enabled": mqtt_connection.get("enabled", True),
            "username": mqtt_connection.get("username"),
            "password": mqtt_connection.get("password"),
            "client_id": mqtt_connection.get("client_id"),
            "clean_session": mqtt_connection.get("clean_session", True),
            "endpoint": {
                "host": mqtt_connection.get("host", ""),
                "port": mqtt_connection.get("port", 1883),
                "keepalive": mqtt_connection.get("keepalive", 60),
            },
            "context": {
                "interaction_type": "message",
                "direction": None,
            },
            "subscriptions": [
                {"topic": str(topic), "qos": qos, "event_type": event_type}
                for topic in merged_topics
            ],
            "environment_sources": source_items,
            "state_driver": state_driver,
            "tls": {
                "enabled": mqtt_connection.get("tls_enabled", False),
                "verify": mqtt_connection.get("tls_verify", True),
                "ca_cert": mqtt_connection.get("ca_cert"),
                "client_cert": mqtt_connection.get("client_cert"),
                "client_key": mqtt_connection.get("client_key"),
            },
        }
        all_connections.append(normalize_connection(normalized_mqtt))

    configured_mqtt_ids = {item.get("id") for item in all_connections if item.get("connector") == "mqtt"}
    for source in environment_sources:
        if source["connection_id"] not in configured_mqtt_ids:
            LOGGER.error("[ENV][%s] connection_id '%s' não encontrado; fonte não será assinada.", source["name"], source["connection_id"])

    active = [item for item in all_connections if item.get("enabled", True)]
    disabled = [item for item in all_connections if not item.get("enabled", True)]
    return all_connections, active, disabled


# Alias interno temporário para reduzir risco na migração 0.7.0.
build_readers_from_config = build_connections_from_config

def build_evo_relay_connections(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Normaliza os servidores EVO Relay e o mapa serial -> equipamento."""
    configured = config.get("evo_relay_connections", []) or []
    if not isinstance(configured, list):
        raise RuntimeError("evo_relay_connections deve ser uma lista")

    relays: list[dict[str, Any]] = []
    seen_ports: set[int] = set()
    seen_serials: set[str] = set()
    for index, item in enumerate(configured):
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        relay_id = str(item.get("id") or f"evo_relay_{index + 1}").strip()
        name = str(item.get("name") or relay_id).strip()
        host = str(item.get("host") or "").strip()
        port = int(item.get("port", 7788))
        listen_port = int(item.get("listen_port", 7788))
        scheme = str(item.get("scheme") or "ws").strip().lower()
        path = str(item.get("path") or "/").strip() or "/"
        if not host:
            raise RuntimeError(f"Servidor ausente no EVO Relay {name}")
        if scheme not in {"ws", "wss"}:
            raise RuntimeError(f"Protocolo inválido no EVO Relay {name}: {scheme}")
        if listen_port in seen_ports:
            raise RuntimeError(f"Porta local duplicada entre EVO Relays: {listen_port}")
        seen_ports.add(listen_port)

        devices_raw = item.get("devices") or []
        if not isinstance(devices_raw, list) or not devices_raw:
            raise RuntimeError(f"O EVO Relay {name} exige ao menos um equipamento")
        devices = []
        for device_index, device in enumerate(devices_raw):
            if not isinstance(device, dict) or not device.get("enabled", True):
                continue
            serial = str(device.get("serial_number") or "").strip()
            if not serial:
                raise RuntimeError(f"serial_number ausente em {name}, equipamento #{device_index + 1}")
            if serial in seen_serials:
                raise RuntimeError(f"Serial EVO Relay duplicado: {serial}")
            seen_serials.add(serial)
            direction_raw = str(device.get("direction") or "none").strip().lower()
            if direction_raw not in {"in", "out", "none"}:
                raise RuntimeError(f"Direção inválida para {serial}: {direction_raw}")
            direction = None if direction_raw == "none" else direction_raw
            device_id = str(device.get("id") or f"{relay_id}_{slugify_entity(serial)}")
            devices.append({
                **device,
                "id": device_id,
                "connection_id": device_id,
                "serial_number": serial,
                "name": str(device.get("name") or serial).strip(),
                "enabled": True,
                "direction": direction,
                "interaction_type": "passage" if direction else "authentication",
                "customer_id": str(device.get("customer_id") or "").strip() or None,
                "site_id": str(device.get("site_id") or "").strip() or None,
                "connector": "evo_relay",
                "driver": "evo_relay",
                # Antes do primeiro `reg`, o IP real do terminal ainda é desconhecido.
                # `host` representa o servidor upstream; `ip` será enriquecido pelo devinfo.curip.
                "host": host,
                "ip": None,
                "relay_server": host,
                "relay_port": port,
                "endpoint": {"host": host, "port": port, "scheme": scheme, "path": path},
            })
        relays.append({
            **item,
            "id": relay_id,
            "name": name,
            "enabled": True,
            "listen_host": str(item.get("listen_host") or "0.0.0.0"),
            "listen_port": listen_port,
            "endpoint": {"host": host, "port": port, "scheme": scheme, "path": path},
            "devices": devices,
        })
    return relays


def relay_device_map(relay: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(device["serial_number"]): device for device in relay.get("devices", [])}


def save_evo_relay_image(serial: str, record: dict[str, Any], maximum_size_mb: int) -> tuple[str | None, str | None]:
    """Salva JPEG Base64 de sendlog e devolve URL local e nome do arquivo."""
    value = record.get("image")
    if not isinstance(value, str) or not value.strip():
        return None, None
    raw = value.strip()
    if raw.lower().startswith("data:image/") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        image = base64.b64decode(raw, validate=False)
    except Exception:
        LOGGER.warning("[EVO RELAY][%s] Imagem Base64 inválida", serial)
        return None, None
    if not image.startswith(b"\xff\xd8\xff"):
        LOGGER.warning("[EVO RELAY][%s] Imagem recebida não é JPEG", serial)
        return None, None
    if len(image) > max(1, int(maximum_size_mb)) * 1024 * 1024:
        LOGGER.warning("[EVO RELAY][%s] Imagem excede limite configurado", serial)
        return None, None

    serial_slug = slugify_entity(serial)
    directory = LAST_PHOTO_DIR / "evo_relay" / serial_slug
    directory.mkdir(parents=True, exist_ok=True)
    when = re.sub(r"[^0-9]", "", str(record.get("time") or ""))[:14] or str(int(time.time()))
    person = slugify_entity(str(record.get("enrollid") or "unknown"))
    filename = f"{when}_{person}_{int(time.time() * 1000)}.jpg"
    (directory / filename).write_bytes(image)
    try:
        captures = sorted(directory.glob("*.jpg"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old in captures[100:]:
            old.unlink(missing_ok=True)
    except OSError:
        LOGGER.debug("[EVO RELAY][%s] Não foi possível aplicar retenção de imagens", serial)
    return f"/local/seiden_bridge/evo_relay/{serial_slug}/{filename}", filename


def handle_evo_relay_record(
    relay: dict[str, Any], serial: str, record: dict[str, Any], state: dict[str, Any],
    supervisor_token: str, bridge_events: list[str], request_timeout: int,
    photo_max_size_mb: int, operation_timezone: str,
) -> None:
    device = relay_device_map(relay).get(serial)
    if device is None:
        LOGGER.warning("[EVO RELAY][%s] Serial não cadastrado %s; frame apenas encaminhado", relay["name"], serial)
        return
    if record.get("event") != 0:
        LOGGER.info("[EVO RELAY][%s][%s] Evento ignorado código=%s", relay["name"], serial, record.get("event"))
        return

    safe_record = dict(record)
    photo_url, photo_filename = save_evo_relay_image(serial, record, photo_max_size_mb)
    safe_record.pop("image", None)
    safe_record["image_captured"] = bool(photo_url)
    if photo_url:
        safe_record["_seiden_photo_url"] = photo_url
        safe_record["_seiden_photo_filename"] = photo_filename

    with STATE_LOCK:
        payload = handle_authorized_record(device, safe_record, state, operation_timezone)
    payload.update({
        "device_serial": serial,
        "device_name": device["name"],
        "customer_id": device.get("customer_id"),
        "site_id": device.get("site_id"),
        "relay_id": relay["id"],
        "reader_ip": device.get("ip"),
        "relay_server": relay["endpoint"].get("host"),
        "relay_port": relay["endpoint"].get("port"),
    })
    payload["reader"]["ip"] = device.get("ip")
    payload["reader"].update({
        "serial_number": serial,
        "relay_server": relay["endpoint"].get("host"),
        "relay_port": relay["endpoint"].get("port"),
    })
    payload["connection"].update({
        "type": "websocket_device",
        "serial_number": serial,
        "relay_id": relay["id"],
        "endpoint": relay["endpoint"],
    })
    payload["context"].update({
        "customer_id": device.get("customer_id"),
        "site_id": device.get("site_id"),
    })
    fire_event_names(
        supervisor_token=supervisor_token,
        event_names=bridge_events,
        payload=payload,
        request_timeout=request_timeout,
    )
    LOGGER.info(
        "[EVO RELAY][%s][%s] %s | direção=%s | foto=%s",
        relay["name"], serial, payload.get("user_name"), device.get("direction") or "none",
        "sim" if photo_url else "não",
    )



def default_state() -> dict[str, Any]:
    """Cria o estado inicial do Occupancy Engine."""
    return {
        "date": today_str(),
        "people_inside": {},
        "first_entry_today": None,
        "last_exit_today": None,
        "entries_today": 0,
        "exits_today": 0,
        "events_today": 0,
        "last_event": None,
    }


def load_state() -> dict[str, Any]:
    """Carrega o estado persistente de ocupação."""
    if not STATE_PATH.exists():
        LOGGER.info(
            "[STATE] Nenhum estado anterior encontrado. "
            "Um novo estado será criado."
        )
        return default_state()

    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            state = json.load(file)

    except (OSError, json.JSONDecodeError) as error:
        LOGGER.error(
            "[STATE] Não foi possível carregar o estado: %s",
            error,
        )
        LOGGER.warning(
            "[STATE] Um novo estado será iniciado."
        )
        return default_state()

    if not isinstance(state, dict):
        LOGGER.error(
            "[STATE] O arquivo de estado não possui um objeto válido."
        )
        return default_state()

    state.setdefault("date", today_str())
    state.setdefault("people_inside", {})
    state.setdefault("first_entry_today", None)
    state.setdefault("last_exit_today", None)
    state.setdefault("entries_today", 0)
    state.setdefault("exits_today", 0)
    state.setdefault("events_today", 0)
    state.setdefault("last_event", None)

    if not isinstance(state["people_inside"], dict):
        LOGGER.error(
            "[STATE] people_inside inválido. "
            "A ocupação será reiniciada."
        )
        state["people_inside"] = {}

    reset_daily_state_if_needed(state)

    return state


def save_state(state: dict[str, Any]) -> None:
    """Salva o estado persistente usando escrita atômica."""
    temporary_path = STATE_PATH.with_suffix(".tmp")

    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(
                state,
                file,
                ensure_ascii=False,
                indent=2,
            )

        temporary_path.replace(STATE_PATH)

    except OSError:
        LOGGER.exception(
            "[STATE] Falha ao salvar o estado persistente."
        )
        raise


def reset_daily_state_if_needed(
    state: dict[str, Any],
) -> None:
    """
    Reinicia os indicadores diários quando a data muda.

    Pessoas que permaneceram após a meia-noite continuam presentes.
    """
    current_date = today_str()

    if state.get("date") == current_date:
        return

    previous_date = state.get("date")

    state["date"] = current_date
    state["first_entry_today"] = None
    state["last_exit_today"] = None
    state["entries_today"] = 0
    state["exits_today"] = 0
    state["events_today"] = 0

    save_state(state)

    LOGGER.info(
        "[STATE] Novo dia iniciado: %s → %s",
        previous_date,
        current_date,
    )


def fire_ha_event(
    supervisor_token: str,
    event_type: str,
    payload: dict[str, Any],
    request_timeout: int,
) -> None:
    """Publica um evento no barramento do Home Assistant."""
    headers = {
        "Authorization": f"Bearer {supervisor_token}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        f"http://supervisor/core/api/events/{event_type}",
        headers=headers,
        json=payload,
        timeout=request_timeout,
    )

    response.raise_for_status()


def safe_fire_ha_event(
    supervisor_token: str,
    event_type: str,
    payload: dict[str, Any],
    request_timeout: int,
) -> bool:
    """Publica um evento sem encerrar o Bridge em caso de falha."""
    try:
        fire_ha_event(
            supervisor_token=supervisor_token,
            event_type=event_type,
            payload=payload,
            request_timeout=request_timeout,
        )

        LOGGER.debug(
            "[HA] Evento publicado: %s | payload=%s",
            event_type,
            payload,
        )

        return True

    except requests.RequestException as error:
        LOGGER.error(
            "[HA] Não foi possível publicar o evento %s: %s",
            event_type,
            error,
        )
        return False




def fire_event_names(
    *,
    supervisor_token: str,
    event_names: list[str],
    payload: dict[str, Any],
    request_timeout: int,
) -> bool:
    """Publica o mesmo payload em nomes únicos, preservando aliases legados."""
    sent = False
    seen: set[str] = set()
    for event_name in event_names:
        normalized = str(event_name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        sent = safe_fire_ha_event(
            supervisor_token=supervisor_token,
            event_type=normalized,
            payload=payload,
            request_timeout=request_timeout,
        ) or sent
    return sent

def slugify_entity(value: str) -> str:
    """Converte um nome em identificador seguro para entidade do HA."""
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower())
    return re.sub(r"_+", "_", normalized).strip("_") or "reader"


def set_ha_state(
    supervisor_token: str,
    entity_id: str,
    state: Any,
    attributes: dict[str, Any],
    request_timeout: int,
) -> bool:
    """Cria ou atualiza uma entidade operacional no Home Assistant."""
    headers = {
        "Authorization": f"Bearer {supervisor_token}",
        "Content-Type": "application/json",
    }
    payload = {"state": str(state), "attributes": attributes}

    try:
        response = requests.post(
            f"http://supervisor/core/api/states/{entity_id}",
            headers=headers,
            json=payload,
            timeout=request_timeout,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as error:
        LOGGER.error(
            "[HA] Não foi possível atualizar a entidade %s: %s",
            entity_id,
            error,
        )
        return False


def publish_reader_entity(
    supervisor_token: str,
    reader: dict[str, Any],
    runtime: dict[str, Any],
    request_timeout: int,
) -> None:
    """Publica o estado operacional individual de um leitor."""
    reader_slug = slugify_entity(reader["name"])
    status = runtime.get("status", "unknown")
    entity_state = "on" if status == "online" else "off"

    set_ha_state(
        supervisor_token=supervisor_token,
        entity_id=f"binary_sensor.seiden_reader_{reader_slug}",
        state=entity_state,
        attributes={
            "friendly_name": f"Seiden Reader {reader['name']}",
            "device_class": "connectivity",
            "reader_name": reader["name"],
            "reader_ip": reader["ip"],
            "direction": reader.get("direction"),
            "driver": reader.get("driver", "evo"),
            "operational_status": status,
            "failure_count": runtime.get("failures", 0),
            "last_error": runtime.get("last_error"),
            "offline_since": runtime.get("offline_since_iso"),
            "last_success": runtime.get("last_success_iso"),
            "last_event": runtime.get("last_event"),
            "icon": "mdi:face-recognition",
        },
        request_timeout=request_timeout,
    )


def update_last_photo_file(
    photo_url: str | None,
    photo_filename: str | None,
    request_timeout: int,
    maximum_size_mb: int = 5,
) -> tuple[bool, str | None, str | None]:
    """Baixa a última foto e cria uma URL única para evitar cache."""
    if not photo_url:
        return False, None, "URL da foto ausente"

    maximum_bytes = max(1, int(maximum_size_mb)) * 1024 * 1024
    safe_name = Path(str(photo_filename or "")).name
    if not safe_name.lower().endswith((".jpg", ".jpeg")):
        safe_name = f"capture_{int(time.time() * 1000)}.jpg"

    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(safe_name).stem).strip("_")
    stem = stem or "capture"
    unique_name = f"{stem}_{int(time.time() * 1000)}.jpg"
    target_path = LAST_PHOTO_DIR / unique_name
    temporary_path = LAST_PHOTO_DIR / f".{unique_name}.tmp"

    try:
        LAST_PHOTO_DIR.mkdir(parents=True, exist_ok=True)

        if str(photo_url).startswith("/local/seiden_bridge/"):
            relative = str(photo_url)[len("/local/seiden_bridge/"):].lstrip("/")
            source_path = LAST_PHOTO_DIR / relative
            if not source_path.exists() or not source_path.is_file():
                raise OSError(f"Imagem local não encontrada: {source_path}")
            total = source_path.stat().st_size
            if total <= 0:
                raise ValueError("Imagem local está vazia")
            if total > maximum_bytes:
                raise ValueError(f"Imagem excede o limite de {maximum_size_mb} MB")
            target_path.write_bytes(source_path.read_bytes())
        else:
            response = requests.get(
                photo_url,
                timeout=request_timeout,
                stream=True,
            )
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not (
                "image/jpeg" in content_type or "image/jpg" in content_type
            ):
                return False, None, f"Tipo de conteúdo não suportado: {content_type}"

            total = 0
            with temporary_path.open("wb") as file_handle:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > maximum_bytes:
                        raise ValueError(
                            f"Imagem excede o limite de {maximum_size_mb} MB"
                        )
                    file_handle.write(chunk)

            if total == 0:
                raise ValueError("Imagem recebida está vazia")

            temporary_path.replace(target_path)

        # Mantém também um arquivo estável para acesso manual e compatibilidade.
        try:
            LAST_PHOTO_PATH.write_bytes(target_path.read_bytes())
        except OSError as error:
            LOGGER.warning("[FOTO] Não foi possível atualizar latest.jpg: %s", error)

        # Remove capturas antigas, preservando a atual e latest.jpg.
        try:
            candidates = sorted(
                (item for item in LAST_PHOTO_DIR.glob("*.jpg") if item.name != "latest.jpg"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for old_file in candidates[5:]:
                old_file.unlink(missing_ok=True)
        except OSError as error:
            LOGGER.debug("[FOTO] Não foi possível limpar imagens antigas: %s", error)

        return True, f"/local/seiden_bridge/{unique_name}", None

    except (requests.RequestException, OSError, ValueError) as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        LOGGER.warning("[FOTO] Não foi possível atualizar a última imagem: %s", error)
        return False, None, str(error)


def publish_last_photo_entity(
    supervisor_token: str,
    last_event: dict[str, Any],
    request_timeout: int,
    photo_available: bool,
    entity_picture: str | None,
    photo_error: str | None = None,
) -> None:
    """Publica a última foto como sensor com entity_picture."""
    event_time = last_event.get("time") or now_iso()

    attributes = {
        "friendly_name": "Última identificação",
        "integration": "Seiden Bridge",
        "bridge_version": BRIDGE_VERSION,
        "entity_picture": entity_picture if photo_available else None,
        "photo_available": photo_available,
        "photo_url": last_event.get("photo_url"),
        "photo_filename": last_event.get("photo_filename"),
        "person": last_event.get("user_name"),
        "reader": last_event.get("reader_name"),
        "action": last_event.get("action"),
        "action_label": action_label(last_event.get("action")),
        "captured_at": last_event.get("time"),
        "photo_error": photo_error,
        "icon": "mdi:camera-account",
    }

    set_ha_state(
        supervisor_token=supervisor_token,
        entity_id="sensor.seiden_last_photo",
        state=event_time if photo_available else "unavailable",
        attributes=attributes,
        request_timeout=request_timeout,
    )

def publish_operational_entities(
    supervisor_token: str,
    readers: list[dict[str, Any]],
    reader_runtime: dict[str, dict[str, Any]],
    state: dict[str, Any],
    started_monotonic: float,
    request_timeout: int,
    publish_last_photo: bool = True,
    photo_max_size_mb: int = 5,
) -> None:
    """Publica as entidades usadas pelo dashboard operacional."""
    runtimes = [reader_runtime[reader["ip"]] for reader in readers]
    online = sum(1 for runtime in runtimes if runtime.get("status") == "online")
    offline = sum(1 for runtime in runtimes if runtime.get("status") == "offline")
    unknown = len(readers) - online - offline
    people = list(state.get("people_inside", {}).values())
    last_event = state.get("last_event") or {}
    uptime_seconds = int(time.monotonic() - started_monotonic)

    if publish_last_photo:
        photo_available = LAST_PHOTO_PATH.exists()
        photo_error = None
        entity_picture = LAST_PHOTO_PUBLIC_URL if photo_available else None
        current_photo_url = last_event.get("photo_url")
        current_photo_filename = last_event.get("photo_filename")
        marker_file = LAST_PHOTO_DIR / ".source"
        picture_marker_file = LAST_PHOTO_DIR / ".entity_picture"
        previous_source = None
        try:
            if marker_file.exists():
                previous_source = marker_file.read_text(encoding="utf-8").strip()
            if picture_marker_file.exists():
                stored_picture = picture_marker_file.read_text(encoding="utf-8").strip()
                if stored_picture:
                    entity_picture = stored_picture
        except OSError:
            previous_source = None

        if current_photo_url and current_photo_url != previous_source:
            photo_available, new_entity_picture, photo_error = update_last_photo_file(
                photo_url=current_photo_url,
                photo_filename=current_photo_filename,
                request_timeout=request_timeout,
                maximum_size_mb=photo_max_size_mb,
            )
            if photo_available and new_entity_picture:
                entity_picture = new_entity_picture
                try:
                    marker_file.write_text(str(current_photo_url), encoding="utf-8")
                    picture_marker_file.write_text(entity_picture, encoding="utf-8")
                except OSError as error:
                    LOGGER.warning("[FOTO] Não foi possível salvar marcador da foto: %s", error)

        publish_last_photo_entity(
            supervisor_token=supervisor_token,
            last_event=last_event,
            request_timeout=request_timeout,
            photo_available=photo_available,
            entity_picture=entity_picture,
            photo_error=photo_error,
        )

    common = {"integration": "Seiden Bridge", "bridge_version": BRIDGE_VERSION}
    reader_statuses = [
        {
            "name": reader["name"],
            "ip": reader["ip"],
            "direction": reader.get("direction"),
            "status": reader_runtime[reader["ip"]].get("status", "unknown"),
            "failure_count": reader_runtime[reader["ip"]].get("failures", 0),
            "last_success": reader_runtime[reader["ip"]].get("last_success_iso"),
            "last_error": reader_runtime[reader["ip"]].get("last_error"),
            "last_event": reader_runtime[reader["ip"]].get("last_event"),
        }
        for reader in readers
    ]

    entities = [
        ("binary_sensor.seiden_bridge_running", "on", {**common, "friendly_name": "Seiden Bridge", "device_class": "running", "icon": "mdi:bridge"}),
        ("sensor.seiden_bridge_version", BRIDGE_VERSION, {**common, "friendly_name": "Versão Seiden Bridge", "icon": "mdi:tag-outline"}),
        ("sensor.seiden_bridge_uptime", uptime_seconds, {**common, "friendly_name": "Uptime Seiden Bridge", "unit_of_measurement": "s", "device_class": "duration", "state_class": "measurement", "icon": "mdi:timer-outline"}),
        ("sensor.seiden_readers_online", online, {**common, "friendly_name": "Leitores online", "icon": "mdi:lan-connect"}),
        ("sensor.seiden_readers_offline", offline, {**common, "friendly_name": "Leitores offline", "icon": "mdi:lan-disconnect"}),
        ("sensor.seiden_readers_unknown", unknown, {**common, "friendly_name": "Leitores em verificação", "icon": "mdi:lan-pending"}),
        ("sensor.seiden_readers_status", f"{online}/{len(readers)}", {**common, "friendly_name": "Estado dos leitores", "readers": reader_statuses, "online": online, "offline": offline, "unknown": unknown, "icon": "mdi:server-network"}),
        ("sensor.seiden_people_inside", len(people), {**common, "friendly_name": "Pessoas presentes", "people_inside": people, "names": [person.get("user_name") for person in people], "icon": "mdi:account-group"}),
        ("binary_sensor.seiden_building_occupied", "on" if people else "off", {**common, "friendly_name": "Ambiente ocupado", "device_class": "occupancy", "people_inside": len(people)}),
        ("sensor.seiden_events_today", state.get("events_today", 0), {**common, "friendly_name": "Movimentos hoje", "icon": "mdi:counter"}),
        ("sensor.seiden_entries_today", state.get("entries_today", 0), {**common, "friendly_name": "Entradas hoje", "icon": "mdi:login"}),
        ("sensor.seiden_exits_today", state.get("exits_today", 0), {**common, "friendly_name": "Saídas hoje", "icon": "mdi:logout"}),
        ("sensor.seiden_last_person", last_event.get("user_name", "Nenhum evento"), {**common, "friendly_name": "Última pessoa", "user_id": last_event.get("user_id"), "photo_url": last_event.get("photo_url"), "photo_filename": last_event.get("photo_filename"), "icon": "mdi:account-clock"}),
        ("sensor.seiden_last_action", action_label(last_event.get("action")), {**common, "friendly_name": "Último movimento", "action": last_event.get("action", "none"), "direction": last_event.get("direction"), "icon": "mdi:swap-horizontal"}),
        ("sensor.seiden_last_reader", last_event.get("reader_name", "Nenhum evento"), {**common, "friendly_name": "Último leitor", "reader_ip": last_event.get("reader_ip"), "icon": "mdi:face-recognition"}),
        ("sensor.seiden_last_event_time", last_event.get("time", "unknown"), {**common, "friendly_name": "Horário do último evento", "device_class": "timestamp", "icon": "mdi:clock-outline"}),
    ]

    for entity_id, entity_state, attributes in entities:
        set_ha_state(
            supervisor_token=supervisor_token,
            entity_id=entity_id,
            state=entity_state,
            attributes=attributes,
            request_timeout=request_timeout,
        )

    for reader in readers:
        publish_reader_entity(
            supervisor_token=supervisor_token,
            reader=reader,
            runtime=reader_runtime[reader["ip"]],
            request_timeout=request_timeout,
        )


def record_key(record: dict[str, Any]) -> str:
    """
    Cria uma chave lógica para deduplicação.

    photourl não participa porque a foto pode ser associada depois.
    """
    return "|".join(
        [
            str(record.get("time")),
            str(record.get("enrollid")),
            str(record.get("event")),
            str(record.get("mode")),
            str(record.get("inout")),
        ]
    )


def build_photo_url(
    reader: dict[str, Any],
    record: dict[str, Any],
) -> str | None:
    """Monta a URL completa da foto."""
    if record.get("_seiden_photo_url"):
        return str(record["_seiden_photo_url"])
    photo_path = record.get("photourl")

    if not photo_path:
        return None

    return f"http://{reader['ip']}{photo_path}"


def build_photo_filename(record: dict[str, Any]) -> str | None:
    """Extrai o nome do arquivo de foto informado pelo leitor."""
    if record.get("_seiden_photo_filename"):
        return str(record["_seiden_photo_filename"])
    photo_path = record.get("photourl")

    if not photo_path:
        return None

    filename = Path(str(photo_path)).name
    return filename or None


def action_label(action: str | None) -> str:
    """Traduz o movimento técnico para exibição no Home Assistant."""
    labels = {
        "entered": "Entrada",
        "exited": "Saída",
        "authorized": "Autorização",
        "none": "Nenhum evento",
    }
    return labels.get(str(action), str(action or "Nenhum evento"))


def handle_authorized_record(
    reader: dict[str, Any],
    record: dict[str, Any],
    state: dict[str, Any],
    operation_timezone: str,
) -> dict[str, Any]:
    """Processa autenticação conforme o contexto da conexão.

    `passage` atualiza ocupação quando há direção in/out.
    `authorization` registra a liberação sem alterar ocupação.
    """
    reset_daily_state_if_needed(state)
    user_id = str(record.get("enrollid"))
    user_name = record.get("name") or user_id
    direction = reader.get("direction")
    interaction_type = reader.get("interaction_type", "passage")
    event_time = record.get("time") or now_iso()
    photo_url = build_photo_url(reader, record)
    photo_filename = build_photo_filename(record)
    people_before = len(state["people_inside"])
    is_first_entry = False
    is_last_exit = False
    was_already_inside = user_id in state["people_inside"]

    if interaction_type == "passage" and direction == "in":
        if not was_already_inside:
            if people_before == 0:
                is_first_entry = True
                state["first_entry_today"] = {"user_id": user_id, "user_name": user_name, "time": event_time}
            state["people_inside"][user_id] = {
                "user_id": user_id, "user_name": user_name, "entered_at": event_time,
                "reader_name": reader["name"], "reader_ip": reader["ip"],
                "connection_id": reader["connection_id"],
            }
        action = "entered"
        state["entries_today"] = int(state.get("entries_today", 0)) + 1
    elif interaction_type == "passage" and direction == "out":
        if was_already_inside:
            del state["people_inside"][user_id]
            if len(state["people_inside"]) == 0:
                is_last_exit = True
                state["last_exit_today"] = {"user_id": user_id, "user_name": user_name, "time": event_time}
        action = "exited"
        state["exits_today"] = int(state.get("exits_today", 0)) + 1
    else:
        action = "authorized"

    people_inside = list(state["people_inside"].values())
    state["events_today"] = int(state.get("events_today", 0)) + 1
    operational = {
        "reader_id": reader["connection_id"],
        "connection_id": reader["connection_id"],
        "action": action,
        "interaction_type": interaction_type,
        "photo_url": photo_url,
        "photo_filename": photo_filename,
        "was_already_inside": was_already_inside,
        "exit_without_entry": interaction_type == "passage" and direction == "out" and not was_already_inside,
        "is_first_entry": is_first_entry,
        "is_last_exit": is_last_exit,
        "people_inside_count": len(people_inside),
        "building_occupied": len(people_inside) > 0,
        "people_inside": people_inside,
        "first_entry_today": state.get("first_entry_today"),
        "last_exit_today": state.get("last_exit_today"),
    }
    payload = create_presence_event(reader=reader, record=record, operational=operational, source_timezone=operation_timezone)
    state["last_event"] = {
        "user_id": user_id, "user_name": user_name,
        "reader_name": reader["name"], "reader_ip": reader["ip"],
        "connection_id": reader["connection_id"], "connector": reader["connector"],
        "interaction_type": interaction_type, "direction": direction,
        "action": action, "time": event_time,
        "photo_url": photo_url, "photo_filename": photo_filename,
    }
    save_state(state)
    return payload

def create_reader_runtime_state() -> dict[str, Any]:
    """Cria o estado de disponibilidade de um leitor."""
    return {
        "failures": 0,
        "next_check": 0.0,
        "offline": False,
        "status": "unknown",
        "offline_since_iso": None,
        "offline_since_monotonic": None,
        "last_error": None,
        "last_success_iso": None,
        "last_event": None,
    }


def calculate_backoff(
    poll_interval: int,
    failure_count: int,
    max_retry_interval: int,
) -> int:
    """Calcula o intervalo exponencial de nova tentativa."""
    exponential_interval = poll_interval * (
        2 ** max(failure_count - 1, 0)
    )

    return min(
        exponential_interval,
        max_retry_interval,
    )


def summarize_request_error(error: Exception) -> str:
    """Gera uma descrição operacional curta do erro."""
    error_text = str(error).lower()

    if "host is unreachable" in error_text:
        return "Host inacessível"

    if "connection refused" in error_text:
        return "Conexão recusada"

    if "timed out" in error_text:
        return "Tempo de conexão esgotado"

    if "name or service not known" in error_text:
        return "Nome ou endereço não encontrado"

    if "no route to host" in error_text:
        return "Sem rota para o equipamento"

    if isinstance(error, requests.HTTPError):
        response = error.response

        if response is not None:
            return f"Erro HTTP {response.status_code}"

        return "Erro HTTP"

    if isinstance(error, requests.Timeout):
        return "Tempo de conexão esgotado"

    if isinstance(error, requests.ConnectionError):
        return "Falha de conexão"

    return type(error).__name__


def mark_reader_offline(
    reader: dict[str, Any],
    runtime: dict[str, Any],
    error: Exception,
    poll_interval: int,
    max_retry_interval: int,
    supervisor_token: str,
    offline_events: list[str],
    request_timeout: int,
) -> None:
    """Marca um leitor como indisponível."""
    runtime["failures"] += 1

    retry_interval = calculate_backoff(
        poll_interval=poll_interval,
        failure_count=runtime["failures"],
        max_retry_interval=max_retry_interval,
    )

    runtime["next_check"] = (
        time.monotonic() + retry_interval
    )

    runtime["last_error"] = str(error)

    reader_name = reader["name"]
    reader_ip = reader["ip"]

    short_error = summarize_request_error(error)

    if not runtime["offline"]:
        runtime["offline"] = True
        runtime["status"] = "offline"
        runtime["offline_since_iso"] = now_iso()
        runtime["offline_since_monotonic"] = (
            time.monotonic()
        )

        LOGGER.warning(
            "[READER][%s] Leitor offline: %s.",
            reader_name,
            short_error,
        )

        LOGGER.debug(
            "[READER][%s] Exceção completa: %r",
            reader_name,
            error,
        )

        offline_payload = {
            "source": "seiden_bridge",
            "reader_id": slugify_entity(reader_name),
            "driver": reader.get("driver", "evo"),
            "reader_name": reader_name,
            "reader_ip": reader_ip,
            "direction": reader.get("direction"),
            "status": "offline",
            "offline_since": runtime["offline_since_iso"],
            "failure_count": runtime["failures"],
            "retry_in_seconds": retry_interval,
            "error": short_error,
            "error_detail": str(error),
        }

        fire_event_names(
            supervisor_token=supervisor_token,
            event_names=offline_events,
            payload=offline_payload,
            request_timeout=request_timeout,
        )

    publish_reader_entity(
        supervisor_token=supervisor_token,
        reader=reader,
        runtime=runtime,
        request_timeout=request_timeout,
    )

    LOGGER.warning(
        "[READER][%s] Tentativa %d falhou. "
        "Nova tentativa em %ss.",
        reader_name,
        runtime["failures"],
        retry_interval,
    )


def mark_reader_online(
    reader: dict[str, Any],
    runtime: dict[str, Any],
    supervisor_token: str,
    online_events: list[str],
    request_timeout: int,
) -> None:
    """Restaura o leitor ao estado online."""
    reader_name = reader["name"]
    reader_ip = reader["ip"]
    previous_status = runtime.get("status", "unknown")

    if runtime["offline"]:
        offline_duration = 0

        if runtime["offline_since_monotonic"] is not None:
            offline_duration = int(
                time.monotonic()
                - runtime["offline_since_monotonic"]
            )

        LOGGER.info(
            "[READER][%s] Leitor online novamente após %ss.",
            reader_name,
            offline_duration,
        )

        online_payload = {
            "source": "seiden_bridge",
            "reader_id": slugify_entity(reader_name),
            "driver": reader.get("driver", "evo"),
            "reader_name": reader_name,
            "reader_ip": reader_ip,
            "direction": reader.get("direction"),
            "status": "online",
            "online_at": now_iso(),
            "offline_since": runtime["offline_since_iso"],
            "offline_duration_seconds": offline_duration,
            "previous_failure_count": runtime["failures"],
        }

        fire_event_names(
            supervisor_token=supervisor_token,
            event_names=online_events,
            payload=online_payload,
            request_timeout=request_timeout,
        )

    runtime["failures"] = 0
    runtime["next_check"] = 0.0
    runtime["offline"] = False
    runtime["status"] = "online"
    runtime["last_success_iso"] = now_iso()
    runtime["offline_since_iso"] = None
    runtime["offline_since_monotonic"] = None
    runtime["last_error"] = None

    if previous_status != "online":
        publish_reader_entity(
            supervisor_token=supervisor_token,
            reader=reader,
            runtime=runtime,
            request_timeout=request_timeout,
        )


def validate_global_config(
    poll_interval: int,
    request_timeout: int,
    max_retry_interval: int,
) -> None:
    """Valida os parâmetros globais."""
    if poll_interval < 1:
        raise RuntimeError(
            "poll_interval deve ser igual ou maior que 1"
        )

    if request_timeout < 1:
        raise RuntimeError(
            "request_timeout deve ser igual ou maior que 1"
        )

    if max_retry_interval < poll_interval:
        raise RuntimeError(
            "max_retry_interval não pode ser menor "
            "que poll_interval"
        )


def validate_reader_structure(readers: list[dict[str, Any]]) -> None:
    """Valida conexões de polling e streaming."""
    valid_interactions = {"passage", "authorization", "authentication", "attendance", "presence", "unlock", "message", "telemetry"}
    for connection in readers:
        for required_field in ("id", "name", "host", "connector"):
            value = connection.get(required_field)
            if value is None or str(value).strip() == "":
                raise RuntimeError(f"Campo obrigatório vazio na conexão: {required_field}")
        connector = str(connection.get("connector", "")).strip().lower()
        if connector not in KNOWN_DRIVERS:
            raise RuntimeError(f"Conector inválido em {connection['name']}: {connector}")
        if connection.get("enabled", True) and connector not in SUPPORTED_DRIVERS:
            raise RuntimeError(
                f"O conector '{connector}' de {connection['name']} ainda não está implementado na versão 0.9.0. "
                "Mantenha a conexão desativada ou selecione EVO/MQTT."
            )
        if not isinstance(connection.get("enabled", True), bool):
            raise RuntimeError(f"Valor enabled inválido na conexão {connection['name']}")

        if connector == "mqtt":
            subscriptions = connection.get("subscriptions")
            if not isinstance(subscriptions, list) or not subscriptions:
                raise RuntimeError(f"A conexão MQTT {connection['name']} exige ao menos uma subscription")
            for subscription in subscriptions:
                if not isinstance(subscription, dict) or not str(subscription.get("topic", "")).strip():
                    raise RuntimeError(f"Subscription MQTT inválida em {connection['name']}")
                qos = int(subscription.get("qos", 0))
                if qos not in (0, 1, 2):
                    raise RuntimeError(f"QoS inválido em {connection['name']}: {qos}")
            continue

        if not str(connection.get("password", "")).strip():
            raise RuntimeError(f"Campo obrigatório vazio na conexão EVO: password")
        interaction = connection.get("interaction_type")
        direction = connection.get("direction")
        if interaction not in valid_interactions:
            raise RuntimeError(f"Tipo de interação inválido em {connection['name']}: {interaction}")
        if interaction == "passage" and direction not in ("in", "out"):
            raise RuntimeError(f"Conexões do tipo passage exigem direction in ou out: {connection['name']}")
        if interaction != "passage" and direction not in (None, "in", "out"):
            raise RuntimeError(f"Direção inválida em {connection['name']}: {direction}")

def find_duplicate_values(
    readers: list[dict[str, Any]],
    field: str,
    normalize_lower: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Retorna os valores duplicados de determinado campo."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for reader in readers:
        value = str(reader[field]).strip()

        if normalize_lower:
            value = value.lower()

        grouped[value].append(reader)

    return {
        value: matches
        for value, matches in grouped.items()
        if len(matches) > 1
    }


def validate_active_reader_duplicates(
    active_readers: list[dict[str, Any]],
) -> None:
    """
    Impede duplicidades operacionais entre leitores ativos.
    """
    duplicate_ips = find_duplicate_values(
        readers=active_readers,
        field="ip",
    )

    if duplicate_ips:
        duplicate_ip = next(iter(duplicate_ips))

        names = ", ".join(
            reader["name"]
            for reader in duplicate_ips[duplicate_ip]
        )

        raise RuntimeError(
            f"IP duplicado entre leitores ativos: "
            f"{duplicate_ip} ({names})"
        )

    duplicate_names = find_duplicate_values(
        readers=active_readers,
        field="name",
        normalize_lower=True,
    )

    if duplicate_names:
        duplicate_name = next(iter(duplicate_names))

        raise RuntimeError(
            f"Nome duplicado entre leitores ativos: "
            f"{duplicate_name}"
        )


def log_disabled_reader_duplicates(
    active_readers: list[dict[str, Any]],
    disabled_readers: list[dict[str, Any]],
) -> None:
    """
    Registra duplicidades envolvendo leitores desativados.

    Essas situações não impedem a inicialização.
    """
    active_ips = {
        str(reader["ip"]).strip()
        for reader in active_readers
    }

    active_names = {
        str(reader["name"]).strip().lower()
        for reader in active_readers
    }

    warned_ips: set[str] = set()
    warned_names: set[str] = set()

    for reader in disabled_readers:
        reader_ip = str(reader["ip"]).strip()
        reader_name = str(reader["name"]).strip()
        normalized_name = reader_name.lower()

        if (
            reader_ip in active_ips
            and reader_ip not in warned_ips
        ):
            LOGGER.warning(
                "[CONFIG] O IP %s é utilizado por um leitor ativo "
                "e também por um leitor desativado. "
                "Isso é permitido enquanto o segundo permanecer "
                "desativado.",
                reader_ip,
            )
            warned_ips.add(reader_ip)

        if (
            normalized_name in active_names
            and normalized_name not in warned_names
        ):
            LOGGER.warning(
                "[CONFIG] O nome '%s' é utilizado por um leitor ativo "
                "e também por um leitor desativado. "
                "Isso é permitido enquanto o segundo permanecer "
                "desativado.",
                reader_name,
            )
            warned_names.add(normalized_name)

    duplicate_disabled_ips = find_duplicate_values(
        readers=disabled_readers,
        field="ip",
    )

    for duplicate_ip, readers in duplicate_disabled_ips.items():
        names = ", ".join(
            reader["name"]
            for reader in readers
        )

        LOGGER.info(
            "[CONFIG] IP repetido apenas entre leitores desativados: "
            "%s (%s). Nenhum conflito operacional.",
            duplicate_ip,
            names,
        )

    duplicate_disabled_names = find_duplicate_values(
        readers=disabled_readers,
        field="name",
        normalize_lower=True,
    )

    for _, readers in duplicate_disabled_names.items():
        names = ", ".join(
            reader["name"]
            for reader in readers
        )

        LOGGER.info(
            "[CONFIG] Nome repetido apenas entre leitores "
            "desativados: %s. Nenhum conflito operacional.",
            names,
        )


def log_reader_summary(
    active_readers: list[dict[str, Any]],
    disabled_readers: list[dict[str, Any]],
    relay_devices: list[dict[str, Any]],
    mqtt_count: int,
    state: dict[str, Any],
    presence_event: str,
    reader_offline_event: str,
    reader_online_event: str,
    poll_interval: int,
    request_timeout: int,
    max_retry_interval: int,
) -> None:
    """Registra o resumo operacional da inicialização."""
    all_reader_devices = list(active_readers) + list(relay_devices)
    active_entry_count = sum(
        1 for reader in all_reader_devices if reader.get("direction") == "in"
    )
    active_exit_count = sum(
        1 for reader in all_reader_devices if reader.get("direction") == "out"
    )

    LOGGER.info("Leitores ativos: %d", len(all_reader_devices))
    LOGGER.info("Leitores HTTP ativos: %d", len(active_readers))
    LOGGER.info("Leitores EVO Relay ativos: %d", len(relay_devices))
    LOGGER.info("Conexões MQTT ativas: %d", mqtt_count)
    LOGGER.info("Leitores desativados: %d", len(disabled_readers))
    LOGGER.info("Leitores ativos de entrada: %d", active_entry_count)
    LOGGER.info("Leitores ativos de saída: %d", active_exit_count)

    LOGGER.info(
        "Evento de presença: %s",
        presence_event,
    )

    LOGGER.info(
        "Evento de leitor offline: %s",
        reader_offline_event,
    )

    LOGGER.info(
        "Evento de leitor online: %s",
        reader_online_event,
    )

    LOGGER.info(
        "Polling normal: %ss",
        poll_interval,
    )

    LOGGER.info(
        "Timeout HTTP: %ss",
        request_timeout,
    )

    LOGGER.info(
        "Backoff máximo: %ss",
        max_retry_interval,
    )

    LOGGER.info(
        "Pessoas dentro restauradas: %d",
        len(state["people_inside"]),
    )

    for reader in disabled_readers:
        LOGGER.info(
            "[READER][%s] %s | direção=%s | "
            "desativado pela configuração",
            reader["name"],
            reader["ip"],
            reader.get("direction"),
        )

    for reader in active_readers:
        LOGGER.info(
            "[READER][%s] %s | direção=%s | ativo",
            reader["name"],
            reader["ip"],
            reader.get("direction"),
        )


def wait_without_active_readers(
    state: dict[str, Any],
    supervisor_token: str,
    request_timeout: int,
    publish_last_photo: bool,
    photo_max_size_mb: int,
    streaming_active: bool = False,
) -> None:
    """Mantém o processo ativo quando não há leitores HTTP para polling."""
    if streaming_active:
        LOGGER.info("Sem leitores HTTP para polling; streaming permanece ativo.")
    else:
        LOGGER.warning("Nenhum leitor está ativo. O Bridge permanecerá em espera.")
    started_monotonic = time.monotonic()

    while True:
        publish_operational_entities(
            supervisor_token=supervisor_token,
            readers=[],
            reader_runtime={},
            state=state,
            started_monotonic=started_monotonic,
            request_timeout=request_timeout,
            publish_last_photo=publish_last_photo,
            photo_max_size_mb=photo_max_size_mb,
        )
        time.sleep(IDLE_SLEEP_SECONDS)


def run_polling_loop(
    readers: list[dict[str, Any]],
    state: dict[str, Any],
    supervisor_token: str,
    bridge_events: list[str],
    connection_offline_events: list[str],
    connection_online_events: list[str],
    poll_interval: int,
    request_timeout: int,
    max_retry_interval: int,
    publish_last_photo: bool,
    photo_max_size_mb: int,
    operation_timezone: str,
) -> None:
    """Executa o loop principal de monitoramento."""
    last_seen: dict[str, str] = {}

    reader_runtime = {
        reader["ip"]: create_reader_runtime_state()
        for reader in readers
    }
    started_monotonic = time.monotonic()
    last_dashboard_publish = 0.0

    publish_operational_entities(
        supervisor_token=supervisor_token,
        readers=readers,
        reader_runtime=reader_runtime,
        state=state,
        started_monotonic=started_monotonic,
        request_timeout=request_timeout,
        publish_last_photo=publish_last_photo,
        photo_max_size_mb=photo_max_size_mb,
    )

    while True:
        loop_started_at = time.monotonic()

        for reader in readers:
            reader_name = reader["name"]
            reader_ip = reader["ip"]
            runtime = reader_runtime[reader_ip]

            if time.monotonic() < runtime["next_check"]:
                continue

            try:
                data = execute_connection_command(
                    connection=reader,
                    command="getlog",
                    request_timeout=request_timeout,
                )

                if not data.get("result"):
                    raise RuntimeError(
                        f"getlog retornou falha: {data}"
                    )

                mark_reader_online(
                    reader=reader,
                    runtime=runtime,
                    supervisor_token=supervisor_token,
                    online_events=connection_online_events,
                    request_timeout=request_timeout,
                )

                records = data.get("record", [])

                if not records:
                    LOGGER.debug(
                        "[READER][%s] Nenhum registro retornado.",
                        reader_name,
                    )
                    continue

                latest = records[0]
                latest_key = record_key(latest)

                if reader_ip not in last_seen:
                    last_seen[reader_ip] = latest_key

                    LOGGER.info(
                        "[READER][%s] Último log inicial: %s",
                        reader_name,
                        latest,
                    )
                    continue

                if latest_key == last_seen[reader_ip]:
                    LOGGER.debug(
                        "[READER][%s] Nenhum novo evento.",
                        reader_name,
                    )
                    continue

                last_seen[reader_ip] = latest_key

                LOGGER.debug(
                    "[READER][%s] Novo log recebido: %s",
                    reader_name,
                    latest,
                )

                if latest.get("event") != 0:
                    LOGGER.warning(
                        "[READER][%s] Evento não "
                        "autorizado/ignorado: código=%s",
                        reader_name,
                        latest.get("event"),
                    )
                    continue

                presence_payload = handle_authorized_record(
                    reader=reader,
                    record=latest,
                    state=state,
                    operation_timezone=operation_timezone,
                )

                runtime["last_event"] = {
                    "user_name": presence_payload["user_name"],
                    "action": presence_payload["action"],
                    "time": presence_payload["time"],
                }

                event_sent = fire_event_names(
                    supervisor_token=supervisor_token,
                    event_names=bridge_events,
                    payload=presence_payload,
                    request_timeout=request_timeout,
                )

                publish_operational_entities(
                    supervisor_token=supervisor_token,
                    readers=readers,
                    reader_runtime=reader_runtime,
                    state=state,
                    started_monotonic=started_monotonic,
                    request_timeout=request_timeout,
                    publish_last_photo=publish_last_photo,
                    photo_max_size_mb=photo_max_size_mb,
                )
                last_dashboard_publish = time.monotonic()

                if event_sent:
                    LOGGER.info(
                        "[READER][%s] %s %s | "
                        "dentro=%d | first=%s | last=%s",
                        reader_name,
                        presence_payload["user_name"],
                        presence_payload["action"],
                        presence_payload[
                            "people_inside_count"
                        ],
                        presence_payload[
                            "is_first_entry"
                        ],
                        presence_payload[
                            "is_last_exit"
                        ],
                    )

            except (
                requests.RequestException,
                json.JSONDecodeError,
                ValueError,
                RuntimeError,
            ) as error:
                mark_reader_offline(
                    reader=reader,
                    runtime=runtime,
                    error=error,
                    poll_interval=poll_interval,
                    max_retry_interval=max_retry_interval,
                    supervisor_token=supervisor_token,
                    offline_events=connection_offline_events,
                    request_timeout=request_timeout,
                )

            except Exception:
                LOGGER.exception(
                    "[READER][%s] Erro inesperado.",
                    reader_name,
                )

        if (
            time.monotonic() - last_dashboard_publish
            >= DASHBOARD_PUBLISH_INTERVAL
        ):
            publish_operational_entities(
                supervisor_token=supervisor_token,
                readers=readers,
                reader_runtime=reader_runtime,
                state=state,
                started_monotonic=started_monotonic,
                request_timeout=request_timeout,
                publish_last_photo=publish_last_photo,
                photo_max_size_mb=photo_max_size_mb,
            )
            last_dashboard_publish = time.monotonic()

        elapsed = time.monotonic() - loop_started_at

        sleep_time = max(
            0.2,
            poll_interval - elapsed,
        )

        time.sleep(sleep_time)


def main() -> None:
    """Inicializa e executa o Seiden Bridge."""
    config = load_config()

    log_level = config.get(
        "log_level",
        DEFAULT_LOG_LEVEL,
    )

    setup_logging(log_level)

    LOGGER.info("Seiden Bridge %s iniciado — arquitetura unificada.", BRIDGE_VERSION)

    LOGGER.info(
        "Nível de log configurado: %s",
        str(log_level).upper(),
    )

    LOGGER.debug(
        "[CONFIG] Configuração carregada: %s",
        sanitize_config_for_log(config),
    )

    (
        all_readers,
        active_readers,
        disabled_readers,
    ) = build_connections_from_config(config)
    evo_relays = build_evo_relay_connections(config)

    state = load_state()
    if bool(config.get("reset_occupancy_state_on_start", False)):
        state = default_state()
        save_state(state)
        LOGGER.warning("[STATE] Estado de ocupação reiniciado por configuração.")

    poll_interval = int(
        config.get(
            "poll_interval",
            DEFAULT_POLL_INTERVAL,
        )
    )

    request_timeout = int(
        config.get(
            "request_timeout",
            DEFAULT_REQUEST_TIMEOUT,
        )
    )

    max_retry_interval = int(
        config.get(
            "max_retry_interval",
            DEFAULT_MAX_RETRY_INTERVAL,
        )
    )

    publish_last_photo = bool(
        config.get("publish_last_photo", True)
    )

    photo_max_size_mb = int(
        config.get("photo_max_size_mb", 5)
    )

    bridge_event = str(config.get("bridge_event", DEFAULT_BRIDGE_EVENT))
    connection_offline_event = str(
        config.get("connection_offline_event", DEFAULT_CONNECTION_OFFLINE_EVENT)
    )
    connection_online_event = str(
        config.get("connection_online_event", DEFAULT_CONNECTION_ONLINE_EVENT)
    )
    # A partir da 0.9.0 existe apenas a arquitetura unificada.
    evo_bridge_events = [bridge_event]
    mqtt_bridge_events = [bridge_event]
    offline_events = [connection_offline_event]
    online_events = [connection_online_event]

    validate_global_config(
        poll_interval=poll_interval,
        request_timeout=request_timeout,
        max_retry_interval=max_retry_interval,
    )

    validate_reader_structure(
        readers=all_readers,
    )

    validate_active_reader_duplicates(
        active_readers=[item for item in active_readers if item.get("connector") == "evo"],
    )

    log_disabled_reader_duplicates(
        active_readers=[item for item in active_readers if item.get("connector") == "evo"],
        disabled_readers=[item for item in disabled_readers if item.get("connector") == "evo"],
    )

    supervisor_token = os.environ.get(
        "SUPERVISOR_TOKEN"
    )

    if not supervisor_token:
        raise RuntimeError(
            "SUPERVISOR_TOKEN não encontrado"
        )

    polling_readers = [item for item in active_readers if item.get("connector") == "evo"]
    mqtt_connections = [item for item in active_readers if item.get("connector") == "mqtt"]

    mqtt_clients = []
    mqtt_state_cache: dict[tuple[str, str], dict[str, Any]] = {}
    mqtt_state_lock = threading.RLock()

    for connection in mqtt_connections:
        connector = get_connector("mqtt")

        def handle_mqtt_event(conn: dict[str, Any], topic: str, payload: Any, raw: bytes) -> None:
            environment_source = find_environment_source(conn, topic)
            measurements = None
            if environment_source is not None:
                measurements = extract_environment_measurements(environment_source, payload)
                if not measurements:
                    LOGGER.warning(
                        "[ENV][%s] mensagem recebida sem medições reconhecidas no tópico %s",
                        environment_source["name"],
                        topic,
                    )
                else:
                    LOGGER.debug(
                        "[ENV][%s] medições normalizadas: %s",
                        environment_source["name"],
                        measurements,
                    )

            state_driver = conn.get("state_driver") or {}
            state_driver_handled = False
            if state_driver.get("enabled") and _mqtt_topic_matches(state_driver.get("topics") or [], topic):
                current = _state_driver_extract(state_driver, payload)
                if current:
                    state_driver_handled = True
                    cache_key = (str(conn["id"]), topic)
                    with mqtt_state_lock:
                        previous = mqtt_state_cache.get(cache_key)
                        if previous is None:
                            # Baseline: retained/startup payloads nunca viram ações falsas.
                            mqtt_state_cache[cache_key] = dict(current)
                            LOGGER.debug(
                                "[MQTT STATE][%s] baseline %s: %s",
                                conn["name"],
                                topic,
                                current,
                            )
                            transitions: list[tuple[str, Any, Any]] = []
                        else:
                            transitions = [
                                (field, previous.get(field), value)
                                for field, value in current.items()
                                if field in previous and previous.get(field) != value
                            ]
                            # Campos que aparecem pela primeira vez entram no baseline sem evento.
                            merged = dict(previous)
                            merged.update(current)
                            mqtt_state_cache[cache_key] = merged

                    prefix = str(state_driver.get("field_prefix") or "state_")
                    device_name = topic.rsplit("/", 1)[-1] if "/" in topic else topic
                    for field, previous_value, current_value in transitions:
                        channel = _state_driver_channel(field, prefix)
                        transition_event = create_mqtt_state_transition_event(
                            connection=conn,
                            topic=topic,
                            device_name=device_name,
                            field=field,
                            channel=channel,
                            previous_state=previous_value,
                            current_state=current_value,
                        )
                        fire_event_names(
                            supervisor_token=supervisor_token,
                            event_names=mqtt_bridge_events,
                            payload=transition_event,
                            request_timeout=request_timeout,
                        )
                        LOGGER.info(
                            "[MQTT STATE][%s] %s · %s: %s → %s",
                            conn["name"],
                            device_name,
                            channel,
                            previous_value,
                            current_value,
                        )

            # Compatibilidade: fontes ambientais continuam publicando sempre.
            # Para tópicos tratados pelo State Driver, o payload bruto pode ser
            # suprimido para reduzir drasticamente volume no barramento.
            publish_raw = (
                environment_source is not None
                or not state_driver_handled
                or bool((conn.get("state_driver") or {}).get("publish_raw", True))
            )
            if publish_raw:
                fire_event_names(
                    supervisor_token=supervisor_token,
                    event_names=mqtt_bridge_events,
                    payload=create_mqtt_event(
                        connection=conn,
                        topic=topic,
                        payload=payload,
                        environment_source=environment_source,
                        measurements=measurements,
                    ),
                    request_timeout=request_timeout,
                )

        client = connector.start(connection, handle_mqtt_event)
        mqtt_clients.append(client)

    relay_connector = EvoRelayConnector()
    relay_threads = []
    for relay in evo_relays:
        def relay_registration(relay_conn, serial, payload):
            device = relay_device_map(relay_conn).get(serial)
            devinfo = payload.get("devinfo") if isinstance(payload.get("devinfo"), dict) else {}
            if device:
                # O `reg` contém a identidade real do terminal. O servidor upstream não deve
                # ser publicado como reader_ip.
                device["ip"] = str(devinfo.get("curip") or "").strip() or device.get("ip")
                device["model"] = devinfo.get("modelname")
                device["firmware"] = devinfo.get("firmware")
                device["mac"] = devinfo.get("mac")
                LOGGER.info(
                    "[EVO RELAY][%s] %s online | serial=%s | ip=%s | direção=%s | modelo=%s | firmware=%s",
                    relay_conn["name"], device["name"], serial, device.get("ip") or "desconhecido",
                    device.get("direction") or "none", devinfo.get("modelname"), devinfo.get("firmware"),
                )
            else:
                LOGGER.warning("[EVO RELAY][%s] Serial não cadastrado conectado: %s", relay_conn["name"], serial)

        def relay_record(relay_conn, serial, record):
            handle_evo_relay_record(
                relay_conn, serial, record, state, supervisor_token, evo_bridge_events,
                request_timeout, photo_max_size_mb, str(config.get("operation_timezone", "UTC")),
            )

        def relay_status(relay_conn, serial, status):
            device = relay_device_map(relay_conn).get(serial)
            if not device:
                return
            fire_event_names(
                supervisor_token=supervisor_token,
                event_names=online_events if status == "online" else offline_events,
                payload={
                    "source": "seiden_bridge", "connector": "evo_relay",
                    "relay_id": relay_conn["id"], "device_serial": serial,
                    "device_name": device["name"], "customer_id": device.get("customer_id"),
                    "site_id": device.get("site_id"), "direction": device.get("direction"),
                    "status": status, "timestamp": now_iso(),
                },
                request_timeout=request_timeout,
            )

        relay_threads.append(relay_connector.start(relay, relay_registration, relay_record, relay_status))

    relay_devices = [device for relay in evo_relays for device in relay.get("devices", [])]
    log_reader_summary(
        active_readers=polling_readers,
        disabled_readers=disabled_readers,
        relay_devices=relay_devices,
        mqtt_count=len(mqtt_connections),
        state=state,
        presence_event=bridge_event,
        reader_offline_event=connection_offline_event,
        reader_online_event=connection_online_event,
        poll_interval=poll_interval,
        request_timeout=request_timeout,
        max_retry_interval=max_retry_interval,
    )

    if not polling_readers:
        if mqtt_connections or evo_relays:
            LOGGER.info("Bridge operando somente com streaming: MQTT=%d EVO Relay=%d", len(mqtt_connections), len(evo_relays))
        wait_without_active_readers(
            state=state,
            supervisor_token=supervisor_token,
            request_timeout=request_timeout,
            publish_last_photo=publish_last_photo,
            photo_max_size_mb=photo_max_size_mb,
            streaming_active=bool(mqtt_connections or evo_relays),
        )
        return

    run_polling_loop(
        readers=polling_readers,
        state=state,
        supervisor_token=supervisor_token,
        bridge_events=evo_bridge_events,
        connection_offline_events=offline_events,
        connection_online_events=online_events,
        poll_interval=poll_interval,
        request_timeout=request_timeout,
        max_retry_interval=max_retry_interval,
        publish_last_photo=publish_last_photo,
        photo_max_size_mb=photo_max_size_mb,
        operation_timezone=str(config.get("operation_timezone", "UTC")),
    )


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        if LOGGER.handlers:
            LOGGER.info(
                "Seiden Bridge encerrado."
            )

    except Exception:
        if not LOGGER.handlers:
            setup_logging(DEFAULT_LOG_LEVEL)

        LOGGER.exception(
            "Falha crítica ao iniciar ou executar "
            "o Seiden Bridge."
        )

        sys.exit(1)
