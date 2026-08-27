# Identificação e relógio confiáveis na recepção

## Why

Auditoria completa do pipeline de recepção (`transport/evolution.py`)
encontrou dois problemas de identificação/tempo que corrompem dado de forma
permanente e silenciosa, sem exigir nada de anormal — só tráfego real:

1. **`@lid`, `@broadcast`, `status@broadcast` não são filtrados.** O JID de
   um evento pode chegar em formatos que não representam uma conversa 1:1
   com um contato real: `status@broadcast` (status do WhatsApp, não
   mensagem), `@broadcast` (lista de transmissão) e `@lid` (identificador
   "linked ID", usado pelo WhatsApp em certos fluxos multi-dispositivo em
   vez do PN — "phone number" — real). Sem filtro, esses eventos podem: (a)
   criar um contato fantasma com `telefone_hash` derivado de uma string
   vazia ou de um identificador que não é telefone; (b) no caso de `@lid`
   especificamente, **splitar o histórico de um mesmo cliente real em dois
   contatos** — um sob o PN, outro sob o LID — cada um vendo só metade da
   conversa, o que corrompe `bola_com`, temperatura e estágio dos dois
   "contatos" resultantes.
2. **`_timestamp` não valida faixa sã.** Um timestamp futuro ou corrompido
   (relógio de celular errado, campo malformado) "vence" para sempre contra
   qualquer mensagem real subsequente, porque `GREATEST` em
   `ultimo_inbound`/`ultimo_outbound` sempre escolhe o maior valor — inclusive
   um valor futuro incorreto. Uma vez gravado, nenhuma mensagem real
   consegue superá-lo: `bola_com`, temperatura (§5) e `eventos_estagio.em`
   ficam contaminados de forma permanente e silenciosa a partir desse ponto.

## What Changes

- `transport/evolution.py::receber`: filtrar `status@broadcast`, `@broadcast`
  — eventos desse JID são descartados (comportamento igual ao de ruído de
  protocolo já tratado em `mensagem-sem-texto-preservada`, `receber()`
  devolve `None`).
- `transport/evolution.py::receber`: tratar `@lid` sem criar contato fantasma
  — na ausência de um campo PN confiável no payload, o evento é recusado e
  logado (nunca silenciosamente aceito com `telefone=""`). Decisão de
  política simples e conservadora: sem PN real, não há identificação
  confiável o suficiente para gravar um contato nesta correção; a
  reconciliação de LID↔PN (se necessária no futuro) fica fora de escopo
  aqui, registrada como observação, não como bug pendente deste change.
- `transport/evolution.py::_timestamp`: clampar timestamp implausível —
  `min(timestamp_recebido, agora())` para qualquer uso em `enviada_em` e no
  `GREATEST` de `ultimo_inbound`/`ultimo_outbound`. Um timestamp futuro nunca
  passa adiante do relógio real do servidor. Timestamp anterior a uma data
  mínima sã (ex. antes do lançamento do produto) é tratado com a mesma
  política de clamp, não de rejeição do evento inteiro — a mensagem em si
  ainda é real e deve ser gravada, só o timestamp usado para ordenação/
  `GREATEST` é corrigido.

## Impact

- Specs afetadas: `identificacao-e-relogio-confiaveis` (nova)
- Código alterado: `camucrm/transport/evolution.py` (`receber`,
  `_timestamp`)
- Testes alterados: `tests/test_transport.py` (evento de
  `status@broadcast`/`@broadcast` não cria/atualiza contato; evento `@lid`
  sem PN confiável é recusado, não silenciosamente aceito; timestamp futuro
  não "trava" `ultimo_inbound` à frente de mensagens reais subsequentes)
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Reconciliação LID↔PN** (unir um contato criado sob LID com o mesmo
  cliente sob PN real, caso isso já tenha ocorrido antes desta correção) —
  não há dado suficiente no payload da Evolution API, hoje, para fazer essa
  reconciliação com segurança; fica registrada como observação em
  `openspec/project.md`, não como item pendente deste change.
- **`editedMessage`/`protocolMessage` (REVOKE)** — fora de escopo, já
  registrado como backlog de baixa prioridade (ver `openspec/project.md`).
