## Context

Três forças em tensão: exatidão (LISTEN/NOTIFY do Postgres, sem polling),
simplicidade (um poller ingênuo por cliente), e o requisito real — um
cliente que ficou offline e reconecta precisa saber o que perdeu, não só o
que muda daqui para frente.

## Decisão: poller único por processo, não LISTEN/NOTIFY agora

`camucrm/painel/stream.py` roda um único `asyncio.Task` por processo que
consulta `token_de_mudanca` a cada 1,5s e dispara um `asyncio.Event`
compartilhado para todos os geradores SSE conectados quando o token muda.

Motivo de não usar `LISTEN/NOTIFY` já nesta entrega: `LISTEN/NOTIFY` é exato
(zero polling), mas não resolve sozinho o buraco de reconexão — o cliente
que caiu e volta ainda precisaria de algum cursor para saber o que perdeu
entre a queda e a reconexão. O token de 3 partes **é** esse cursor
(`?desde_id=N` no stream), então ele é necessário de qualquer forma.

Como o token já cumpre o papel de cursor, trocar o corpo do poller por
`LISTEN camu_mudanca` no futuro não muda uma linha do contrato SSE exposto
ao cliente — só a implementação interna de como `stream.py` descobre que
algo mudou. Isso é registrado no docstring de `stream.py` para que o upgrade
seja barato (trocar a implementação do poller) e não uma reescrita do
protocolo.

## Risco documentado: bloqueio do event loop

`psycopg` é síncrono. Uma chamada direta a ele dentro de um gerador
`async def` (o gerador SSE) bloqueia o event loop inteiro — todos os
clientes SSE conectados, não só o que fez a chamada. Mitigação:

- Toda leitura de banco dentro do gerador SSE passa por `asyncio.to_thread`.
- Rotas normais de `api.py` continuam em `def` puro — Starlette já usa
  threadpool automaticamente para elas, sem necessidade de `to_thread`
  explícito.
- Pool de conexão próprio do painel, com `max_size` maior que o padrão de 5:
  conexões SSE ficam penduradas por longos períodos e não podem esgotar o
  pool usado pelas rotas normais.

## Alternativas descartadas

- **Polling por cliente** (cada aba consulta o backend a cada 1,5s): três
  abas abertas geram três consultas por intervalo em vez de uma. O poller
  único por processo evita essa multiplicação.
- **WebSocket**: mais estado (conexão bidirecional, handshake, reconexão
  mais complexa) sem ganho — o caso de uso é unidirecional (servidor →
  cliente), e SSE já cobre isso com menos superfície.
