# Delta: ingestao-restrita-por-instancia

## ADDED Requirements

### Requirement: Restrição é por instância, nunca global

A regra "só contato conhecido ou de prospecção" DEVE se aplicar exclusivamente
às instâncias listadas em `CAMU_INSTANCIAS_RESTRITAS`. Uma instância não
listada (inclusive quando a variável está ausente ou vazia) DEVE continuar
criando `contato`/`conversa` para qualquer telefone novo, exatamente como
antes deste change.

#### Scenario: Instância não listada aceita telefone novo normalmente

- **WHEN** chega uma mensagem inbound de um telefone nunca visto, numa
  instância que não está em `CAMU_INSTANCIAS_RESTRITAS` (ou a variável está
  ausente)
- **THEN** um `contato` novo é criado e a mensagem é gravada, do jeito que
  já acontecia antes deste change

#### Scenario: Variável ausente não muda nenhum comportamento existente

- **WHEN** `CAMU_INSTANCIAS_RESTRITAS` não está configurada
- **THEN** `ingest.ingerir` se comporta de forma idêntica à versão anterior
  a este change, para qualquer instância

### Requirement: Instância restrita só acompanha contato já conhecido

Numa instância listada em `CAMU_INSTANCIAS_RESTRITAS`, uma mensagem de
telefone que NÃO é `contato` existente e NÃO está em `prospeccoes` NÃO DEVE
gerar `contato`, `conversa` ou `mensagem` nenhuma. `ingerir` DEVE devolver
`ResultadoIngestao(ignorada=True)` para esse caso.

#### Scenario: Telefone desconhecido numa instância restrita é ignorado

- **WHEN** chega uma mensagem (inbound ou o eco `fromMe` de uma enviada)
  numa instância restrita, de um telefone que não é contato conhecido nem
  está em `prospeccoes`
- **THEN** nenhum `contato`, `conversa` ou `mensagem` é criado, e o
  resultado da ingestão é `ignorada=True`

#### Scenario: Telefone já contato numa instância restrita segue normalmente

- **WHEN** chega uma mensagem numa instância restrita, de um telefone que
  já é `contato` existente
- **THEN** a mensagem é gravada na conversa desse contato, exatamente como
  numa instância não restrita

#### Scenario: Telefone de prospecção B2B numa instância restrita segue normalmente

- **WHEN** chega uma mensagem numa instância restrita, de um telefone
  presente em `prospeccoes` (mas ainda sem `contato` criado)
- **THEN** um `contato` novo é criado (com `tipo=b2b`, mesma regra de
  `prospeccao-b2b-shortlist`) e a mensagem é gravada

### Requirement: Payload cru continua sendo preservado incondicionalmente

O registro em `eventos_recebidos_bruto` (change `ingestao-a-prova-de-falha`)
NÃO DEVE ser afetado pela restrição de instância — todo payload recebido pelo
webhook é gravado ali antes de qualquer decisão de ingestão, restrita ou não.

#### Scenario: Evento ignorado por restrição de instância ainda foi staged

- **WHEN** um evento chega numa instância restrita e é ignorado por
  telefone desconhecido
- **THEN** existe uma linha correspondente em `eventos_recebidos_bruto`
  para esse payload, como para qualquer outro evento recebido

### Requirement: `cmd_ingerir` e o webhook nunca divergem na restrição

A CLI (`camucrm ingerir --instancia`) DEVE aplicar exatamente a mesma regra
de restrição por instância que o webhook aplica — os dois caminhos chamam
`ingest.ingerir` com o mesmo parâmetro `instancia`.

#### Scenario: Mesmo payload, mesmo resultado pelos dois caminhos

- **WHEN** o mesmo payload de uma instância restrita, com telefone
  desconhecido, é processado uma vez via `POST /webhook` e uma vez via
  `camucrm ingerir --instancia <nome>`
- **THEN** os dois caminhos ignoram a mensagem da mesma forma, sem criar
  `contato`/`conversa`/`mensagem` em nenhum dos dois
