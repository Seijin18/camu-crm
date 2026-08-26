# Delta: rascunho-registrado

## ADDED Requirements

### Requirement: Rascunho gerado é persistido com as duas opções

Toda geração de rascunho DEVE gravar `opcao_1` e `opcao_2`, ou uma recusa com
`motivo` (§10) — nunca uma linha com geração parcial.

#### Scenario: Geração grava as duas opções

- **WHEN** `drafts.gerar` produz duas opções para uma conversa
- **THEN** uma linha em `rascunhos` é gravada com `opcao_1` e `opcao_2`
  preenchidos

#### Scenario: Recusa é gravada com motivo, não como falha

- **WHEN** `drafts.gerar` decide recusar (encerrar) em vez de propor opções
- **THEN** a linha é gravada com `encerrar: true` e `motivo` preenchido,
  sem `opcao_1`/`opcao_2`

### Requirement: Opção não escolhida não é descartada

Registrar a escolha humana NÃO DEVE apagar a opção não escolhida — as duas
opções continuam na linha, só o campo `escolhida` (ou `texto_final`, se
escrito do zero) muda.

#### Scenario: Escolher uma opção mantém as duas na linha

- **WHEN** o operador registra que usou a opção 1
- **THEN** `opcao_1` e `opcao_2` continuam presentes na linha
- **AND** `escolhida` passa a indicar a opção 1

### Requirement: Um mensagem_id não é reivindicado por mais de um rascunho

O banco DEVE impedir, por constraint (não por validação de aplicação), que
dois rascunhos reivindiquem o mesmo `mensagem_id`.

#### Scenario: Segunda tentativa de vincular o mesmo mensagem_id falha

- **WHEN** um `mensagem_id` já está vinculado a um rascunho e uma segunda
  tentativa de vínculo com o mesmo `mensagem_id` é feita
- **THEN** a segunda tentativa falha por violação do índice único parcial

### Requirement: Vínculo por flag na CLI

`camucrm enviar --rascunho <id> --opcao {1,2}` DEVE gravar `mensagem_id` no
rascunho correspondente, vinculado à mensagem recém-registrada.

#### Scenario: Envio com flag vincula corretamente

- **WHEN** `camucrm enviar --rascunho <id> --opcao 1` é executado
- **THEN** o rascunho `<id>` passa a ter `mensagem_id` apontando para a
  mensagem registrada por esse envio

### Requirement: Reconciliação pelo eco não usa casamento aproximado

A reconciliação automática (caminho 2) DEVE exigir igualdade exata de texto
normalizado (strip, colapso de espaço, casefold). NÃO DEVE usar fuzzy
matching nem LLM para decidir um vínculo.

#### Scenario: Texto editado no envio não vincula automaticamente

- **WHEN** a mensagem `out` recebida do eco da Evolution difere, mesmo que
  minimamente, do texto normalizado de qualquer opção pendente
- **THEN** nenhum vínculo automático é criado — `mensagem_id` permanece
  `NULL` no rascunho pendente

### Requirement: Purga remove texto de rascunho

`purgar_mensagens_antigas` DEVE apagar `opcao_1`, `opcao_2` e `texto_final`
de `rascunhos` associados às mensagens purgadas (§12).

#### Scenario: Purga de mensagens antigas apaga texto do rascunho vinculado

- **WHEN** `purgar_mensagens_antigas` remove uma mensagem antiga vinculada a
  um rascunho
- **THEN** `opcao_1`, `opcao_2` e `texto_final` desse rascunho são apagados
- **AND** a linha do rascunho em si (contexto, escolha, timestamps) não é
  removida
