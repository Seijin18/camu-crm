# Tasks — painel atualiza em tempo real

## 1. Implementação

- [x] 1.1 `camucrm/db.py`: `token_de_mudanca` retorna a string de 3 partes
      descrita no `design.md` (→ Requirement "Token de mudança como
      cursor").
- [x] 1.2 `camucrm/painel/stream.py`: poller único (`asyncio.Task`) a cada
      1,5s, `asyncio.Event` compartilhado, heartbeat `: ping` a cada 20s,
      gerador SSE consumindo `desde_id` da querystring (→ Requirement
      "Poller único por processo"; → Requirement "Token de mudança como
      cursor", cenário "Reconexão com desde_id não perde eventos").
- [x] 1.3 `camucrm/painel/server.py`: pool de conexão próprio do painel,
      `max_size` > 5; startup/shutdown do poller único.
- [x] 1.4 `camucrm/painel/static/app.js`: cliente SSE via `fetch()` +
      `ReadableStream`, parser manual (~25 linhas), header `X-Camu-Token`,
      reconexão com `desde_id` do último evento recebido (→ Requirement
      "Token nunca na URL").

## 2. Testes

- [x] 2.1 `tests/test_painel_stream.py`: token como comparação pura (sem
      side effect); emite só quando muda; heartbeat; reconexão com
      `desde_id` não pula mensagem; relógio falso — nada de `sleep` real
      (→ Requirement "Poller único por processo"; → Requirement "Leitura de
      banco no gerador SSE nunca bloqueia o loop").
- [x] 2.2 Suíte completa verde.
