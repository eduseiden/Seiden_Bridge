## 0.12.0 — Environmental Source Registry

- Adicionado cadastro de múltiplas fontes ambientais MQTT.
- Adicionados nomes amigáveis independentes do Zigbee2MQTT.
- Adicionados descrição, local, ativo e `profile_id` por fonte.
- Adicionado mapeamento configurável de temperatura, umidade e bateria, inclusive por caminhos pontuados.
- Assinaturas MQTT das fontes são incorporadas automaticamente à conexão.
- Mantido o payload MQTT original para compatibilidade.
- Fontes inválidas ou desabilitadas não interrompem a inicialização do Bridge.
- Mantido o schema canônico de eventos em 2.0.

## 0.11.0 — Consolidação de versão e metadados

- Alinha a versão exibida no add-on, no runtime, nos eventos e na documentação.
- Consolida o repositório `Seiden_Bridge` como baseline oficial para as próximas evoluções.
- Remove artefatos locais de cache Python do pacote de distribuição.
- Nenhuma alteração funcional nos conectores EVO ou MQTT e nenhum impacto no schema canônico 2.0.

## 0.10.0.1

- Corrige `NameError: config is not defined` ao processar um novo evento EVO.
- O fuso `operation_timezone` passa explicitamente do processo principal ao loop e ao normalizador do evento.
- Corrige a identificação da versão no log de inicialização.

# Changelog

## 0.10.0
- Adoção do Seiden One Platform Standard v1.0.
- Timestamps canônicos publicados em UTC com sufixo Z.
- Eventos EVO sem offset são interpretados no fuso `operation_timezone`.
- Eventos MQTT e EVO permanecem no schema canônico 2.0.

## 0.10.0 — Arquitetura unificada sem legado

- Remove `seiden_presence`, `seiden_reader_online` e `seiden_reader_offline`.
- Remove `legacy_events_enabled`, `ha_event`, `mqtt_event`, `reader_online_event` e `reader_offline_event`.
- EVO e MQTT publicam exclusivamente em `seiden_bridge_event`.
- Conectividade usa exclusivamente `seiden_connection_online` e `seiden_connection_offline`.
- Elimina duplicidade de eventos durante passagens EVO.

# Changelog

## 0.8.3 — Eventos unificados e configuração MQTT

- Adicionado `bridge_event`, usado como evento principal por EVO e MQTT.
- Adicionados `connection_online_event` e `connection_offline_event`.
- Adicionado `legacy_events_enabled` para manter temporariamente os eventos antigos.
- EVO publica simultaneamente em `seiden_bridge_event` e `seiden_presence` durante a transição.
- MQTT continua em `seiden_bridge_event`, evitando publicação duplicada quando o alias legado possui o mesmo nome.
- Saúde do EVO publica eventos genéricos e, durante a transição, os aliases de leitor.
- Schema MQTT mantido editável pela interface do Supervisor, inclusive para conexões já existentes.
- Documentada a limitação do frontend do Home Assistant: o add-on não possui API para forçar o recarregamento visual da tela Configuration após salvar.

## 0.8.2.2 — Compatibilidade com Paho MQTT 2.x

- Corrige a exceção `TypeError` nos callbacks `on_connect` e `on_disconnect`.
- Trata corretamente objetos `ReasonCode` da Callback API v2 do Paho MQTT.
- Preserva integralmente o funcionamento do conector EVO e a configuração MQTT da versão anterior.

## 0.8.2.2 — Correção de schema MQTT

- Corrigida a validação de `mqtt_connections` no schema do add-on.
- Campos com valores padrão deixaram de ser exigidos pelo Supervisor ao salvar a configuração.
- Mantido integralmente o funcionamento do conector EVO.
- Mantidas as correções de proteção das credenciais MQTT nos logs.

## 0.8.2

- Corrige o schema das conexões MQTT: campos com valores padrão passam a ser opcionais na validação do Supervisor.
- Evita falhas de salvamento quando a interface omite booleanos ou valores padrão, como `enabled`, `clean_session`, `qos` e opções TLS.
- Mantém `id`, `name`, `host` e `topics` como campos obrigatórios.
- Protege senha MQTT e chave privada TLS nos logs.
- Alinha as referências internas de versão.

## 0.8.1 — Correção de instalação

- Corrige a sintaxe de campos opcionais no schema do Home Assistant Supervisor.
- Evita o erro `Missing option 'scheme?' in endpoint` durante atualização/instalação.
- Mantém integralmente os conectores EVO e MQTT introduzidos na 0.8.0.

## 0.8.0 — MQTT Input Connector

- preserva o funcionamento EVO e a compatibilidade da 0.7.0;
- adiciona conector MQTT assíncrono com `paho-mqtt` 2.1.0;
- permite múltiplas assinaturas por conexão, QoS 0/1/2 e payload JSON ou texto;
- adiciona reconexão automática e TLS opcional;
- publica mensagens normalizadas no evento configurável `seiden_bridge_event`;
- adiciona `create_mqtt_event()` ao esquema canônico 2.0;
- separa conexões EVO de polling e conexões MQTT de streaming;
- mantém correlação e inferência fora do Bridge.

## 0.7.0 — Connector Foundation

### Arquitetura
- Introduz `connections` como modelo principal de configuração.
- Separa conexão técnica, endpoint e contexto operacional.
- Cria a camada `connectors` com contrato comum `BaseConnector`.
- EVO passa a ser o primeiro conector, deixando de definir o escopo do Bridge.
- Novo envelope canônico de eventos `schema_version: 2.0`, com `connection`, `context`, `subject` e `result`.

### Casos de uso
- `passage` com direção `in` ou `out` mantém o Occupancy Engine.
- `authorization` e demais interações sem direção registram autenticações sem alterar ocupação.
- A direção passa a ser opcional fora do caso de passagem.

### Compatibilidade
- Configurações `entry_readers`, `exit_readers` e `readers` continuam aceitas com aviso de migração.
- Campos e objetos legados de eventos são preservados durante a transição.
- Eventos e entidades existentes do Home Assistant permanecem compatíveis.


## 0.6.3

### Corrigido

- Incluído `reader_id` estável nos eventos `seiden_reader_offline` e `seiden_reader_online`.
- Incluído `reader_id` também nos campos planos do evento de presença para compatibilidade.
- Padronizada a correlação de fontes entre Seiden Bridge e Seiden FLOW.


## 0.6.2

- Corrige falha crítica ao marcar um leitor como offline.
- Remove parâmetros indevidos enviados a `calculate_backoff()`, que causavam `NameError` quando o equipamento ficava inacessível.
- O Bridge agora mantém o processo ativo, publica o estado offline e continua as tentativas com backoff exponencial.

## 0.6.1

- Modularizado o núcleo em `seiden_bridge_app`.
- Criada interface abstrata `ReaderDriver`.
- Movido o protocolo EVO para um driver isolado.
- Criada fábrica central de drivers.
- Padronizado o envelope de eventos (`schema_version: 1.0`).
- Adicionados objetos estruturados `reader`, `person` e `operation`.
- Preservados todos os campos planos e entidades da 0.6.0.
- Mantido o comportamento operacional e o dashboard compatíveis.

## 0.6.0

### Arquitetura

- Renomeia o produto para Seiden Bridge.
- Adota o novo slug `seiden_bridge`.
- Renomeia o executável para `seiden_bridge.py`.
- Remove `evo` dos identificadores centrais e das entidades globais.
- Mantém o prefixo `seiden_` em todas as entidades publicadas.
- Introduz o conceito de driver por leitor.
- Disponibiliza as opções EVO, Control iD, Hikvision e Intelbras na configuração.
- Implementa o driver EVO; os demais permanecem planejados e só podem ficar desativados.
- Inclui `driver` e `source` nos eventos normalizados.
- Inclui `event_id` único nos eventos de presença.
- Move as fotografias para `/config/www/seiden_bridge`.
- Atualiza dashboard, documentação, logs e nomes amigáveis.

### Compatibilidade

- Esta é uma versão de ruptura para instalação limpa.
- Não há migração automática das entidades ou dados da versão 0.5.1.

## 0.5.1

- Corrige a atualização visual da última fotografia no dashboard.
- Substitui a entidade artificial `camera.seiden_evo_last_photo` por `sensor.seiden_evo_last_photo`, usando o atributo nativo `entity_picture`.
- Gera uma URL de imagem exclusiva a cada passagem para eliminar cache do navegador e do frontend do Home Assistant.
- Mantém `/local/seiden_evo/latest.jpg` para acesso manual e compatibilidade.
- Preserva somente as cinco capturas mais recentes no diretório local.

## 0.5.0

- Publicação automática da última fotografia em `camera.seiden_evo_last_photo`.
- Elimina a necessidade de configurar manualmente uma câmera genérica para o dashboard.
- Download da fotografia diretamente do `photo_url` informado pelo leitor EVO.
- Armazenamento local da imagem em `/config/www/seiden_evo/latest.jpg`.
- Atualização atômica do arquivo para evitar imagem parcial durante o download.
- Controle de cache por parâmetro de versão na URL da imagem.
- Validação de tipo JPEG, tamanho máximo e imagem vazia.
- Novas opções `publish_last_photo` e `photo_max_size_mb`.
- Mantido suporte multi-arquitetura para AMD64 e AArch64.
- Mantida compatibilidade dos eventos e sensores existentes da versão 0.4.5.

## 0.4.5

### Adicionado

- Suporte à arquitetura `aarch64`, utilizada pelo Raspberry Pi 5.
- Arquivo `build.yaml` para selecionar a imagem-base correta em `amd64` e `aarch64`.
- Campo `photo_filename` no evento de presença, no último evento persistido e nos atributos do sensor da última pessoa.

### Alterado

- Dockerfile passa a utilizar `ARG BUILD_FROM`, sem fixar a imagem `amd64`.
- Estado de `sensor.seiden_evo_last_action` passa a ser exibido como `Entrada` ou `Saída`.
- Valor técnico original do movimento permanece disponível no atributo `action`.
- Versão central do Bridge atualizada para `0.4.5`.

## 0.4.4

### Adicionado

- Entidades operacionais criadas diretamente no Home Assistant para uso em dashboards.
- Estado geral do Bridge, versão e uptime.
- Contadores de leitores online, offline e em verificação.
- Estado individual de conectividade para cada leitor ativo.
- Quantidade e lista de pessoas presentes.
- Contadores diários de movimentos, entradas e saídas.
- Informações da última pessoa, último movimento, último leitor e horário.
- Sensor consolidado com o estado de todos os leitores.
- Exemplo de dashboard operacional em `dashboard_evo.yaml`.

### Alterado

- O estado persistente passa a armazenar contadores diários e o último evento.
- As entidades operacionais são atualizadas após eventos e periodicamente a cada 60 segundos.
- O modo de espera sem leitores ativos também mantém as entidades do Bridge atualizadas.

## 0.4.3

### Corrigido

- IPs e nomes duplicados entre leitores desativados deixam de impedir
  a inicialização do Bridge.
- Leitor ativo e leitor desativado podem compartilhar temporariamente
  o mesmo IP ou nome.
- Apenas duplicidades entre leitores ativos são tratadas como erro
  operacional crítico.

### Alterado

- Duplicidade entre leitor ativo e desativado gera WARNING.
- Duplicidade apenas entre leitores desativados gera INFO.
- Leitores desativados continuam fora do polling, backoff e eventos.
- Validação estrutural foi separada da validação operacional.


## 0.4.2

### Corrigido

- Todos os leitores desativados deixam de causar encerramento crítico.
- O Bridge permanece ativo em modo de espera quando não há leitores ativos.
- Removida a duplicidade de traceback em falhas críticas.
- Melhorada a apresentação dos logs durante manutenção planejada.

### Alterado

- Erros de comunicação são resumidos no nível WARNING.
- A exceção completa permanece disponível no nível DEBUG.
- Leitores desativados também são validados na inicialização.
- Nenhum polling ou evento de disponibilidade é gerado quando todos
  os leitores estão desativados.


## 0.4.1

### Adicionado

- Opção `enabled` para cada leitor de entrada e saída.
- Possibilidade de desativar temporariamente um leitor sem removê-lo.
- Contagem de leitores ativos e desativados na inicialização.
- Identificação dos leitores desativados no log.

### Alterado

- Leitores desativados não realizam polling.
- Leitores desativados não geram backoff.
- Leitores desativados não geram eventos de disponibilidade.
- Configurações antigas sem `enabled` continuam sendo consideradas ativas.

## 0.4.0

### Adicionado

- Listas independentes para leitores de entrada e de saída.
- Configuração `entry_readers`.
- Configuração `exit_readers`.
- Direção determinada automaticamente pelo grupo do leitor.
- Contadores de leitores de entrada e saída na inicialização.
- Validação de nomes duplicados.
- Compatibilidade temporária com a configuração antiga `readers`.
- Configuração efetivamente carregada disponível no nível DEBUG,
  com senhas ocultadas.

### Alterado

- Removido o campo editável `direction` de cada leitor.
- A direção não depende mais do seletor gráfico do Home Assistant.
- Leitores em `entry_readers` são tratados internamente como `in`.
- Leitores em `exit_readers` são tratados internamente como `out`.

### Correção

- Corrigida a inconsistência em que o formulário mostrava `in`,
  mas o App continuava utilizando `out` internamente.


## 0.3.1

### Adicionado

- Data e hora em todas as mensagens do Seiden EVO Bridge.
- Níveis configuráveis de logging:
  - DEBUG
  - INFO
  - WARNING
  - ERROR
- Configuração `log_level` na interface do App.
- Logs detalhados de registros EVO no nível DEBUG.
- Padronização das mensagens com identificação do componente e leitor.

### Alterado

- Mensagens de indisponibilidade passam a usar o nível WARNING.
- Falhas de integração com o Home Assistant passam a usar o nível ERROR.
- Eventos operacionais normais passam a usar o nível INFO.


## 0.3.0

### Adicionado

- Backoff exponencial independente por leitor.
- Intervalo máximo de nova tentativa configurável.
- Timeout HTTP configurável.
- Evento `seiden_reader_offline`.
- Evento `seiden_reader_online`.
- Informação da duração da indisponibilidade.
- Validação da configuração na inicialização.
- Logs padronizados por leitor.
- Escrita atômica do estado persistente.
- Campo `building_occupied` no evento de presença.
- Campo `was_already_inside`.
- Campo `exit_without_entry`.

### Corrigido

- Duplicidade causada pela criação do registro antes da associação da foto.
- Alteração indevida do horário de entrada em autenticações repetidas.
- Indicação incorreta de última saída quando o usuário não constava como presente.
- Reinicialização diária dos indicadores de primeira entrada e última saída.

## 0.2.2

- Correções de indentação.
- Deduplicação dos eventos com e sem `photourl`.

## 0.2.0

- Occupancy Engine.
- Entrada e saída.
- Pessoas presentes.
- Primeira entrada.
- Última saída.
- Persistência de estado.

## 0.1.0

- MVP de comunicação com o EVO Facial.
- Leitura de logs.
- Publicação de eventos no Home Assistant.
