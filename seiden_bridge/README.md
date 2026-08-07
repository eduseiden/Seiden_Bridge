# Seiden Bridge 0.14.1.1

Camada de integração do Seiden One. Captura dados de múltiplas origens, normaliza-os no schema canônico 2.0 e publica eventos no Home Assistant.


## MQTT State Driver — 0.14.1.1

A versão 0.14.1.1 torna a seleção de tópicos do State Driver **explícita e isolada**. `topics` continua definindo tudo que a conexão MQTT recebe; `state_driver_topics` define somente quais desses tópicos podem gerar `state_transition`.

Isso evita que sensores, fontes TCA, `seiden/lca/interactions` ou qualquer tópico futuro com campos semelhantes sejam interpretados acidentalmente pelo State Driver.

Exemplo recomendado:

```yaml
mqtt_connections:
  - id: mqtt_casa
    name: MQTT Casa
    enabled: true
    host: core-mosquitto
    port: 1883
    username: mqtt_user
    password: SUA_SENHA
    event_type: mqtt.message_received

    topics:
      - zigbee2mqtt/SensorPortaGeladeira
      - seiden/tca/sources/energia_geladeira
      - seiden/lca/interactions
      - zigbee2mqtt/Interruptor Sala
      - zigbee2mqtt/Interruptor Suite Sacada

    state_driver_enabled: true
    state_driver_topics:
      - zigbee2mqtt/Interruptor Sala
      - zigbee2mqtt/Interruptor Suite Sacada
    state_driver_field_prefix: state_
    state_driver_publish_raw: false
```

**Comportamento:**

- tópicos presentes apenas em `topics` continuam no fluxo MQTT normal;
- somente tópicos que também aparecem em `state_driver_topics` são analisados pelo State Driver;
- `seiden/lca/interactions` e fontes TCA não são afetados;
- `state_driver_publish_raw: false` suprime o payload bruto **somente quando aquele tópico foi efetivamente tratado pelo State Driver**;
- o primeiro payload de cada tópico estabelece baseline e não gera transição;
- mensagens em que apenas `last_seen`, `linkquality` ou outros campos mudaram geram zero eventos de estado.

Se `state_driver_enabled: true` for usado sem `state_driver_topics`, o driver é desabilitado de forma segura e o comportamento MQTT legado é preservado.

### Ajustes 0.14.0

- `reader_ip` do EVO Relay passa a vir de `devinfo.curip` recebido no `reg`; o servidor upstream fica separado em `relay_server` e `relay_port`.
- O resumo de inicialização conta leitores EVO Relay e conexões MQTT corretamente.
- O Bridge não exibe mais o aviso de ausência de leitores quando está operando somente por streaming.
- Nova opção `reset_occupancy_state_on_start` (padrão `false`) permite limpar, de forma explícita, estado persistido de ocupação em ambientes de teste/migração.
- A foto do EVO Relay continua publicada em `photo_url` no mesmo `seiden_bridge_event`, mantendo o contrato de entrada do Vision.

## EVO Relay WebSocket

A versão 0.14.0 consolida o modo EVO Relay introduzido na 0.13.0: o Bridge pode receber conexões WebSocket dos faciais, encaminhá-las de forma transparente ao servidor EVO existente e, ao mesmo tempo, extrair os eventos `sendlog` e suas imagens Base64.

O servidor/porta são configurados uma única vez e vários equipamentos são associados pelo `serial_number`. Cada equipamento pode ter cliente, site, nome e direção operacional `in`, `out` ou `none`.

```yaml
evo_relay_connections:
  - id: evo_cloud
    name: Servidor EVO
    enabled: true
    listen_host: 0.0.0.0
    listen_port: 7788
    host: 64.23.152.47
    port: 7788
    scheme: ws
    path: /
    devices:
      - serial_number: AYTI25116940
        name: Lab - Entrada
        customer_id: lab
        site_id: lab
        direction: in
      - serial_number: AYTJ15126851
        name: Cliente - Facial
        customer_id: cliente_x
        site_id: matriz
        direction: none
```

O `sn` recebido em `reg`/`sendlog` identifica o equipamento. Somente seriais cadastrados geram eventos Seiden; seriais desconhecidos continuam sendo encaminhados ao servidor sem publicação. Fotos de autenticações válidas são salvas por serial em `/config/www/seiden_bridge/evo_relay/` e publicadas como `photo_url`, sem transportar o Base64 bruto no barramento do Home Assistant.

## Environmental Source Registry

A versão 0.12.0 permite cadastrar múltiplas fontes ambientais MQTT com identidade independente do nome técnico do Zigbee2MQTT. Cada fonte pode possuir nome amigável, descrição, local, ativo monitorado, perfil futuro e mapeamento dos campos do payload.

```yaml
mqtt_connections:
  - id: mqtt_casa
    name: MQTT Casa
    enabled: true
    host: core-mosquitto
    port: 1883
    username: usuario
    password: senha
    topics: []

environment_sources:
  - id: adega_vinhos
    name: Adega de Vinhos
    enabled: true
    connection_id: mqtt_casa
    topic: zigbee2mqtt/termometro_adega
    description: Monitora temperatura e umidade da adega climatizada.
    location_id: sala_jantar
    location_name: Sala de Jantar
    asset_id: adega_principal
    asset_name: Adega Principal
    profile_id: wine_cellar
    fields:
      temperature_c: temperature
      humidity_pct: humidity
      battery_pct: battery
```

Os tópicos das fontes são adicionados automaticamente às assinaturas da conexão MQTT. Caminhos pontuados são aceitos, por exemplo `sensor.temperature`.

O evento mantém o payload original em `data` e `raw` e acrescenta `source_id`, `source_name`, `location_*`, `asset_*`, `profile_id`, `environment` e as medições canônicas. Isso preserva a compatibilidade com o Vision atual e prepara os perfis ambientais futuros.

Fontes desabilitadas ou incompletas são ignoradas com log e não derrubam o Bridge. Uma conexão MQTT sem `topics` é válida quando possui fontes ambientais associadas.

## Eventos

EVO Direct, EVO Relay e MQTT publicam exclusivamente em `seiden_bridge_event`. A origem é identificada no envelope pelos campos `connector`, `connection` e `event_type`.

## Responsabilidades

O Bridge identifica, conecta e normaliza a fonte. A interpretação de faixas e perfis pertence ao Seiden Vision; histórico, filtros e inteligência operacional pertencem ao Seiden FLOW.
