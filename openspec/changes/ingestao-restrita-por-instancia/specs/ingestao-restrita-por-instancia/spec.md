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
`ResultadoIngestao(ignorada=True, ignorada_por_restricao_instancia=True)`
para esse caso — o segundo campo distingue este motivo de qualquer outro
"ignorado" (ex.: evento que não é mensagem de conversa), porque só este
motivo dispara a exclusão do payload cru (ver requirement abaixo).

#### Scenario: Telefone desconhecido numa instância restrita é ignorado

- **WHEN** chega uma mensagem (inbound ou o eco `fromMe` de uma enviada)
  numa instância restrita, de um telefone que não é contato conhecido nem
  está em `prospeccoes`
- **THEN** nenhum `contato`, `conversa` ou `mensagem` é criado, e o
  resultado da ingestão é `ignorada=True` com
  `ignorada_por_restricao_instancia=True`

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

### Requirement: Payload cru é gravado antes da decisão, e excluído se o motivo for restrição de instância

O registro em `eventos_recebidos_bruto` (change `ingestao-a-prova-de-falha`)
NÃO DEVE ser afetado pela restrição de instância ANTES da decisão — todo
payload recebido pelo webhook é gravado ali antes de qualquer chamada a
`ingest.ingerir`, restrita ou não (a garantia de durabilidade contra falha
no meio do processamento continua intacta). DEPOIS da decisão, se o motivo
foi especificamente "instância restrita, telefone desconhecido"
(`ResultadoIngestao.ignorada_por_restricao_instancia=True`), a linha
correspondente DEVE ser excluída imediatamente — revisão de 2026-08-27,
pedido explícito do usuário: mensagem de quem nunca teve relação nenhuma
com a Camu não deve deixar rastro nem no staging técnico.

#### Scenario: Payload existe até a decisão terminar com sucesso

- **WHEN** um evento chega numa instância restrita
- **THEN** existe uma linha em `eventos_recebidos_bruto` para esse payload
  enquanto `ingest.ingerir` ainda está decidindo

#### Scenario: Evento ignorado por restrição de instância é excluído do staging

- **WHEN** um evento chega numa instância restrita, é ignorado por telefone
  desconhecido, e `ingerir` termina sem exceção
- **THEN** a linha correspondente em `eventos_recebidos_bruto` é excluída
  (não apenas marcada como processada)

#### Scenario: Evento ignorado por outro motivo continua preservado normalmente

- **WHEN** um evento é ignorado por um motivo que NÃO é restrição de
  instância (ex.: não é mensagem de conversa)
- **THEN** a linha em `eventos_recebidos_bruto` continua existindo, marcada
  como processada, sujeita só à retenção padrão
  (`purgar_eventos_brutos_antigos`)

#### Scenario: Falha no meio do processamento preserva o payload normalmente

- **WHEN** `ingest.ingerir` levanta uma exceção durante o processamento de
  um evento de instância restrita
- **THEN** a linha em `eventos_recebidos_bruto` NÃO é excluída — permanece
  `processado=False` com o erro registrado, disponível para
  `camucrm reprocessar-falhas`, exatamente como qualquer outra falha

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
