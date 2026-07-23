# Seiden Bridge 0.6.0

O Seiden Bridge é a camada de integração da Seiden Tech para leitores de acesso e reconhecimento. Ele normaliza eventos dos equipamentos, monitora disponibilidade, mantém o estado operacional local e publica entidades e eventos no Home Assistant.

## Ruptura arquitetural da versão 0.6.0

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
