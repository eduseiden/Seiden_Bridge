# Seiden Bridge 0.10.0.1

Camada de integração do Seiden One. Captura dados de múltiplas origens, normaliza-os no schema canônico 2.0 e publica eventos no Home Assistant.

## Arquitetura unificada

A versão 0.10.0 remove definitivamente os eventos legados. EVO e MQTT publicam exclusivamente em:

- `seiden_bridge_event`
- `seiden_connection_online`
- `seiden_connection_offline`

A origem é identificada no próprio envelope pelos campos `connector`, `connection` e `event_type`. Uma passagem EVO gera somente um evento.

## Conectores

- EVO: autenticações e passagens por polling.
- MQTT: mensagens de tópicos configurados, com JSON preservado em `raw`.

O Bridge não correlaciona evidências nem conclui contexto operacional. Essa responsabilidade pertence ao Seiden FLOW; o enriquecimento pertence ao Seiden Vision.

## Migração

Atualize primeiro FLOW 0.6.0 e Vision 0.5.0. Depois atualize o Bridge para 0.10.0. Os campos legados são removidos da tela de configuração.
