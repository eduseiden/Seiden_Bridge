# Seiden Bridge 0.12.0

Camada de integração do Seiden One. Captura dados de múltiplas origens, normaliza-os no schema canônico 2.0 e publica eventos no Home Assistant.

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

EVO e MQTT publicam exclusivamente em `seiden_bridge_event`. A origem é identificada no envelope pelos campos `connector`, `connection` e `event_type`.

## Responsabilidades

O Bridge identifica, conecta e normaliza a fonte. A interpretação de faixas e perfis pertence ao Seiden Vision; histórico, filtros e inteligência operacional pertencem ao Seiden FLOW.
