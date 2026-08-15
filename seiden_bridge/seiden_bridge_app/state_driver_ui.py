"""Ingress configurator for Seiden Bridge State Drivers.

Provides a dynamic UI for:
- MQTT State Driver: selects a subset of already subscribed MQTT topics.
- Home Assistant State Driver: selects HA entities fetched from the Core REST API.

The native Home Assistant app options schema is static and cannot build dynamic
choices from MQTT topics or from the entity registry/state machine. This ingress
UI keeps the YAML options as the single persisted source of truth.
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


def _parse_lines(value: Any) -> list[str]:
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


def _supervisor_request(
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
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
        with request.urlopen(req, timeout=12) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supervisor HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"Falha de comunicação com Supervisor: {exc}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _persist_options(options: dict[str, Any]) -> None:
    result = _supervisor_request(
        "/addons/self/options",
        method="POST",
        payload={"options": options},
    )
    if isinstance(result, dict) and result.get("result") not in (None, "ok"):
        raise RuntimeError(f"Supervisor rejeitou opções: {result}")


def _load_ha_entities() -> list[dict[str, str]]:
    """Fetch current HA states once when the configurator is opened/saved.

    No background polling is created. Each item contains only the fields needed
    by the UI, keeping memory and HTML payload compact.
    """
    raw = _supervisor_request("/core/api/states")
    if not isinstance(raw, list):
        raise RuntimeError("Home Assistant retornou formato inesperado em /api/states")

    entities: list[dict[str, str]] = []
    for state in raw:
        if not isinstance(state, dict):
            continue
        entity_id = str(state.get("entity_id") or "").strip()
        if "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        attributes = state.get("attributes")
        friendly_name = ""
        if isinstance(attributes, dict):
            friendly_name = str(attributes.get("friendly_name") or "").strip()
        entities.append({
            "entity_id": entity_id,
            "domain": domain,
            "friendly_name": friendly_name,
        })

    entities.sort(key=lambda item: (
        item["domain"].lower(),
        (item["friendly_name"] or item["entity_id"]).lower(),
        item["entity_id"].lower(),
    ))
    return entities


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


def _render_mqtt_sections(options: dict[str, Any]) -> str:
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

        selected = set(_parse_lines(conn.get("state_driver_topics")))
        enabled = bool(conn.get("state_driver_enabled", False))
        field_prefix = str(conn.get("state_driver_field_prefix") or "state_")
        publish_raw = bool(conn.get("state_driver_publish_raw", False))

        rows: list[str] = []
        for topic in topics:
            checked = " checked" if topic in selected else ""
            kind = "Zigbee2MQTT" if topic.startswith("zigbee2mqtt/") else "MQTT"
            rows.append(
                f"""
                <label class="item-row">
                  <input type="checkbox" name="mqtt_selected_{index}" value="{html.escape(topic, quote=True)}"{checked}>
                  <span class="item-main">
                    <span class="item-name">{html.escape(_topic_label(topic))}</span>
                    <span class="item-path">{html.escape(topic)}</span>
                  </span>
                  <span class="badge">{kind}</span>
                </label>
                """
            )
        if not rows:
            rows.append('<div class="empty">Nenhum tópico cadastrado nesta conexão MQTT.</div>')

        sections.append(
            f"""
            <section class="card">
              <div class="card-head">
                <div>
                  <h3>{html.escape(conn_name)}</h3>
                  <div class="sub">{html.escape(conn_id)}</div>
                </div>
                <label class="switch-line">
                  <input type="checkbox" name="mqtt_enabled_{index}" value="1"{" checked" if enabled else ""}>
                  <span>State Driver ativo</span>
                </label>
              </div>

              <p class="explain">
                Marque o subconjunto dos tópicos já assinados que deve gerar transições de estado. O campo de compatibilidade do add-on é preenchido automaticamente em formato legível.
              </p>

              <div class="item-list">{''.join(rows)}</div>

              <details>
                <summary>Opções avançadas</summary>
                <div class="advanced-grid">
                  <label>
                    <span>Prefixo dos campos de estado</span>
                    <input type="text" name="mqtt_prefix_{index}" value="{html.escape(field_prefix, quote=True)}">
                  </label>
                  <label class="check-advanced">
                    <input type="checkbox" name="mqtt_publish_raw_{index}" value="1"{" checked" if publish_raw else ""}>
                    <span>Publicar também o payload MQTT bruto</span>
                  </label>
                </div>
              </details>
            </section>
            """
        )

    if not sections:
        return """
        <section class="card">
          <div class="empty">Nenhuma conexão MQTT configurada.</div>
        </section>
        """
    return "".join(sections)


def _render_ha_section(
    options: dict[str, Any],
    ha_entities: list[dict[str, str]],
    ha_error: str = "",
) -> str:
    selected = set(_parse_lines(options.get("ha_state_driver_entities")))
    enabled = bool(options.get("ha_state_driver_enabled", False))
    ignore_states = "\n".join(
        _parse_lines(options.get("ha_state_driver_ignore_states")) or ["unknown", "unavailable"]
    )

    # Preserve selected IDs in the display even if an entity is temporarily absent.
    known_ids = {item["entity_id"] for item in ha_entities}
    for missing in sorted(selected - known_ids):
        domain = missing.split(".", 1)[0] if "." in missing else ""
        ha_entities.append({
            "entity_id": missing,
            "domain": domain,
            "friendly_name": "(entidade selecionada, indisponível no momento)",
        })

    rows: list[str] = []
    for item in ha_entities:
        entity_id = item["entity_id"]
        friendly = item["friendly_name"] or entity_id
        domain = item["domain"]
        checked = " checked" if entity_id in selected else ""
        search_text = f"{domain} {entity_id} {friendly}".lower()
        rows.append(
            f"""
            <label class="item-row ha-entity"
                   data-domain="{html.escape(domain.lower(), quote=True)}"
                   data-search="{html.escape(search_text, quote=True)}">
              <input type="checkbox" name="ha_selected" value="{html.escape(entity_id, quote=True)}"{checked}>
              <span class="item-main">
                <span class="item-name">{html.escape(friendly)}</span>
                <span class="item-path">{html.escape(entity_id)}</span>
              </span>
              <span class="badge">{html.escape(domain)}</span>
            </label>
            """
        )

    if not rows:
        rows.append('<div class="empty">Nenhuma entidade disponível no Home Assistant.</div>')

    error_html = ""
    if ha_error:
        error_html = (
            '<div class="notice error compact">'
            + html.escape(ha_error)
            + '</div>'
        )

    return f"""
    <section class="card">
      <div class="card-head">
        <div>
          <h3>Home Assistant</h3>
          <div class="sub">HA State Driver</div>
        </div>
        <label class="switch-line">
          <input type="checkbox" name="ha_enabled" value="1"{" checked" if enabled else ""}>
          <span>State Driver ativo</span>
        </label>
      </div>

      <p class="explain">
        Selecione as entidades cujo estado deve ser normalizado pelo Bridge. A lista é lida
        diretamente do Home Assistant somente ao abrir esta tela.
      </p>

      {error_html}

      <div class="filters">
        <label>
          <span>Filtrar por domínio</span>
          <input id="ha-domain-filter" type="text" placeholder="Ex.: light, switch, binary_"
                 autocomplete="off" spellcheck="false">
        </label>
        <label>
          <span>Buscar entidade</span>
          <input id="ha-search-filter" type="text" placeholder="Nome ou entity_id"
                 autocomplete="off" spellcheck="false">
        </label>
      </div>

      <div class="filter-meta">
        <span id="ha-visible-count"></span>
        <button class="link-button" type="button" id="ha-clear-filters">Limpar filtros</button>
      </div>

      <div class="item-list ha-list">{''.join(rows)}</div>

      <details>
        <summary>Opções avançadas</summary>
        <div class="advanced-grid">
          <label>
            <span>Estados ignorados (um por linha)</span>
            <textarea name="ha_ignore_states" rows="3">{html.escape(ignore_states)}</textarea>
          </label>
        </div>
      </details>
    </section>
    """


def _render(
    options: dict[str, Any],
    ha_entities: list[dict[str, str]],
    notice: str = "",
    error_message: str = "",
    ha_error: str = "",
) -> str:
    notice_html = f'<div class="notice ok">{html.escape(notice)}</div>' if notice else ""
    error_html = f'<div class="notice error">{html.escape(error_message)}</div>' if error_message else ""

    mqtt_html = _render_mqtt_sections(options)
    ha_html = _render_ha_section(options, list(ha_entities), ha_error=ha_error)

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Seiden Bridge — State Drivers</title>
<style>
:root {{
  color-scheme: light dark;
  --bg: #f6f7f9; --card: #fff; --text: #1f2937; --muted: #667085;
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
.wrap {{ max-width: 1040px; margin: 0 auto; padding: 28px 18px 48px; }}
.hero {{ margin-bottom: 22px; }}
h1 {{ margin: 0 0 7px; font-size: 27px; letter-spacing: -.02em; }}
h2 {{ margin: 27px 0 7px; font-size: 20px; }}
h3 {{ margin: 0; font-size: 18px; }}
.hero p, .section-intro {{ margin: 0; color: var(--muted); line-height: 1.5; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 18px; margin: 14px 0; box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
.card-head {{ display: flex; justify-content: space-between; gap: 18px; align-items: center;
  border-bottom: 1px solid var(--line); padding-bottom: 14px; }}
.sub {{ margin-top: 4px; color: var(--muted); font-size: 12px; }}
.explain {{ color: var(--muted); margin: 15px 0 11px; line-height: 1.45; }}
.switch-line, .check-advanced {{ display: flex; align-items: center; gap: 9px; white-space: nowrap; }}
input[type=checkbox] {{ width: 18px; height: 18px; accent-color: var(--accent); }}
.item-list {{ border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
.ha-list {{ max-height: 520px; overflow-y: auto; }}
.item-row {{ display: flex; align-items: center; gap: 12px; padding: 12px 13px;
  border-bottom: 1px solid var(--line); cursor: pointer; }}
.item-row:last-child {{ border-bottom: none; }}
.item-row:hover {{ background: var(--accent-soft); }}
.item-main {{ flex: 1; min-width: 0; }}
.item-name {{ display: block; font-weight: 600; }}
.item-path {{ display: block; margin-top: 3px; color: var(--muted);
  font: 12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; overflow-wrap: anywhere; }}
.badge {{ color: var(--muted); border: 1px solid var(--line); border-radius: 999px;
  padding: 4px 8px; font-size: 11px; }}
.filters {{ display: grid; grid-template-columns: minmax(180px,.7fr) minmax(240px,1.3fr);
  gap: 12px; margin: 13px 0 8px; }}
.filters label > span, .advanced-grid label > span {{ display: block; margin-bottom: 6px;
  color: var(--muted); font-size: 13px; }}
input[type=text], textarea {{ width: 100%; padding: 10px 11px; border: 1px solid var(--line);
  border-radius: 8px; background: var(--card); color: var(--text); font: inherit; }}
textarea {{ resize: vertical; }}
.filter-meta {{ display: flex; justify-content: space-between; align-items: center;
  color: var(--muted); font-size: 12px; margin: 0 0 8px; min-height: 28px; }}
.link-button {{ border: 0; background: transparent; color: var(--accent); padding: 5px 0;
  cursor: pointer; font: inherit; }}
details {{ margin-top: 13px; }}
summary {{ cursor: pointer; color: var(--muted); }}
.advanced-grid {{ margin-top: 13px; display: grid; grid-template-columns: 1fr; gap: 14px; }}
.actions {{ display: flex; justify-content: flex-end; margin-top: 22px; }}
.primary {{ border: 0; border-radius: 9px; background: var(--accent); color: #fff;
  font-weight: 700; padding: 11px 17px; cursor: pointer; }}
.notice {{ border-radius: 10px; padding: 12px 14px; margin: 12px 0; line-height: 1.4; }}
.notice.compact {{ margin: 10px 0; }}
.notice.ok {{ background: var(--ok-bg); color: var(--ok); }}
.notice.error {{ background: var(--err-bg); color: var(--err); }}
.empty {{ padding: 16px; color: var(--muted); }}
.footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; line-height: 1.5; }}
.hidden-by-filter {{ display: none !important; }}
@media (max-width: 680px) {{
  .card-head {{ align-items: flex-start; flex-direction: column; }}
  .switch-line {{ white-space: normal; }}
  .badge {{ display: none; }}
  .filters {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>State Drivers</h1>
    <p>
      Normalize mudanças de estado de diferentes tecnologias antes que elas cheguem aos módulos
      analíticos da Seiden One.
    </p>
  </div>

  {notice_html}
  {error_html}

  <form method="post">
    <input type="hidden" name="csrf" value="{html.escape(_CSRF_TOKEN, quote=True)}">

    <h2>MQTT State Driver</h2>
    <p class="section-intro">Selecione tópicos já cadastrados nas conexões MQTT.</p>
    {mqtt_html}

    <h2>HA State Driver</h2>
    <p class="section-intro">Selecione entidades diretamente do Home Assistant.</p>
    {ha_html}

    <div class="actions">
      <button class="primary" type="submit">Salvar e reiniciar Bridge</button>
    </div>
  </form>

  <div class="footer">
    A tela consulta as entidades do Home Assistant apenas quando é aberta ou salva. O HA State
    Driver em execução continua orientado a eventos via WebSocket, sem polling.
  </div>
</div>

<script>
(function() {{
  const domainInput = document.getElementById('ha-domain-filter');
  const searchInput = document.getElementById('ha-search-filter');
  const clearButton = document.getElementById('ha-clear-filters');
  const count = document.getElementById('ha-visible-count');
  const rows = Array.from(document.querySelectorAll('.ha-entity'));

  function normalize(value) {{
    return (value || '').trim().toLowerCase();
  }}

  function update() {{
    const domain = normalize(domainInput && domainInput.value);
    const search = normalize(searchInput && searchInput.value);
    let visible = 0;

    rows.forEach(function(row) {{
      const rowDomain = row.dataset.domain || '';
      const rowSearch = row.dataset.search || '';
      // Prefix matching intentionally allows "binary_" -> "binary_sensor".
      const matchesDomain = !domain || rowDomain.startsWith(domain);
      const matchesSearch = !search || rowSearch.includes(search);
      const show = matchesDomain && matchesSearch;
      row.classList.toggle('hidden-by-filter', !show);
      if (show) visible += 1;
    }});

    if (count) count.textContent = visible + ' de ' + rows.length + ' entidades exibidas';
  }}

  if (domainInput) domainInput.addEventListener('input', update);
  if (searchInput) searchInput.addEventListener('input', update);
  if (clearButton) clearButton.addEventListener('click', function() {{
    if (domainInput) domainInput.value = '';
    if (searchInput) searchInput.value = '';
    update();
  }});
  update();
}})();
</script>
</body>
</html>"""


def _read_form(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0 or length > 2_000_000:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", errors="replace")
    return parse.parse_qs(raw, keep_blank_values=True)


def _apply_mqtt_form(options: dict[str, Any], form: dict[str, list[str]]) -> None:
    mqtt_connections = options.get("mqtt_connections")
    if not isinstance(mqtt_connections, list):
        mqtt_connections = []

    for index, conn in enumerate(mqtt_connections):
        if not isinstance(conn, dict):
            continue

        available = conn.get("topics") or []
        if not isinstance(available, list):
            available = []
        available_topics = [str(t).strip() for t in available if str(t).strip()]
        available_set = set(available_topics)

        selected_values = form.get(f"mqtt_selected_{index}", [])
        selected = [
            topic for topic in available_topics
            if topic in selected_values and topic in available_set
        ]

        conn["state_driver_enabled"] = form.get(f"mqtt_enabled_{index}", ["0"])[0] == "1"
        conn["state_driver_topics"] = ", ".join(selected)

        prefix = form.get(
            f"mqtt_prefix_{index}",
            [str(conn.get("state_driver_field_prefix") or "state_")],
        )[0].strip()
        conn["state_driver_field_prefix"] = prefix or "state_"
        conn["state_driver_publish_raw"] = (
            form.get(f"mqtt_publish_raw_{index}", ["0"])[0] == "1"
        )


def _apply_ha_form(
    options: dict[str, Any],
    form: dict[str, list[str]],
    ha_entities: list[dict[str, str]],
) -> None:
    available_ids = {item["entity_id"] for item in ha_entities}
    selected_values = [str(item).strip() for item in form.get("ha_selected", []) if str(item).strip()]

    # Also allow entities that were already selected but are temporarily absent.
    existing_selected = set(_parse_lines(options.get("ha_state_driver_entities")))
    allowed_ids = available_ids | existing_selected
    selected = list(dict.fromkeys(item for item in selected_values if item in allowed_ids))

    options["ha_state_driver_enabled"] = form.get("ha_enabled", ["0"])[0] == "1"
    options["ha_state_driver_entities"] = "\n".join(selected)

    ignore_states = _parse_lines(form.get("ha_ignore_states", [""])[0])
    options["ha_state_driver_ignore_states"] = "\n".join(
        ignore_states or ["unknown", "unavailable"]
    )


def _load_entities_safely() -> tuple[list[dict[str, str]], str]:
    try:
        return _load_ha_entities(), ""
    except Exception as exc:
        LOGGER.warning("[STATE UI] Não foi possível listar entidades HA: %s", exc)
        return [], f"Não foi possível consultar as entidades do Home Assistant: {exc}"


class StateDriverHandler(BaseHTTPRequestHandler):
    server_version = "SeidenStateUI/0.18.0"

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
        entities, ha_error = _load_entities_safely()
        self._send_html(_render(_load_options(), entities, ha_error=ha_error))

    def do_POST(self) -> None:
        if not self._allowed_client():
            self.send_error(403)
            return

        form = _read_form(self)
        csrf = form.get("csrf", [""])[0]
        if not secrets.compare_digest(csrf, _CSRF_TOKEN):
            entities, ha_error = _load_entities_safely()
            self._send_html(
                _render(
                    _load_options(),
                    entities,
                    error_message="Sessão expirada. Reabra a página e tente novamente.",
                    ha_error=ha_error,
                ),
                403,
            )
            return

        options = _load_options()

        # Saving HA selections is intentionally blocked if HA cannot be queried.
        # This prevents an API outage from silently wiping a valid entity list.
        try:
            entities = _load_ha_entities()
        except Exception as exc:
            LOGGER.error("[STATE UI] Salvamento cancelado: lista de entidades HA indisponível: %s", exc)
            self._send_html(
                _render(
                    options,
                    [],
                    error_message=(
                        "Configuração não foi alterada porque o Home Assistant não pôde "
                        f"ser consultado: {exc}"
                    ),
                    ha_error="A lista de entidades está temporariamente indisponível.",
                ),
                503,
            )
            return

        try:
            _apply_mqtt_form(options, form)
            _apply_ha_form(options, form, entities)
            _persist_options(options)
        except Exception as exc:
            LOGGER.error("[STATE UI] Falha ao salvar configuração: %s", exc)
            self._send_html(
                _render(options, entities, error_message=f"Não foi possível salvar: {exc}"),
                500,
            )
            return

        LOGGER.info("[STATE UI] State Drivers atualizados; reinício solicitado.")
        self._send_html(
            _render(
                options,
                entities,
                notice="Configuração salva. O Seiden Bridge será reiniciado para aplicar as alterações.",
            )
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
    LOGGER.info("[STATE UI] Configurador de State Drivers disponível via Ingress na porta %d.", INGRESS_PORT)
    return server
