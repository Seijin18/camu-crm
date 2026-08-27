# Tasks — ingestão à prova de falha

## 1. Implementação — boot e schema

- [ ] 1.1 `camucrm/webhook.py::servir()`: chamar `ensure_schema()` no boot
      do processo — falha de conectar/schema derruba o boot com erro alto
      (→ Requirement "Schema ausente falha no boot, não no primeiro
      evento").

## 2. Implementação — staging de eventos brutos

- [ ] 2.1 `camucrm/db.py`: tabela `eventos_recebidos_bruto` em `SCHEMA`
      (`id`, `recebido_em`, `payload jsonb`, `processado`, `processado_em`,
      `erro`, `tentativas`) — ver `design.md` (→ Requirement "Payload bruto
      é preservado antes do processamento").
- [ ] 2.2 `camucrm/webhook.py`/`camucrm/ingest.py`: gravar o payload cru em
      `eventos_recebidos_bruto` ANTES de chamar `ingerir()`; marcar
      `processado=true`/`processado_em` em caso de sucesso, `erro` em caso
      de exceção (→ Requirement "Payload bruto é preservado antes do
      processamento"; → Requirement "Falha de ingestão deixa rastro
      reprocessável").
- [ ] 2.3 `camucrm/cli.py`: comando novo `camucrm reprocessar-falhas` — lê
      linhas com `processado=false`, tenta reingerir cada uma, atualiza o
      status (→ Requirement "Reprocessamento manual de falhas").
- [ ] 2.4 Job de purga de `eventos_recebidos_bruto`: remove linhas
      `processado=true` mais antigas que `RETENCAO_EVENTOS_BRUTOS_DIAS`;
      NUNCA remove linha `processado=false` automaticamente (→ Requirement
      "Retenção da caixa de reprocessamento não apaga falha pendente").

## 3. Implementação — dedupe, transação e CLI

- [ ] 3.1 `camucrm/db.py`: estender proteção de dedupe para evento sem
      `externa_id` via hash do payload cru como identificador sintético (→
      Requirement "Evento sem externa_id ainda é protegido contra
      duplicação").
- [ ] 3.2 `camucrm/ingest.py::ingerir`: envolver `upsert_contato` →
      `get_or_create_conversa` → `registrar_mensagem` numa única transação
      — falha no meio não deixa contato/conversa órfãos (→ Requirement
      "Cadeia de ingestão é transacional").
- [ ] 3.3 `camucrm/cli.py::cmd_ingerir`: default `--transporte evolution`
      (ou detecção de formato), `para_envio=False`; diferenciar na saída
      "ignorado por configuração" de "ignorado por evento benigno" (→
      Requirement "cmd_ingerir não finge sucesso silencioso").

## 4. Investigação — payload em lote

- [ ] 4.1 Investigar (documentação oficial + teste manual, se viável) se
      `messages.upsert` da Evolution API pode chegar em lote/array no corpo
      do webhook. Registrar a conclusão no `design.md` ou em comentário de
      código. Se confirmado, implementar o desmembramento; se não, nenhuma
      mudança adicional é necessária além de documentar a conclusão.

## 5. Testes

- [ ] 5.1 `tests/test_webhook.py`: schema ausente no boot derruba o processo
      com erro alto, não silêncio no primeiro webhook (→ Requirement
      "Schema ausente falha no boot, não no primeiro evento").
- [ ] 5.2 `tests/test_ingest.py`: exceção forçada dentro de `ingerir()`
      deixa a linha correspondente em `eventos_recebidos_bruto` com
      `processado=false` e `erro` preenchido (→ Requirement "Falha de
      ingestão deixa rastro reprocessável").
- [ ] 5.3 `tests/test_cli.py`: `camucrm reprocessar-falhas` reingere com
      sucesso uma falha registrada e marca `processado=true` (→ Requirement
      "Reprocessamento manual de falhas").
- [ ] 5.4 `tests/test_cli.py`: `cmd_ingerir` sem `--transporte` não produz a
      mesma saída de um evento benigno real quando o payload é da Evolution
      API (→ Requirement "cmd_ingerir não finge sucesso silencioso").
- [ ] 5.5 `tests/integration/`: transação única entre `upsert_contato` →
      `get_or_create_conversa` → `registrar_mensagem` — falha no meio não
      deixa contato/conversa órfãos (→ Requirement "Cadeia de ingestão é
      transacional").
- [ ] 5.6 `tests/integration/`: índice de dedupe estendido rejeita
      reentrega de evento sem `externa_id` mas com payload idêntico (→
      Requirement "Evento sem externa_id ainda é protegido contra
      duplicação").
- [ ] 5.7 Suíte completa verde (unitária sem Postgres; integração à parte
      com Postgres).
