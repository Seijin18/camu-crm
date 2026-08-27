# Delta: marco-manual-visivel-na-aba-conversas

## ADDED Requirements

### Requirement: Conversa fechada por marco manual continua na aba Conversas

`GET /api/conversas` DEVE incluir, junto das conversas abertas, as
conversas com `resultado` preenchido (`ganho`/`perdido`, fechadas por marco
manual via `acoes.marcar_marco`) — nunca deixar uma conversa desaparecer da
lista só porque foi encerrada manualmente. Cada card exposto DEVE trazer o
campo `resultado`, para a UI diferenciar visualmente uma conversa fechada
de uma aberta.

#### Scenario: Conversa marcada ganho ou perdido continua na lista

- **WHEN** uma conversa é fechada manualmente com marco `ganho` ou
  `perdido`
- **THEN** `GET /api/conversas` continua incluindo essa conversa na
  resposta
- **AND** o card dessa conversa expõe `resultado` com o valor gravado
  (`"ganho"` ou `"perdido"`)

#### Scenario: Conversa aberta não é afetada

- **WHEN** uma conversa não tem marco manual (`resultado IS NULL`)
- **THEN** o card continua sendo exibido normalmente, com `resultado`
  ausente ou `null`

### Requirement: Kanban e fila continuam mostrando só conversas abertas

`GET /api/kanban` e a fila do dia (`rules/fila.py::montar_fila`) DEVEM
continuar restritos a conversas abertas (`resultado IS NULL`) — a inclusão
de conversas fechadas manualmente na aba Conversas não se propaga para
essas duas telas.

#### Scenario: Conversa fechada manualmente some do kanban e da fila

- **WHEN** uma conversa é fechada manualmente com marco `ganho` ou
  `perdido`
- **THEN** ela não aparece mais em `GET /api/kanban`
- **AND** ela não aparece mais na fila do dia
