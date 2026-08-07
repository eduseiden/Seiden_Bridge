"""Ingress configurator for Seiden Bridge MQTT State Driver.

The native Home Assistant app options schema is static and cannot render a
dynamic multi-select populated from another option (`topics`). This ingress UI
reads the already configured MQTT topics and lets the operator select the exact
subset handled by the State Driver.

Selections are persisted back to the app options through the official
Supervisor API. The Bridge core continues to consume the same
`state_driver_topics` option, so there is no second source of truth.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, parse, request

LOGGER = logging.getLogger("seiden_bridge")
OPTIONS_PATH = Path("/data/options.json")
SUPERVISOR_BASE = "http://supervisor"
INGRESS_PORT = 8099

_CSRF_TOKEN = secrets.token_urlsafe(32)
_SERVER: ThreadingHTTPServer | None = None


def _parse_topic_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[\n,;]+", value) if item.strip()]
    return []


def _load_options() -> dict[str, Any]:
    try:
        with OPTIONS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.error("[STATE UI] Falha ao ler options.json: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _supervisor_request(path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN indisponível")

    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = request.Request(
        f"{SUPERVISOR_BASE}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supervisor HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"Falha de comunicação com Supervisor: {exc}") from exc

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _persist_options(options: dict[str, Any]) -> None:
    result = _supervisor_request(
        "/addons/self/options",
        method="POST",
        payload={"options": options},
    )
    if result.get("result") not in (None, "ok"):
        raise RuntimeError(f"Supervisor rejeitou opções: {result}")


def _restart_later(delay: float = 1.25) -> None:
    def _worker() -> None:
        time.sleep(delay)
        try:
            _supervisor_request("/addons/self/restart", method="POST", payload={})
        except Exception as exc:
            LOGGER.error("[STATE UI] Opções salvas, mas restart automático falhou: %s", exc)

    threading.Thread(target=_worker, name="seiden-state-ui-restart", daemon=True).start()


def _topic_label(topic: str) -> str:
    if topic.startswith("zigbee2mqtt/"):
        return topic[len("zigbee2mqtt/"):]
    return topic


def _render(options: dict[str, Any], notice: str = "", error_message: str = "") -> str:
    mqtt_connections = options.get("mqtt_connections") or []
    if not isinstance(mqtt_connections, list):
        mqtt_connections = []

    sections: list[str] = []
    for index, conn in enumerate(mqtt_connections):
        if not isinstance(conn, dict):
            continue

        conn_id = str(conn.get("id") or f"mqtt_{index}")
        conn_name = str(conn.get("name") or conn_id)
        topics = conn.get("topics") or []
        if not isinstance(topics, list):
            topics = []
        topics = [str(t).strip() for t in topics if str(t).strip()]

        selected = set(_parse_topic_lines(conn.get("state_driver_topics")))
        enabled = bool(conn.get("state_driver_enabled", False))
        field_prefix = str(conn.get("state_driver_field_prefix") or "state_")
        publish_raw = bool(conn.get("state_driver_publish_raw", False))

        checkboxes: list[str] = []
        for topic in topics:
            checked = " checked" if topic in selected else ""
            kind = "Zigbee2MQTT" if topic.startswith("zigbee2mqtt/") else "MQTT"
            checkboxes.append(
                f"""
                <label class="topic-row">
                  <input type="checkbox" name="selected_{index}" value="{html.escape(topic, quote=True)}"{checked}>
                  <span class="topic-main">
                    <span class="topic-name">{html.escape(_topic_label(topic))}</span>
                    <span class="topic-path">{html.escape(topic)}</span>
                  </span>
                  <span class="badge">{kind}</span>
                </label>
                """
            )

        if not checkboxes:
            checkboxes.append('<div class="empty">Nenhum tópico cadastrado nesta conexão MQTT.</div>')

        sections.append(
            f"""
            <section class="card">
              <div class="card-head">
                <div>
                  <h2>{html.escape(conn_name)}</h2>
                  <div class="sub">{html.escape(conn_id)}</div>
                </div>
                <label class="switch-line">
                  <input type="checkbox" name="enabled_{index}" value="1"{" checked" if enabled else ""}>
                  <span>State Driver ativo</span>
                </label>
              </div>

              <p class="explain">
                Selecione, dentre os tópicos já assinados por esta conexão, quais devem gerar
                eventos normalizados de transição de estado.
              </p>

              <div class="topic-list">
                {''.join(checkboxes)}
              </div>

              <details>
                <summary>Opções avançadas</summary>
                <div class="advanced-grid">
                  <label>
                    <span>Prefixo dos campos de estado</span>
                    <input type="text" name="prefix_{index}" value="{html.escape(field_prefix, quote=True)}">
                  </label>
                  <label class="check-advanced">
                    <input type="checkbox" name="publish_raw_{index}" value="1"{" checked" if publish_raw else ""}>
                    <span>Publicar também o payload MQTT bruto</span>
                  </label>
                </div>
              </details>
            </section>
            """
        )

    if not sections:
        sections.append(
            """
            <section class="card">
              <div class="empty">Nenhuma conexão MQTT configurada em mqtt_connections.</div>
            </section>
            """
        )

    notice_html = f'<div class="notice ok">{html.escape(notice)}</div>' if notice else ""
    error_html = f'<div class="notice error">{html.escape(error_message)}</div>' if error_message else ""

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Seiden Bridge — MQTT State Driver</title>
<style>
:root {{
  color-scheme: light dark;
  --bg: #f6f7f9; --card: #ffffff; --text: #1f2937; --muted: #667085;
  --line: #e5e7eb; --accent: #1688d4; --accent-soft: #eaf5fd;
  --ok: #166534; --ok-bg: #ecfdf3; --err: #991b1b; --err-bg: #fef2f2;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #111318; --card: #1b1e24; --text: #f3f4f6; --muted: #a1a7b3;
    --line: #30343d; --accent-soft: #102a3c; --ok: #86efac; --ok-bg: #102619;
    --err: #fca5a5; --err-bg: #351515;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }}
.wrap {{ max-width: 980px; margin: 0 auto; padding: 28px 18px 48px; }}
.hero {{ margin-bottom: 22px; }}
h1 {{ margin: 0 0 7px; font-size: 26px; letter-spacing: -.02em; }}
.hero p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 18px; margin: 14px 0; box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
.card-head {{ display: flex; justify-content: space-between; gap: 18px; align-items: center;
  border-bottom: 1px solid var(--line); padding-bottom: 14px; }}
h2 {{ margin: 0; font-size: 18px; }}
.sub {{ margin-top: 4px; color: var(--muted); font-size: 12px; }}
.explain {{ color: var(--muted); margin: 15px 0 11px; line-height: 1.45; }}
.switch-line, .check-advanced {{ display: flex; align-items: center; gap: 9px; white-space: nowrap; }}
input[type=checkbox] {{ width: 18px; height: 18px; accent-color: var(--accent); }}
.topic-list {{ border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
.topic-row {{ display: flex; align-items: center; gap: 12px; padding: 12px 13px;
  border-bottom: 1px solid var(--line); cursor: pointer; }}
.topic-row:last-child {{ border-bottom: none; }}
.topic-row:hover {{ background: var(--accent-soft); }}
.topic-main {{ flex: 1; min-width: 0; }}
.topic-name {{ display: block; font-weight: 600; }}
.topic-path {{ display: block; margin-top: 3px; color: var(--muted);
  font: 12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; overflow-wrap: anywhere; }}
.badge {{ color: var(--muted); border: 1px solid var(--line); border-radius: 999px;
  padding: 4px 8px; font-size: 11px; }}
details {{ margin-top: 13px; }}
summary {{ cursor: pointer; color: var(--muted); }}
.advanced-grid {{ margin-top: 13px; display: grid; grid-template-columns: 1fr; gap: 14px; }}
.advanced-grid label > span {{ display: block; margin-bottom: 6px; color: var(--muted); font-size: 13px; }}
input[type=text] {{ width: 100%; padding: 10px 11px; border: 1px solid var(--line);
  border-radius: 8px; background: var(--card); color: var(--text); }}
.actions {{ display: flex; justify-content: flex-end; margin-top: 18px; }}
button {{ border: 0; border-radius: 9px; background: var(--accent); color: white;
  font-weight: 700; padding: 11px 17px; cursor: pointer; }}
.notice {{ border-radius: 10px; padding: 12px 14px; margin: 12px 0; line-height: 1.4; }}
.notice.ok {{ background: var(--ok-bg); color: var(--ok); }}
.notice.error {{ background: var(--err-bg); color: var(--err); }}
.empty {{ padding: 16px; color: var(--muted); }}
.footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; line-height: 1.5; }}
@media (max-width: 640px) {{
  .card-head {{ align-items: flex-start; flex-direction: column; }}
  .switch-line {{ white-space: normal; }}
  .badge {{ display: none; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>MQTT State Driver</h1>
    <p>Os tópicos abaixo vêm diretamente de <strong>topics</strong>. A seleção é sempre um
    subconjunto da assinatura MQTT, eliminando duplicidade e erro de digitação.</p>
  </div>

  {notice_html}
  {error_html}

  <form method="post">
    <input type="hidden" name="csrf" value="{html.escape(_CSRF_TOKEN, quote=True)}">
    {''.join(sections)}
    <div class="actions"><button type="submit">Salvar e reiniciar Bridge</button></div>
  </form>

  <div class="footer">
    Primeiro payload de cada canal continua sendo apenas baseline. Eventos são gerados somente em
    mudanças reais de estado.
  </div>
</div>
</body>
</html>"""


def _read_form(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0 or length > 1_000_000:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    return parse.parse_qs(raw, keep_blank_values=True)


def _apply_form(options: dict[str, Any], form: dict[str, list[str]]) -> dict[str, Any]:
    mqtt_connections = options.get("mqtt_connections")
    if not isinstance(mqtt_connections, list):
        raise ValueError("mqtt_connections inválido")

    for index, conn in enumerate(mqtt_connections):
        if not isinstance(conn, dict):
            continue

        available = conn.get("topics") or []
        if not isinstance(available, list):
            available = []
        available_topics = [str(t).strip() for t in available if str(t).strip()]
        available_set = set(available_topics)

        selected_values = form.get(f"selected_{index}", [])
        selected = [
            topic for topic in available_topics
            if topic in selected_values and topic in available_set
        ]

        conn["state_driver_enabled"] = form.get(f"enabled_{index}", ["0"])[0] == "1"
        conn["state_driver_topics"] = "\n".join(selected)

        prefix = form.get(
            f"prefix_{index}",
            [str(conn.get("state_driver_field_prefix") or "state_")]
        )[0].strip()
        conn["state_driver_field_prefix"] = prefix or "state_"
        conn["state_driver_publish_raw"] = form.get(f"publish_raw_{index}", ["0"])[0] == "1"

    return options


class StateDriverHandler(BaseHTTPRequestHandler):
    server_version = "SeidenStateUI/0.14.3"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("[STATE UI] " + fmt, *args)

    def _allowed_client(self) -> bool:
        client_ip = self.client_address[0]
        return client_ip in {"172.30.32.2", "127.0.0.1", "::1"}

    def _send_html(self, page: str, status: int = 200) -> None:
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._allowed_client():
            self.send_error(403)
            return
        self._send_html(_render(_load_options()))

    def do_POST(self) -> None:
        if not self._allowed_client():
            self.send_error(403)
            return

        form = _read_form(self)
        csrf = form.get("csrf", [""])[0]
        if not secrets.compare_digest(csrf, _CSRF_TOKEN):
            self._send_html(
                _render(_load_options(), error_message="Sessão expirada. Reabra a página e tente novamente."),
                403
            )
            return

        options = _load_options()
        try:
            updated = _apply_form(options, form)
            _persist_options(updated)
        except Exception as exc:
            LOGGER.error("[STATE UI] Falha ao salvar configuração: %s", exc)
            self._send_html(_render(options, error_message=f"Não foi possível salvar: {exc}"), 500)
            return

        LOGGER.info("[STATE UI] Configuração do MQTT State Driver atualizada; reinício solicitado.")
        self._send_html(
            _render(updated, notice="Configuração salva. O Seiden Bridge será reiniciado para aplicar as alterações.")
        )
        _restart_later()


def start_state_driver_ui() -> ThreadingHTTPServer | None:
    global _SERVER
    if _SERVER is not None:
        return _SERVER

    try:
        server = ThreadingHTTPServer(("0.0.0.0", INGRESS_PORT), StateDriverHandler)
        server.daemon_threads = True
    except OSError as exc:
        LOGGER.error("[STATE UI] Não foi possível iniciar UI na porta %d: %s", INGRESS_PORT, exc)
        return None

    thread = threading.Thread(
        target=server.serve_forever,
        name="seiden-state-driver-ui",
        daemon=True,
    )
    thread.start()
    _SERVER = server
    LOGGER.info("[STATE UI] Configurador MQTT State Driver disponível via Ingress na porta %d.", INGRESS_PORT)
    return server
