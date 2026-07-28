# Seiden Bridge 0.8.2.2

## EVO + MQTT Input Connector

O **Seiden Bridge** é a camada de integração do **Seiden One**. Ele captura eventos de diferentes origens, normaliza suas estruturas e os publica para consumo por outros componentes, sem realizar correlação ou interpretação operacional.

A versão **0.8.2.2** preserva o funcionamento construído para o **EVO** na versão 0.7.0 e adiciona o **MQTT** como primeira origem assíncrona de eventos.

> **Seiden Bridge transforma eventos de múltiplas origens em eventos padronizados.**


## Configuração MQTT na 0.8.2.2

Para manter compatibilidade integral com as conexões EVO da 0.7.0 e respeitar o limite de profundidade do schema do Home Assistant, as origens MQTT são configuradas em `mqtt_connections`:

```yaml
mqtt_connections:
  - id: mqtt_casa
    name: MQTT Casa
    enabled: true
    host: 192.168.1.10
    port: 1883
    username: seiden_bridge
    password: "senha"
    client_id: seiden_bridge_casa
    clean_session: true
    keepalive: 60
    qos: 0
    event_type: mqtt.message_received
    topics:
      - zigbee2mqtt/FechaduraSala
      - zigbee2mqtt/SensorPortaSala
    tls_enabled: false
    tls_verify: true
```

As conexões EVO continuam no bloco `connections`, sem qualquer mudança de formato.

## O que existe na versão 0.8.2.2

- conector EVO por polling;
- conector MQTT por assinatura de tópicos;
- múltiplas conexões configuráveis;
- payload MQTT em JSON ou texto;
- múltiplas assinaturas por conexão;
- QoS 0, 1 ou 2;
- TLS opcional;
- reconexão automática;
- evento canônico MQTT publicado no Home Assistant;
- preservação do payload original em `raw`;
- compatibilidade com as configurações legadas de leitores;
- arquitetura preparada para novos conectores.

## Limite de responsabilidade

O Bridge:

- captura;
- traduz;
- normaliza;
- publica.

O Bridge **não correlaciona eventos** e **não conclui o significado operacional**. Por exemplo, ele pode publicar `lock.authentication_succeeded` e `door.opened`, mas a conclusão de que uma pessoa entrou ou saiu pertence ao **Seiden FLOW**, eventualmente enriquecida pelo **Seiden Vision**.

## Modelo de configuração

Cada integração configurada é uma **connection**. O campo `connector` define a tecnologia utilizada por essa conexão.

### EVO

```yaml
connections:
  - id: evo_entrada
    name: Entrada Principal
    connector: evo
    enabled: true
    password: "1234"
    endpoint:
      host: 192.168.4.157
      scheme: http
      path: /api
    context:
      interaction_type: passage
      direction: in
```

Um EVO utilizado apenas para autorização pode operar sem direção:

```yaml
connections:
  - id: evo_maquina
    name: Liberação da Máquina
    connector: evo
    enabled: true
    password: "1234"
    endpoint:
      host: 192.168.4.158
      scheme: http
      path: /api
    context:
      interaction_type: authorization
      direction: none
```

`passage` com direção `in` ou `out` alimenta o mecanismo de ocupação existente. Interações como `authorization` publicam o evento sem alterar a quantidade de pessoas presentes.

### MQTT

```yaml
mqtt_event: seiden_bridge_event

connections:
  - id: mqtt_casa
    name: MQTT Casa
    connector: mqtt
    enabled: true
    username: seiden_bridge
    password: "senha"
    client_id: seiden_bridge_casa
    clean_session: true

    endpoint:
      host: 192.168.1.10
      port: 1883
      keepalive: 60

    context:
      interaction_type: message
      direction: none

    subscriptions:
      - topic: zigbee2mqtt/FechaduraSala
        qos: 0
        event_type: lock.telemetry_received

      - topic: zigbee2mqtt/SensorPortaSala
        qos: 0
        event_type: door.telemetry_received
```

## Evento MQTT publicado no Home Assistant

Por padrão, mensagens MQTT normalizadas são publicadas no evento:

```text
seiden_bridge_event
```

O nome pode ser alterado pela opção `mqtt_event`.

Estrutura lógica do evento:

```json
{
  "schema_version": "2.0",
  "event_id": "uuid",
  "event_type": "door.telemetry_received",
  "timestamp": "2026-07-28T14:30:00-03:00",
  "connection": {
    "id": "mqtt_casa",
    "name": "MQTT Casa",
    "connector": "mqtt"
  },
  "context": {
    "interaction_type": "message",
    "direction": "none"
  },
  "data": {
    "topic": "zigbee2mqtt/SensorPortaSala",
    "payload": {
      "contact": false,
      "battery": 82
    }
  },
  "raw": {
    "topic": "zigbee2mqtt/SensorPortaSala",
    "payload": {
      "contact": false,
      "battery": 82
    }
  }
}
```

## TLS no MQTT

```yaml
tls:
  enabled: true
  verify: true
  ca_cert: /ssl/ca.crt
  client_cert: /ssl/client.crt
  client_key: /ssl/client.key
```

## Configurações gerais

```yaml
poll_interval: 2
request_timeout: 5
max_retry_interval: 300
log_level: INFO
publish_last_photo: true
photo_max_size_mb: 5

ha_event: seiden_presence
mqtt_event: seiden_bridge_event
reader_offline_event: seiden_reader_offline
reader_online_event: seiden_reader_online
```

## Conectores

| Conector | Valor | Situação |
|---|---|---|
| EVO | `evo` | Implementado |
| MQTT | `mqtt` | Implementado |
| Control iD | `control_id` | Planejado |
| Hikvision | `hikvision` | Planejado |
| Intelbras | `intelbras` | Planejado |

Conectores ainda não implementados podem permanecer cadastrados somente com `enabled: false`. Uma conexão ativa com conector não implementado impede a inicialização e gera uma mensagem objetiva no log.

## Entidades EVO mantidas

A versão 0.8.2.2 mantém as entidades operacionais já existentes para o EVO:

```text
binary_sensor.seiden_bridge_running
sensor.seiden_bridge_version
sensor.seiden_bridge_uptime
sensor.seiden_readers_online
sensor.seiden_readers_offline
sensor.seiden_readers_unknown
sensor.seiden_readers_status
sensor.seiden_people_inside
binary_sensor.seiden_building_occupied
sensor.seiden_events_today
sensor.seiden_entries_today
sensor.seiden_exits_today
sensor.seiden_last_person
sensor.seiden_last_action
sensor.seiden_last_reader
sensor.seiden_last_event_time
sensor.seiden_last_photo
```

Para cada conexão EVO ativa é criada uma entidade no padrão:

```text
binary_sensor.seiden_reader_<nome_normalizado>
```

## Eventos EVO mantidos

```text
seiden_presence
seiden_reader_online
seiden_reader_offline
```

Os payloads preservam os campos necessários para compatibilidade com dashboards, automações e o Seiden FLOW.

## Última fotografia EVO

Quando habilitada, a última fotografia é publicada em:

```text
/config/www/seiden_bridge/latest.jpg
/local/seiden_bridge/latest.jpg
```

Card sugerido:

```yaml
type: picture-entity
entity: sensor.seiden_last_photo
name: Última passagem
show_name: true
show_state: false
show_entity_picture: true
fit_mode: contain
```

## Compatibilidade

As configurações legadas `entry_readers`, `exit_readers` e `readers` continuam sendo carregadas automaticamente, com aviso de migração para o modelo `connections`.

O esquema canônico permanece em `2.0`, mantendo os campos legados necessários durante a transição.

## Arquitetura interna

```text
seiden_bridge.py
        ↓
seiden_bridge_app/app.py
        ↓
connectors/
  ├── base.py
  ├── factory.py
  ├── evo.py
  └── mqtt.py
        ↓
events.py
        ↓
Home Assistant
```

- `evo.py` executa polling sobre o equipamento EVO;
- `mqtt.py` mantém assinaturas assíncronas no broker;
- `factory.py` resolve o conector de cada conexão;
- `events.py` gera os envelopes canônicos;
- `app.py` coordena o ciclo de vida, estado e publicação.

## Arquiteturas suportadas

- `amd64`: mini PCs Intel/AMD;
- `aarch64`: Raspberry Pi 5 e outros equipamentos ARM64.

## Atualização da 0.7.0 para a 0.8.2.2

1. Faça backup da configuração atual.
2. Atualize o repositório de add-ons.
3. Instale ou atualize o Seiden Bridge para a versão 0.8.2.2.
4. Mantenha as conexões EVO existentes.
5. Adicione uma conexão MQTT somente quando desejar utilizá-la.
6. Inicie o add-on e confira os logs.
7. Valide os eventos EVO já existentes.
8. Valide o evento `seiden_bridge_event` para as assinaturas MQTT.

## Histórico desta versão

A versão 0.7.0 criou a base de conectores e introduziu `connections` como modelo principal. A versão 0.8.2.2 utiliza essa base para adicionar o MQTT sem remover ou substituir o conector EVO.
