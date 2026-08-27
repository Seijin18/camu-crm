# Delta: ingestao-a-prova-de-falha

## ADDED Requirements

### Requirement: Schema ausente falha no boot, não no primeiro evento

`webhook.py::servir()` DEVE chamar `ensure_schema()` no boot do processo.
Falha de conectar ao banco ou de aplicar o schema DEVE derrubar o processo
com um erro alto no boot, nunca ficar silenciosa até o primeiro evento
chegar.

#### Scenario: Boot contra banco sem schema falha alto

- **WHEN** o processo do webhook sobe contra um banco sem o schema aplicado
  (ou inacessível)
- **THEN** o processo falha no boot, antes de aceitar qualquer requisição

### Requirement: Payload bruto é preservado antes do processamento

Todo evento recebido pelo webhook DEVE ter seu payload cru gravado em
`eventos_recebidos_bruto` antes de qualquer parsing ou chamada a
`ingerir()`.

#### Scenario: Payload é gravado antes de processar

- **WHEN** um evento chega ao webhook
- **THEN** uma linha em `eventos_recebidos_bruto` com o payload cru existe
  antes do resultado do processamento ser conhecido

### Requirement: Falha de ingestão deixa rastro reprocessável

Uma exceção levantada dentro de `ingerir()` NÃO DEVE resultar em perda
silenciosa do evento — a linha correspondente em `eventos_recebidos_bruto`
DEVE permanecer com `processado=false` e o erro registrado, disponível para
reprocessamento manual.

#### Scenario: Exceção durante ingestão preserva o payload para reprocessar

- **WHEN** `ingerir()` levanta uma exceção ao processar um evento
- **THEN** a linha correspondente em `eventos_recebidos_bruto` permanece com
  `processado=false` e `erro` preenchido
- **AND** o payload original continua disponível para reprocessamento

### Requirement: Reprocessamento manual de falhas

`camucrm reprocessar-falhas` DEVE ler as linhas de `eventos_recebidos_bruto`
com `processado=false`, tentar reingerir cada uma, e marcar como
`processado=true` em caso de sucesso.

#### Scenario: Comando reprocessa uma falha registrada com sucesso

- **WHEN** `camucrm reprocessar-falhas` é executado com uma linha
  `processado=false` cujo payload agora processa sem erro (ex. schema já
  corrigido)
- **THEN** a linha passa a `processado=true` com `processado_em` preenchido

### Requirement: Retenção da caixa de reprocessamento não apaga falha pendente

A purga de `eventos_recebidos_bruto` DEVE remover apenas linhas com
`processado=true` mais antigas que a janela de retenção configurada.
Linhas com `processado=false` NÃO DEVEM ser removidas automaticamente sob
nenhuma circunstância.

#### Scenario: Purga não remove falha ainda não resolvida

- **WHEN** a purga de `eventos_recebidos_bruto` roda e existe uma linha
  antiga com `processado=false`
- **THEN** essa linha não é removida, independentemente de sua idade

### Requirement: Cadeia de ingestão é transacional

`upsert_contato`, `get_or_create_conversa` e `registrar_mensagem`, quando
executados para um único evento, DEVEM rodar dentro de uma única transação
— uma falha em qualquer ponto do meio NÃO DEVE deixar contato ou conversa
gravados sem a mensagem correspondente.

#### Scenario: Falha no meio da cadeia não deixa órfão

- **WHEN** `registrar_mensagem` falha depois que `upsert_contato` e
  `get_or_create_conversa` já rodaram, dentro do mesmo evento
- **THEN** nenhuma das três operações persiste — a transação inteira é
  desfeita

### Requirement: Evento sem externa_id ainda é protegido contra duplicação

Um evento cujo payload não contém `key.id` (ou equivalente) DEVE ainda assim
ser protegido contra duplicação em reentrega, via um identificador sintético
derivado do payload cru.

#### Scenario: Reentrega de evento sem key.id não duplica mensagem

- **WHEN** o mesmo payload, sem `key.id`, é entregue duas vezes
- **THEN** apenas uma mensagem correspondente é gravada

### Requirement: cmd_ingerir não finge sucesso silencioso

`camucrm cli ingerir` executado sem `--transporte evolution` sobre um
payload real da Evolution API NÃO DEVE produzir a mesma saída de um evento
benignamente ignorado — a saída DEVE diferenciar "ignorado por configuração
divergente do webhook" de "ignorado por ser evento benigno".

#### Scenario: Payload real sem flag de transporte não é confundido com evento benigno

- **WHEN** `camucrm cli ingerir` é executado sem `--transporte evolution`
  sobre um payload real de mensagem da Evolution API
- **THEN** a saída indica que o evento foi ignorado por configuração, não
  por ser benigno
