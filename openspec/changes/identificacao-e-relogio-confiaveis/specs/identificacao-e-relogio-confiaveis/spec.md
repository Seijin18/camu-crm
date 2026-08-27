# Delta: identificacao-e-relogio-confiaveis

## ADDED Requirements

### Requirement: Broadcast e status não criam evento

Um evento cujo JID é `status@broadcast` ou termina em `@broadcast` NÃO DEVE
criar ou atualizar contato, conversa ou mensagem — `receber()` DEVE devolver
`None` para esses casos.

#### Scenario: Status do WhatsApp não cria contato

- **WHEN** chega um evento cujo remetente é `status@broadcast`
- **THEN** `receber()` devolve `None`
- **AND** nenhum contato ou conversa é criado ou atualizado

#### Scenario: Lista de transmissão não cria contato

- **WHEN** chega um evento cujo JID termina em `@broadcast`
- **THEN** `receber()` devolve `None`
- **AND** nenhum contato ou conversa é criado ou atualizado

### Requirement: JID sem PN confiável não cria contato fantasma

Um evento cujo JID é `@lid` (linked ID) e cujo payload não traz um campo de
PN (phone number) confiável NÃO DEVE criar um contato — a ausência de
identificação confiável é tratada como recusa registrada (log), não como
aceitação silenciosa com telefone vazio ou inválido.

#### Scenario: Evento @lid sem PN é recusado

- **WHEN** chega um evento cujo JID é `@lid` e o payload não contém um
  campo de PN confiável
- **THEN** `receber()` devolve `None` (ou equivalente de recusa) sem criar
  contato
- **AND** a recusa é registrada em log, não silenciosa

### Requirement: Timestamp futuro não trava o relógio da conversa

O timestamp de um evento recebido DEVE ser clampado a `min(timestamp,
agora())` antes de ser usado em `enviada_em` ou em qualquer `GREATEST`
contra `ultimo_inbound`/`ultimo_outbound`. Um timestamp anterior a uma data
mínima sã DEVE ser clampado da mesma forma, sem descartar a mensagem em si.

#### Scenario: Timestamp futuro não supera mensagens reais subsequentes

- **WHEN** um evento chega com timestamp no futuro, seguido por uma
  mensagem real com timestamp presente
- **THEN** `ultimo_inbound` (ou `ultimo_outbound`, conforme direção) reflete
  a mensagem real subsequente, não fica preso ao valor futuro

#### Scenario: Timestamp implausivelmente antigo é clampado, mensagem é gravada

- **WHEN** um evento chega com timestamp anterior à data mínima sã
- **THEN** a mensagem ainda é gravada normalmente
- **AND** o timestamp usado para ordenação/`GREATEST` é o valor clampado,
  não o valor bruto implausível
