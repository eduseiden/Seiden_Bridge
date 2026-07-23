# Changelog

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
