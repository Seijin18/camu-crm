# Painel web de leitura

## Why

Hoje não há como ver o sistema funcionando sem alternar comandos. `camucrm
acompanhar` (`cli.py:281`) é instrumento de operação — ANSI, `time.sleep`, e
o próprio docstring do comando diz que não é o painel da §13. Três achados
sustentam este change: não existe avaliação de conversa persistida em lugar
nenhum; rascunhos gerados por `drafts.gerar` são impressos e descartados; e
`openspec/` está vazio — nada do sistema em produção tem planejamento
registrado.

Isso cria tensão explícita com §6/§13: painel é o passo 8 de §13, e este
change o antecipa antes de haver histórico suficiente para justificá-lo por
ordem natural. A tensão é assumida, não escondida — ver a nota em
`project.md` sobre a antecipação. A mitigação de escopo: a fila continua
sendo `/` (a mesma prioridade de `rules.fila.montar_fila`), o kanban vira uma
aba — nenhuma reformulação do modelo de decisão, só uma superfície de
leitura sobre o que já existe.

## What Changes

- Módulo novo `camucrm/painel/`:
  - `__init__.py`: expõe `app`, `servir(porta=PORTA_PADRAO)`,
    `PORTA_PADRAO = 8093`.
  - `server.py`: FastAPI, middleware de token opcional (header
    `X-Camu-Token`, comparação por `hmac.compare_digest` — mesmo padrão de
    `webhook._autorizado`), cabeçalho CSP `default-src 'self'`, bind
    `127.0.0.1`, monta `static/` e inclui o router de `api.py`.
  - `api.py`: `APIRouter` com prefixo `/api`, rotas finas em `def` puro (não
    `async def` — Starlette usa threadpool automaticamente e `psycopg` é
    síncrono) que só chamam `views.py` e `db.py`.
  - `views.py`: funções puras — sem FastAPI, sem I/O — que montam os dicts de
    resposta: fila (via `rules.fila.montar_fila`, intacto), kanban (cada
    coluna sai com `derivada`, `aceita_drop`, `motivo_recusa` citando §3),
    detalhe de conversa (evidência literal, incluindo `Classificacao.sinal`
    recalculado com `persistir=False` — primeira superfície do sistema a
    mostrar esse campo), `contato.tem_telefone` como booleano — nunca o
    telefone em claro (§12).
  - `static/index.html`, `app.js`, `app.css`: sem framework, sem bundler, sem
    CDN. `textContent` sempre, `innerHTML` nunca — texto de WhatsApp é
    conteúdo não confiável.
- `camucrm/db.py` ganha métodos de leitura novos: `fatos_detalhados`,
  `eventos_da_conversa`, `objecoes_da_conversa`, `followups_da_conversa`,
  `marcos_detalhados`, `correcoes_da_conversa`,
  `listar_mensagens_registradas(desde_id=)`, `ultimas_mensagens_globais`
  (move o SQL cru hoje em `cli._ultimas_mensagens`, `cli.py:412`, para
  `db.py` — `cli.py` passa a chamar o método novo), `contato_resumido`,
  `token_de_mudanca` (leitura nasce aqui; o contrato pleno de cursor é do
  change `painel-tempo-real`). Dataclasses de linha novas com sufixo
  `Registro` (distinto de `drafts.Rascunho`, que já existe).
- `camucrm/cli.py` ganha `camucrm painel --porta`, chamando
  `camucrm.painel.servir`.
- `Makefile` ganha os alvos `painel`, `servir` e `acompanhar` — os dois
  últimos hoje não existem, apesar dos comandos correspondentes já existirem
  na CLI.
- Reuso explícito, sem duplicar cálculo: `db.listar_conversas_abertas`,
  `pipeline.recalcular(persistir=False)`, `rules.fila.montar_fila` intacto,
  `rules.estagio.sugere_b2b`/`mudar_funil`, `taxonomia.estagio_label`/
  `is_terminal`, `metrics.metricas_chave`/`tempo_por_estagio`/
  `saude_taxonomia`, `drafts.gerar`.
- N+1 conhecido e aceito: `pipeline.recalcular` custa ~9 consultas por
  conversa. Mitigado com `?limite=200` na listagem e log de aviso acima de
  150 conversas abertas. Documentado, não otimizado nesta entrega.
- Sem SSE nesta entrega — atualização é por botão manual (SSE é o escopo do
  change `painel-tempo-real`).

## Impact

- Specs afetadas: `painel-web` (nova)
- Código alterado: `camucrm/painel/__init__.py`, `camucrm/painel/server.py`,
  `camucrm/painel/api.py`, `camucrm/painel/views.py`,
  `camucrm/painel/static/*`, `camucrm/db.py`, `camucrm/cli.py`, `Makefile`
- Testes alterados: `tests/test_painel_views.py` (novo),
  `tests/test_painel_api.py` (novo)
- Bloqueado por: —
- Bloqueia: `painel-tempo-real`, `acoes-no-painel`, `rascunho-registrado`,
  `resumo-conversa` — todos dependem da fundação do painel (módulo, rotas de
  leitura, layout de tela) introduzida aqui.

## Fora de escopo (decisão explícita)

- SSE / tempo real (`painel-tempo-real`).
- Drag-and-drop e demais ações humanas no painel (`acoes-no-painel`).
- Rascunho persistido em tabela (`rascunho-registrado`).
- Resumo de conversa por LLM (`resumo-conversa`).
- Telas agregadas de análise de desempenho (`analise-desempenho`).
- Envio de mensagem pelo painel — `camucrm enviar` continua o único caminho.
- Login, sessão ou multiusuário.
- Layout mobile.
- Biblioteca de gráfico ou qualquer build step (webpack, npm, etc.).
- Editar fato diretamente na tela.
- Expor o servidor além de `127.0.0.1`.
- Migrações de schema.
