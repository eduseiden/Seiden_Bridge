"""EVO WebSocket relay connector for Seiden Bridge 0.14.1.1."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

LOGGER = logging.getLogger("seiden_bridge")


class EvoRelayConnector:
    connector_id = "evo_relay"

    def start(
        self,
        relay: dict[str, Any],
        on_registration: Callable[[dict[str, Any], str, dict[str, Any]], None],
        on_record: Callable[[dict[str, Any], str, dict[str, Any]], None],
        on_status: Callable[[dict[str, Any], str, str], None] | None = None,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=lambda: asyncio.run(self._serve(relay, on_registration, on_record, on_status)),
            name=f"evo-relay-{relay['id']}",
            daemon=True,
        )
        thread.start()
        return thread

    async def _serve(self, relay, on_registration, on_record, on_status):
        endpoint = relay["endpoint"]
        upstream_path = str(endpoint.get("path") or "/")
        if not upstream_path.startswith("/"):
            upstream_path = "/" + upstream_path
        upstream_url = f"{endpoint.get('scheme','ws')}://{endpoint['host']}:{endpoint.get('port',7788)}{upstream_path}"
        listen_host = relay.get("listen_host", "0.0.0.0")
        listen_port = int(relay.get("listen_port", 7788))

        LOGGER.info(
            "[EVO RELAY][%s] Escutando ws://%s:%s → %s",
            relay["name"], listen_host, listen_port, upstream_url,
        )

        async def handler(device_ws):
            serial = None
            peer = getattr(device_ws, "remote_address", None)
            LOGGER.info("[EVO RELAY][%s] Terminal conectado: %s", relay["name"], peer)
            try:
                async with connect(
                    upstream_url,
                    compression=None,
                    ping_interval=None,
                    ping_timeout=None,
                    max_size=None,
                    proxy=None,
                ) as upstream_ws:

                    async def device_to_server():
                        nonlocal serial
                        async for message in device_ws:
                            # Transparency first: forward exact frame before analysis.
                            await upstream_ws.send(message)
                            if not isinstance(message, str):
                                continue
                            try:
                                payload = json.loads(message)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(payload, dict):
                                continue
                            cmd = str(payload.get("cmd") or "").lower()
                            candidate = str(payload.get("sn") or serial or "").strip()
                            if candidate:
                                serial = candidate
                            if cmd == "reg" and serial:
                                await asyncio.to_thread(on_registration, relay, serial, payload)
                                if on_status:
                                    await asyncio.to_thread(on_status, relay, serial, "online")
                            elif cmd == "sendlog" and serial:
                                records = payload.get("record") or []
                                if isinstance(records, list):
                                    for record in records:
                                        if isinstance(record, dict):
                                            await asyncio.to_thread(on_record, relay, serial, record)

                    async def server_to_device():
                        async for message in upstream_ws:
                            await device_ws.send(message)

                    tasks = {asyncio.create_task(device_to_server()), asyncio.create_task(server_to_device())}
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    await asyncio.gather(*done, return_exceptions=True)
            except Exception:
                LOGGER.exception("[EVO RELAY][%s] Falha na sessão %s", relay["name"], peer)
            finally:
                if serial and on_status:
                    await asyncio.to_thread(on_status, relay, serial, "offline")

        async with serve(
            handler,
            listen_host,
            listen_port,
            compression=None,
            ping_interval=None,
            ping_timeout=None,
            max_size=None,
        ):
            await asyncio.Future()
