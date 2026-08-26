# Painel atualiza em tempo real

## Why

Sem atualização automática, o operador precisa recarregar a tela para ver
mensagem nova, mudança de estágio ou de fila — o kanban perde a proposta de
"ver o sistema trabalhando" que motivou antecipar o painel (`project.md`,
nota sobre antecipação).

## What Changes

- `camucrm/db.py::token_de_mudanca` (leitura já introduzida em
  `painel-leitura`) vira aqui o contrato pleno: string de 3 partes
  `f"{max_mensagem_id}:{max_evento_estagio_id}:{epoch(max_conversas.atualizado_em)}"`.
- `camucrm/painel/stream.py` novo: poller único por processo
  (`asyncio.Task`, intervalo 1,5s) que compara `token_de_mudanca` a cada
  ciclo e dispara um `asyncio.Event` compartilhado quando ele muda, para
  todos os geradores SSE conectados. Heartbeat `: ping` a cada 20s para
  manter a conexão viva atrás de proxy.
- Cliente SSE do lado do JS via `fetch()` + `ReadableStream`, não
  `EventSource` — `EventSource` não aceita header customizado, e o token
  precisa continuar fora da URL e do log de acesso. Reconexão com
  `?desde_id=N` no query string do stream (não do token de auth).
- `camucrm/painel/server.py` ganha pool de conexão próprio do painel com
  `max_size` maior que o padrão de 5 — conexões SSE penduradas por múltiplos
  clientes não podem esgotar o pool geral.
- Toda leitura de banco dentro do gerador `async def` do SSE passa por
  `asyncio.to_thread` — psycopg é síncrono; uma chamada direta ali congela
  todos os clientes SSE conectados (o bug mais provável identificado no
  design). Rotas normais de `api.py` continuam em `def` puro, sem mudança.

## Impact

- Specs afetadas: `painel-tempo-real` (nova)
- Código alterado: `camucrm/db.py` (`token_de_mudanca`),
  `camucrm/painel/stream.py` (novo), `camucrm/painel/server.py` (pool
  próprio, startup/shutdown do poller), `camucrm/painel/static/app.js`
- Testes alterados: `tests/test_painel_stream.py` (novo)
- Bloqueado por: `painel-leitura`
- Bloqueia: —
