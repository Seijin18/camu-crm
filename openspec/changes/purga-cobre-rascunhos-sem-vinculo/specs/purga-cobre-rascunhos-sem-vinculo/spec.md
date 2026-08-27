# Delta: purga-cobre-rascunhos-sem-vinculo

## ADDED Requirements

### Requirement: Purga alcança rascunho sem vínculo

`purgar_mensagens_antigas` DEVE anonimizar `opcao_1`, `opcao_2` e
`texto_final` de todo rascunho de uma conversa encerrada há mais de
`meses`, via `conversa_id` direto — independentemente de `mensagem_id`
estar preenchido ou `NULL`. A linha do rascunho em si (contexto, escolha,
timestamps) NÃO DEVE ser removida, apenas o texto anonimizado.

#### Scenario: Rascunho nunca vinculado é anonimizado pela purga

- **WHEN** `purgar_mensagens_antigas` roda sobre uma conversa encerrada há
  mais de `meses` que tem um rascunho com `mensagem_id IS NULL`
- **THEN** `opcao_1`, `opcao_2` e `texto_final` desse rascunho são
  anonimizados
- **AND** a linha do rascunho (contexto, escolha, timestamps) continua
  existindo

### Requirement: Purga de resumo não depende de mensagem_id apontar para linha purgada

A purga de `resumos_conversa` DEVE alcançar todo resumo de uma conversa
encerrada há mais de `meses`, independentemente do valor de
`ultima_mensagem_id` apontar para uma mensagem já purgada, ainda existente,
ou `NULL`.

#### Scenario: Resumo de conversa encerrada é purgado independente de ultima_mensagem_id

- **WHEN** `purgar_mensagens_antigas` roda sobre uma conversa encerrada há
  mais de `meses` que tem um resumo registrado
- **THEN** `resumo` e `proximo_passo` desse resumo são anonimizados,
  qualquer que seja o valor de `ultima_mensagem_id`
