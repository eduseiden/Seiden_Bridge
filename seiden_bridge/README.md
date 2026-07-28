# Seiden Bridge 0.8.0

## MQTT Input Connector

A versão 0.8.0 preserva integralmente o conector EVO da 0.7.0 e adiciona MQTT como primeira origem assíncrona de eventos. O Bridge assina tópicos configuráveis, aceita payload JSON ou texto e publica no Home Assistant um evento canônico, sem correlacionar ou interpretar o significado operacional.

### Exemplo MQTT

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

O evento publicado é `seiden_bridge_event`. O payload preserva tópico e conteúdo original em `raw`, além dos campos canônicos `schema_version`, `event_id`, `event_type`, `timestamp`, `connection` e `data`.

### Limite de responsabilidade

O Bridge captura, normaliza e publica. Ele não conclui se alguém entrou ou saiu; essa interpretação pertence ao Seiden FLOW, eventualmente enriquecida pelo Seiden Vision.

---

## Connector Foundation

O Seiden Bridge é a camada de integração do Seiden One. A versão 0.7.0 substitui o modelo central de **leitores de entrada e saída** por **conexões**, preparando o produto para integrar equipamentos, APIs, sistemas, bancos de dados, mensageria e plataformas de automação. Nesta versão, o conector funcional continua sendo o EVO.

### Novo modelo de configuração

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

Um EVO utilizado apenas para liberar um recurso não precisa de direção:

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

`passage` atualiza o Occupancy Engine. `authorization` publica a autenticação, mas não altera a quantidade de pessoas presentes.

### Compatibilidade

As configurações `entry_readers`, `exit_readers` e `readers` continuam sendo carregadas automaticamente, com aviso de migração. O evento canônico passa ao esquema `2.0`, mas mantém os objetos e campos planos das versões 0.6.x durante a transição.

---

O Seiden Bridge é a camada de integração da Seiden Tech para leitores de acesso e reconhecimento. Ele normaliza eventos dos equipamentos, monitora disponibilidade, mantém o estado operacional local e publica entidades e eventos no Home Assistant.

## Ruptura arquitetural da versão 0.7.0

Esta versão substitui integralmente o antigo **Seiden EVO Bridge**. Como o ambiente atual é de testes, não há migração automática da versão 0.5.1.

Principais mudanças:

- novo nome: `Seiden Bridge`;
- novo slug: `seiden_bridge`;
- novo executável: `seiden_bridge.py`;
- entidades renomeadas para o prefixo `seiden_`;
- diretório de imagens alterado para `/config/www/seiden_bridge`;
- arquitetura preparada para múltiplos drivers;
- EVO disponível como primeiro driver;
- Control iD, Hikvision e Intelbras já aparecem como opções de configuração, mas ainda não estão implementados.

## Drivers

| Driver | Valor na configuração | Situação |
|---|---|---|
| EVO | `evo` | Implementado |
| Control iD | `control_id` | Planejado |
| Hikvision | `hikvision` | Planejado |
| Intelbras | `intelbras` | Planejado |

Drivers não implementados podem permanecer cadastrados somente com `enabled: false`. Um leitor ativo com driver não implementado impede a inicialização e gera uma mensagem objetiva no log.

## Exemplo de configuração

```yaml
poll_interval: 2
request_timeout: 5
max_retry_interval: 300
log_level: INFO
publish_last_photo: true
photo_max_size_mb: 5

ha_event: seiden_presence
reader_offline_event: seiden_reader_offline
reader_online_event: seiden_reader_online

entry_readers:
  - name: Entrada Principal
    driver: evo
    ip: 192.168.4.157
    password: "1234"
    enabled: true

exit_readers:
  - name: Saída Principal
    driver: hikvision
    ip: 192.168.4.158
    password: "1234"
    enabled: false
```

## Entidades principais

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

Para cada leitor ativo é criada uma entidade no padrão:

```text
binary_sensor.seiden_reader_<nome_normalizado>
```

Exemplo:

```text
binary_sensor.seiden_reader_entrada_principal
```

## Eventos do Home Assistant

```text
seiden_presence
seiden_reader_online
seiden_reader_offline
```

Os payloads incluem `source: seiden_bridge`, `driver`, identificação do leitor e, nos eventos de presença, um `event_id` único.

## Última fotografia

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

## Arquiteturas suportadas

- `amd64`: mini PCs Intel/AMD;
- `aarch64`: Raspberry Pi 5 e outros equipamentos ARM64.

## Instalação limpa

1. Desinstale o antigo Seiden EVO Bridge.
2. Atualize o repositório de add-ons.
3. Instale o Seiden Bridge.
4. Configure novamente os leitores.
5. Inicie o add-on e confira os logs.
6. Atualize dashboards e automações para as novas entidades.


## Arquitetura interna 0.7.0

A aplicação agora possui um núcleo modular. O arquivo `seiden_bridge.py` é somente o ponto de entrada. Drivers ficam em `seiden_bridge_app/drivers/` e implementam o contrato `ReaderDriver`.

Eventos de presença usam o esquema canônico `1.0`, incluindo `event_type`, `timestamp`, objetos `reader`, `person` e `operation`. Os campos planos da 0.6.0 foram preservados para compatibilidade com dashboards e automações existentes.