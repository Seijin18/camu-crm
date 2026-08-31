# Tasks — painel web de leitura

## 1. Implementação — módulo painel

- [x] 1.1 `camucrm/painel/__init__.py`: criar `app`, `servir(porta=PORTA_PADRAO)`,
      `PORTA_PADRAO = 8093`.
- [x] 1.2 `camucrm/painel/server.py`: FastAPI, middleware de token
      (`X-Camu-Token`, `hmac.compare_digest`, padrão de `webhook._autorizado`),
      CSP `default-src 'self'`, bind `127.0.0.1`, monta `static/` e inclui o
      router de `api.py` (→ Requirement "Autenticação por token opcional";
      → Requirement "Painel não envia e não segura credencial", cenário "bind
      é 127.0.0.1").
- [x] 1.3 `camucrm/painel/api.py`: `APIRouter` prefixo `/api`, rotas finas
      que chamam `views.py` + `db.py`. Rotas em `def` puro (não `async def`)
      — Starlette usa threadpool automaticamente; psycopg é síncrono.
- [x] 1.4 `camucrm/painel/views.py`: funções puras (sem FastAPI, sem I/O) que
      montam dict de resposta — fila (via `rules.fila.montar_fila` intacto),
      kanban (cada coluna com `derivada`/`aceita_drop`/`motivo_recusa`
      citando §3), detalhe de conversa (evidência literal,
      `Classificacao.sinal`), `contato.tem_telefone` booleano nunca telefone
      cru (→ Requirement "Colunas derivadas do kanban recusam drop";
      → Requirement "Telefone nunca em claro";
      → Requirement "Leitura reaproveita as regras existentes, sem duplicar
      cálculo").
- [x] 1.5 `camucrm/painel/static/index.html`, `app.js`, `app.css`: sem
      framework, sem CDN; `textContent` sempre, `innerHTML` nunca (risco de
      XSS do texto de WhatsApp).

## 2. Implementação — `db.py`

- [x] 2.1 `camucrm/db.py`: `fatos_detalhados` (evidência incluída —
      `fatos_da_conversa` a descarta e o invariante 2 do `CLAUDE.md` só é
      verificável com ela presente).
- [x] 2.2 `camucrm/db.py`: `eventos_da_conversa`, `objecoes_da_conversa`,
      `followups_da_conversa`, `marcos_detalhados`, `correcoes_da_conversa`.
- [x] 2.3 `camucrm/db.py`: `listar_mensagens_registradas(desde_id=)`,
      `ultimas_mensagens_globais` — mover o SQL cru hoje em
      `cli._ultimas_mensagens` (`cli.py:412`) para cá; `cli.py` passa a
      chamar `db.ultimas_mensagens_globais`.
- [x] 2.4 `camucrm/db.py`: `contato_resumido`, `token_de_mudanca` (base para
      o change `painel-tempo-real`, mas a função de leitura nasce aqui).
- [x] 2.5 Dataclasses `RascunhoRegistro`, `ObjecaoRegistro`,
      `MensagemRegistro` etc. com sufixo `Registro` (distinto de
      `drafts.Rascunho`, que já existe).

## 3. Implementação — CLI e Makefile

- [x] 3.1 `camucrm/cli.py`: comando `camucrm painel --porta` chamando
      `camucrm.painel.servir`.
- [x] 3.2 `Makefile`: alvos `painel`, `servir`, `acompanhar` (os dois
      últimos hoje faltam).

## 4. Testes

- [x] 4.1 `tests/test_painel_views.py`: card de conversa, colunas do kanban
      (derivada com `aceita_drop:false` e §3 no motivo), filtros e ordenação
      da fila/kanban, `contato.tem_telefone` nunca telefone (→ Requirement
      "Colunas derivadas do kanban recusam drop", cenário "Coluna derivada
      recusa drop"; → Requirement "Telefone nunca em claro", cenário
      "Resposta da API nunca inclui telefone").
- [x] 4.2 `tests/test_painel_api.py`: `TestClient` (ASGITransport, sem rede)
      + `FakeDatabase`; token ausente/errado/certo (espelha `TesteToken` de
      `test_webhook.py`); `test_nao_existe_rota_de_envio` (nenhum path
      contém "enviar" **e** checagem por AST de que `camucrm.painel` não
      importa `camucrm.transport`); `test_detalhe_nunca_devolve_telefone`
      (→ Requirement "Painel não envia e não segura credencial", cenários
      "Nenhuma rota de envio existe" e "camucrm.painel não importa
      camucrm.transport"; → Requirement "Autenticação por token opcional",
      todos os cenários).
- [x] 4.3 Suíte completa (`make test`) verde, sem Postgres e sem rede.
