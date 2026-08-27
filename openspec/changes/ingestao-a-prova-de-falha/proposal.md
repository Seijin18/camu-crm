# Ingestão à prova de falha

## Why

Auditoria completa da recepção/ingestão (`webhook.py`, `ingest.py`, `db.py`)
encontrou uma cadeia de falhas silenciosas que, juntas, significam que uma
mensagem real de cliente pode desaparecer sem deixar rastro nenhum,
enquanto a Evolution API continua recebendo `2xx` e nunca reentrega:

1. **`webhook.get_db()` nunca chama `ensure_schema()`.** Contra um banco
   novo, recriado, ou uma migração ainda não aplicada, a primeira mensagem
   recebida falha ao gravar — e a exceção é engolida (item 2), então o
   operador não vê nada até perceber, por fora, que conversas não estão
   aparecendo.
2. **Qualquer exceção dentro de `ingerir()` é engolida com um log único, sem
   fila de reprocessamento.** O comentário do código assume que "a próxima
   mensagem resolve" — verdade para o estado agregado (`bola_com`,
   temperatura), falso para o CONTEÚDO da mensagem perdida: o que o cliente
   disse naquele evento específico nunca é recuperado, só o relógio segue em
   frente.
3. **Sem transação única entre `upsert_contato` → `get_or_create_conversa` →
   `registrar_mensagem`.** Uma falha no meio dessa cadeia deixa contato ou
   conversa órfãos, sem qualquer flag indicando que um evento foi
   parcialmente processado e perdido.
4. **Índice de dedupe é parcial** (`WHERE externa_id IS NOT NULL`) — um
   evento sem `key.id` (payload malformado) não é protegido contra
   duplicação em reentrega da Evolution API.
5. **`camucrm cli ingerir` sem `--transporte evolution`** cai no
   `ConsoleTransporte` por padrão e produz a MESMA saída ("evento ignorado")
   de um evento benigno real — um operador rodando o comando manualmente
   para depurar um evento perdido não consegue distinguir "ignorei porque
   configurei errado" de "ignorei porque o evento realmente não representa
   nada".

Ver `design.md` para a decisão de staging/DLQ de eventos brutos que resolve
os itens 1–3 de uma vez.

## What Changes

- `webhook.py::servir()`: chamar `ensure_schema()` no boot do processo — é
  idempotente, já seguro rodar de novo. Falha de conectar ou de schema vira
  erro alto no boot (processo não sobe), não silêncio no primeiro evento.
- Tabela nova `eventos_recebidos_bruto` (ver `design.md` para o desenho
  completo): grava o payload cru + timestamp de chegada ANTES de qualquer
  parsing/ingestão. Se `_processar` falhar por qualquer motivo, o payload já
  está persistido e disponível para reprocessamento — nada se perde mesmo
  quando o processamento falha no meio.
- Comando novo `camucrm reprocessar-falhas`: lê `eventos_recebidos_bruto`
  marcados como não processados com sucesso, tenta reingerir cada um,
  marca como processado em caso de sucesso.
- `db.py`: índice de dedupe estendido para cobrir evento sem `externa_id` —
  ver `design.md` para a estratégia (fallback a um hash do payload cru
  quando `key.id` está ausente).
- `ingest.py::ingerir` (ou onde a cadeia é orquestrada): `upsert_contato` →
  `get_or_create_conversa` → `registrar_mensagem` passam a rodar dentro de
  uma única transação — falha em qualquer ponto do meio não deixa contato
  ou conversa órfãos sem a mensagem correspondente.
- `cli.py::cmd_ingerir`: alinhar com o comportamento do webhook — default
  `--transporte evolution` (ou detectar o formato do payload), `para_envio=
  False` por padrão, e diferenciar na saída "ignorado por configuração
  divergente do webhook" de "ignorado por ser evento benigno" (reação,
  broadcast, etc).
- Investigação (não implementação às cegas, registrada como tarefa dentro
  deste change, não como change à parte): confirmar contra documentação/
  comportamento real da Evolution API se `messages.upsert` pode chegar em
  lote (array) no corpo do webhook. Se confirmado, decidir e implementar o
  desmembramento; se não, documentar a conclusão e por que nenhuma mudança
  foi necessária.

## Impact

- Specs afetadas: `ingestao-a-prova-de-falha` (nova)
- Código alterado: `camucrm/webhook.py` (`servir`, boot), `camucrm/db.py`
  (`SCHEMA` — `eventos_recebidos_bruto`, índice de dedupe estendido),
  `camucrm/ingest.py` (gravação do payload bruto antes de processar),
  `camucrm/cli.py` (`cmd_ingerir`, comando novo `reprocessar-falhas`)
- Testes alterados: `tests/test_webhook.py` (schema ausente falha alto no
  boot), `tests/test_ingest.py` (exceção dentro de `ingerir()` deixa rastro
  reprocessável em `eventos_recebidos_bruto`), `tests/test_cli.py`
  (`cmd_ingerir` sem `--transporte` não finge sucesso silencioso; comando
  `reprocessar-falhas`), `tests/integration/` (transação única
  contato→conversa→mensagem; índice de dedupe estendido)
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Retenção permanente de `eventos_recebidos_bruto`** — é uma caixa de
  reprocessamento de curto prazo (ver `design.md` para a janela de
  retenção), não um histórico permanente paralelo a `mensagens`.
- **Reconciliação automática/silenciosa de falhas** — `reprocessar-falhas`
  é sempre acionado manualmente pelo operador, nunca um cron automático
  nesta primeira versão.
