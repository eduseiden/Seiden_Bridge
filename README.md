# Seiden Bridge 0.15.2.1

## MQTT single-channel relay — 0.15.2.1

Relés Zigbee2MQTT de um único canal que publicam `{"state":"ON"}` são normalizados como
canal canônico `main`. O primeiro payload após reiniciar o Bridge continua sendo apenas
baseline; para o LCA descobrir o device é necessário ocorrer uma mudança real de estado
depois que o Bridge estiver em execução.


Camada de integração do Seiden One. Captura dados de múltiplas origens, normaliza-os no schema canônico 2.0 e publica eventos no Home Assistant.


## MQTT State Driver — 0.15.2

O MQTT State Driver transforma payloads MQTT repetitivos em eventos operacionais compactos. Ele mantém apenas o último valor dos campos `state_*` de cada tópico em memória e publica `state_transition` somente quando ocorre uma mudança real.

O primeiro payload recebido após a inicialização estabelece o baseline e **não gera evento**, evitando ações falsas em restart ou mensagens retained.

Exemplo recomendado — assine somente os dispositivos que realmente precisam gerar transições:

```yaml
mqtt_connections:
  - id: mqtt_casa
    name: MQTT Casa
    enabled: true
    host: core-mosquitto
    port: 1883
    username: mqtt_user
    password: senha

    topics:
      - zigbee2mqtt/Interruptor Suite Sacada

    state_driver_enabled: true
    state_driver_topics: |-
      zigbee2mqtt/Interruptor Sala
      zigbee2mqtt/Interruptor Suite Sacada
    state_driver_field_prefix: state_

    # false = para tópicos com state_*, publica somente transições reais.
    # Fontes ambientais continuam publicando seus eventos normalmente.
    state_driver_publish_raw: false
```

Com um payload como:

```json
{
  "state_l1": "ON",
  "state_l2": "OFF",
  "state_l3": "OFF",
  "state_l4": "OFF",
  "linkquality": 104,
  "last_seen": "2026-08-07T15:30:46.160Z"
}
```

uma mudança de `state_l1` produz um único evento canônico:

```json
{
  "event_type": "state_transition",
  "connector": "mqtt",
  "device_name": "Interruptor Suite Sacada",
  "channel": "l1",
  "previous_state": "OFF",
  "current_state": "ON"
}
```

`linkquality`, `last_seen`, backlight e demais campos não entram no cache de estados nem geram transições.

### Estratégia de volume

- **Assinatura seletiva:** em produção, prefira tópicos exatos; não assine `zigbee2mqtt/#` se o módulo precisa de poucos dispositivos.
- **Cache mínimo:** somente os valores `state_*` mais recentes ficam em RAM; nenhum histórico MQTT é mantido.
- **Baseline silencioso:** a primeira mensagem de cada tópico não gera evento.
- **Deduplicação por estado:** payload repetido gera zero eventos.
- **Evento por mudança:** somente canais cujo estado realmente mudou são publicados.
- **Payload bruto opcional:** `state_driver_publish_raw: false` evita duplicar o payload MQTT no barramento para tópicos tratados.
- **Sem polling adicional:** o driver trabalha no mesmo callback MQTT já existente; não cria threads, timers ou consultas extras.
- **Telemetria separada:** `last_seen`, `linkquality` e disponibilidade podem alimentar saúde de infraestrutura no futuro sem contaminar eventos operacionais.


> **Compatibilidade com Home Assistant:** `state_driver_topics` continua existindo para preservar upgrades e compatibilidade com o Supervisor. A interface web agora o preenche automaticamente em formato legível, com tópicos separados por vírgulas.

O State Driver é **opt-in**. Com `state_driver_enabled` ausente ou `false`, o comportamento MQTT da versão anterior permanece inalterado.

Além dos campos `state_l1`, `state_left`, `state_center` etc., a 0.15.2 também reconhece automaticamente o campo simples `state` quando o prefixo padrão `state_` está configurado. Isso cobre relés Zigbee2MQTT que publicam apenas `{"state": "ON"}`.


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


## Configuração visual do MQTT State Driver

A partir da 0.15.2, o Seiden Bridge oferece uma **Web UI via Ingress** para selecionar
os tópicos do MQTT State Driver sem redigitar a lista `topics`.

1. Cadastre normalmente os tópicos em `mqtt_connections[].topics`.
2. Abra **Seiden Bridge → Abrir interface web**.
3. Em cada conexão MQTT, marque o subconjunto de tópicos que deve gerar `state_transition`.
4. Clique em **Salvar e reiniciar Bridge**.

A interface só permite selecionar tópicos que já existem em `topics`, garantindo que
`state_driver_topics` seja sempre um subconjunto da assinatura MQTT.

O campo YAML `state_driver_topics` continua suportado por compatibilidade com a 0.14.2,
mas a Web UI passa a ser a forma recomendada de manutenção.


## Home Assistant State Driver — 0.15.2

O Bridge pode observar entidades do Home Assistant sem polling e sem criar MQTT artificial.
O driver usa WebSocket, registra triggers de estado somente para as entidades explicitamente configuradas,
fazendo o próprio Home Assistant filtrar eventos irrelevantes antes de chegarem ao Bridge.

Exemplo:

```yaml
ha_state_driver_enabled: true
ha_state_driver_entities: |-
  switch.suite_cama_real_switch
ha_state_driver_ignore_states: |-
  unknown
  unavailable
```

Uma mudança `off → on` ou `on → off` gera o mesmo `event_type: state_transition` usado
pelo MQTT State Driver, com `connector: home_assistant` e `entity_id` preservado como
identidade técnica. Mudanças apenas de atributos, criação/remoção da entidade e transições
para/de `unknown` ou `unavailable` são ignoradas para evitar ruído e falsas ações.

O driver é totalmente opt-in. Se não estiver configurado, a execução da 0.15.2 permanece
equivalente à 0.14.3 para MQTT, EVO Direct, EVO Relay e demais fontes.


## HA State Driver — configuração visual

Na 0.15.2, a Web UI do Seiden Bridge passa a listar as entidades diretamente do
Home Assistant. O operador pode habilitar o HA State Driver, filtrar por domínio
e selecionar as entidades por checkbox, sem digitar `entity_id` manualmente.

O filtro de domínio usa correspondência por prefixo. Exemplos:

- `light` mostra entidades `light.*`
- `switch` mostra entidades `switch.*`
- `binary_` mostra entidades `binary_sensor.*`

Há também busca por nome amigável ou `entity_id`.

A consulta ao Home Assistant ocorre somente ao abrir ou salvar a tela. O HA State
Driver em execução permanece totalmente orientado a eventos via WebSocket e não
usa polling.
