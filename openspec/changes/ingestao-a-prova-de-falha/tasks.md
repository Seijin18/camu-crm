# Tasks — ingestão à prova de falha

## 1. Implementação — boot e schema

- [x] 1.1 `camucrm/webhook.py::servir()`: chamar `ensure_schema()` no boot
      do processo — falha de conectar/schema derruba o boot com erro alto
      (→ Requirement "Schema ausente falha no boot, não no primeiro
      evento").

## 2. Implementação — staging de eventos brutos

- [x] 2.1 `camucrm/db.py`: tabela `eventos_recebidos_bruto` em `SCHEMA`
      (`id`, `recebido_em`, `payload jsonb`, `processado`, `processado_em`,
      `erro`, `tentativas`) — ver `design.md` (→ Requirement "Payload bruto
      é preservado antes do processamento").
- [x] 2.2 `camucrm/webhook.py`: gravar o payload cru em
      `eventos_recebidos_bruto` ANTES de chamar `ingerir()`; marcar
      `processado=true`/`processado_em` em caso de sucesso, `erro` em caso
      de exceção (→ Requirement "Payload bruto é preservado antes do
      processamento"; → Requirement "Falha de ingestão deixa rastro
      reprocessável"). Implementado em `webhook.py::_processar` (não em
      `ingest.py` — segue o fluxo exato do design.md: "webhook.py grava uma
      linha aqui antes de chamar `ingerir()`"; `cmd_ingerir` não grava
      staging, de propósito, porque ali há um operador olhando a saída.
- [x] 2.3 `camucrm/cli.py`: comando novo `camucrm reprocessar-falhas` — lê
      linhas com `processado=false`, tenta reingerir cada uma, atualiza o
      status (→ Requirement "Reprocessamento manual de falhas").
- [x] 2.4 Job de purga de `eventos_recebidos_bruto`
      (`Database.purgar_eventos_brutos_antigos`, chamado por `cmd_purgar`):
      remove linhas `processado=true` mais antigas que
      `RETENCAO_EVENTOS_BRUTOS_DIAS`; NUNCA remove linha `processado=false`
      automaticamente (→ Requirement "Retenção da caixa de reprocessamento
      não apaga falha pendente").

## 3. Implementação — dedupe, transação e CLI

- [x] 3.1 `camucrm/ingest.py::_externa_id_efetivo`: estende proteção de
      dedupe para evento sem `externa_id` via hash do payload cru (`bruto`)
      como identificador sintético — reaproveita o índice único parcial já
      existente (`mensagens_externa_id_idx`), sem índice novo em `db.py` (→
      Requirement "Evento sem externa_id ainda é protegido contra
      duplicação").
- [x] 3.2 `camucrm/ingest.py::ingerir`: `upsert_contato` →
      `get_or_create_conversa` → `registrar_mensagem` rodam dentro de
      `Database.transacao()` (uma única transação Postgres, via `conn=`
      passado aos três) — falha no meio não deixa contato/conversa órfãos
      (→ Requirement "Cadeia de ingestão é transacional"). Não estava nas
      decisões do `design.md` (que cobre só staging/retenção/comando
      manual/dedupe), mas é requirement normativo de `spec.md`/`tasks.md` —
      implementado integralmente, com teste de integração provando o
      rollback contra Postgres real.
- [x] 3.3 `camucrm/cli.py::cmd_ingerir`: `--transporte` default para
      `evolution` (com `para_envio=False`), preservando o flag para
      overrides; quando o transporte efetivo não é `evolution` e o payload
      tem cara de evento real da Evolution API, a saída avisa
      "CONFIGURAÇÃO" em vez de imprimir o mesmo "evento ignorado" de um
      benigno de verdade (→ Requirement "cmd_ingerir não finge sucesso
      silencioso").

## 4. Investigação — payload em lote

- [x] 4.1 Investigado via documentação oficial (webhooks da Evolution API)
      e busca nas issues públicas do repositório: nenhuma evidência de que
      `messages.upsert` chegue em lote/array no corpo do webhook — `data` é
      sempre um objeto único nos exemplos e relatos encontrados. Conclusão
      registrada em `design.md` ("Investigação: `messages.upsert` pode
      chegar em lote?"). Nenhuma mudança de código foi feita por causa
      disso; o guard defensivo já existente em
      `EvolutionTransporte.receber` (`isinstance(dados, Mapping)`) já
      garante que, se acontecer, o evento é ignorado sem dado errado — ver
      comentário adicionado ali.

## 5. Testes

- [x] 5.1 `tests/test_webhook.py::TesteBootFalhaAlto`: schema ausente/banco
      indisponível no boot derruba o processo com erro alto (via mock,
      `uvicorn.run` nunca chamado), e o caminho de sucesso confere que
      `ensure_schema()` roda antes de subir o serviço (→ Requirement
      "Schema ausente falha no boot, não no primeiro evento").
- [x] 5.2 `tests/test_webhook.py::TesteStagingDeEventosBrutos`: exceção
      forçada dentro do processamento do webhook deixa a linha
      correspondente em `eventos_recebidos_bruto` marcada via
      `marcar_evento_bruto_falhou` (não `processado`), com o erro
      registrado (→ Requirement "Falha de ingestão deixa rastro
      reprocessável"). **Divergência do texto original da tarefa**: o
      teste vive em `test_webhook.py`, não em `test_ingest.py` — o staging
      é responsabilidade de `webhook.py::_processar` (design.md), não de
      `ingest.ingerir`, então é ali que o comportamento é observável.
- [x] 5.3 `tests/test_cli.py::TesteReprocessarFalhas`: `camucrm
      reprocessar-falhas` reingere com sucesso uma falha registrada e
      marca `processado=true`; evento que falha de novo continua pendente
      com `tentativas` incrementado (→ Requirement "Reprocessamento manual
      de falhas").
- [x] 5.4 `tests/test_cli.py::TesteCmdIngerirNaoFingeSucessoSilencioso`:
      `cmd_ingerir` sem `--transporte` usa `evolution` por padrão; com
      `--transporte console` sobre um payload real da Evolution API avisa
      "CONFIGURAÇÃO" (diferente de um evento benigno de verdade, que não
      avisa) (→ Requirement "cmd_ingerir não finge sucesso silencioso").
- [x] 5.5 `tests/integration/test_ingestao_a_prova_de_falha.py::
      TesteCadeiaDeIngestaoETransacional`: transação única entre
      `upsert_contato` → `get_or_create_conversa` → `registrar_mensagem` —
      falha no meio não deixa contato/conversa órfãos, contra Postgres real
      (→ Requirement "Cadeia de ingestão é transacional").
- [x] 5.6 `tests/integration/test_ingestao_a_prova_de_falha.py::
      TesteDedupeEstendidoContraPostgres` (mais o equivalente em memória em
      `tests/test_ingest.py::TesteDedupeSemExternaId`): índice de dedupe
      existente rejeita reentrega de evento sem `externa_id` mas com
      payload idêntico, via hash sintético (→ Requirement "Evento sem
      externa_id ainda é protegido contra duplicação").
- [x] 5.7 Suíte completa verde: `make test` (493 testes, 54 skipped sem
      `CAMU_TEST_DSN`) e `make test-db` (54 testes contra Postgres real,
      incluindo os 6 novos deste change) — ambas OK.
