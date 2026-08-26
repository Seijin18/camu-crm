# Delta: acoes-humanas

## ADDED Requirements

### Requirement: Ação humana compartilhada entre CLI e painel

Toda ação humana (marcar marco, mudar tipo/funil) DEVE ser implementada uma
única vez em `camucrm/acoes.py` e consumida tanto pela CLI quanto pelo
painel. Nenhum dos dois caminhos DEVE reimplementar a sequência de efeitos.

#### Scenario: Marcar marco pela CLI e pelo painel produz o mesmo estado final

- **WHEN** o mesmo marco é registrado numa conversa via CLI (`camucrm
  marcar`) e, em outra conversa equivalente, via painel (drop no kanban)
- **THEN** o estado final (marco gravado, resultado, estágio recalculado) é
  o mesmo nos dois caminhos

### Requirement: Marco incompatível com o funil é recusado

Um marco que não se aplica ao funil da conversa (ex.:
`consignacao_assinada` numa conversa B2C) DEVE ser recusado, tanto pela CLI
quanto pela API do painel.

#### Scenario: consignacao_assinada em conversa B2C é recusado

- **WHEN** `marcar consignacao_assinada` é chamado numa conversa cujo funil
  é B2C
- **THEN** a ação é recusada, pela CLI e pela API, sem gravar o marco

### Requirement: Correção é sempre gravada

Toda mudança de funil DEVE gravar uma linha em `correcoes`, independente do
caminho que a originou (CLI ou painel).

#### Scenario: Mudar de funil grava correção

- **WHEN** o funil de uma conversa muda, por qualquer caminho
- **THEN** uma linha correspondente existe em `correcoes`

### Requirement: Coluna derivada recusa drop com 422

`POST /api/marcos` numa coluna marcada como `derivada` (ver
`painel-web`) DEVE responder HTTP 422 com corpo contendo `erro` e `regra`
citando a seção do documento (§3).

#### Scenario: Drop em coluna derivada devolve 422

- **WHEN** `POST /api/marcos` é chamado para gravar um marco numa coluna
  derivada
- **THEN** a resposta é HTTP 422 com `{"erro": "...", "regra": "§3"}`
- **AND** nenhum marco é gravado
