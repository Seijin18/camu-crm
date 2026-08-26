# Ações humanas compartilhadas entre CLI e painel

## Why

Hoje `cli.cmd_marcar` (`cli.py:203`) e `cli.cmd_tipo` (`cli.py:219`) carregam
a sequência completa de efeitos (marco → resultado → recalcular; ou
set_tipo + set_funil + registrar_correcao + mudar_funil + evento) só na CLI.
O painel precisa da mesma ação — drag-and-drop no kanban, marcar marco
manual. Se o painel importar `cli`, inverte a dependência (UI dependendo de
UI); se reimplementar a sequência, cria divergência entre os dois caminhos —
o mesmo motivo já declarado em `ingest.py` para módulos de ação
compartilhados.

## What Changes

- `camucrm/acoes.py` novo: módulo de topo com as ações humanas registradas —
  marcar marco (marco → resultado → recalcular) e mudar tipo/funil com
  correção (set_tipo + set_funil + registrar_correcao + mudar_funil +
  evento).
- `cli.cmd_marcar` e `cli.cmd_tipo` refatorados para chamar `acoes.py` em
  vez de carregar a sequência inline. Comportamento observável idêntico.
- `camucrm/painel/views.py::marco_permitido(funil, marco)` função pura nova
  que recusa combinação inválida (ex.: `consignacao_assinada` numa conversa
  B2C). Ela é consumida por `acoes.py` e portanto passa a valer também na
  CLI — corrigindo uma falha hoje aceita silenciosamente (marco órfão fora
  do funil correto).
- Drag-and-drop no kanban do painel: `POST /api/marcos` (drop numa coluna
  terminal grava `marcos_manuais`) e `POST /api/correcoes` (arrastar entre
  funis grava `correcoes`); ambos passam por `acoes.py`.
- O servidor recusa de novo o drop numa coluna derivada, com HTTP 422 e
  corpo `{"erro": "...", "regra": "§N"}`. Usabilidade (JS desabilita
  visualmente o alvo) e contrato (servidor recusa de qualquer forma) são
  camadas diferentes — a segunda não pode depender da primeira.

## Impact

- Specs afetadas: `acoes-humanas` (nova)
- Código alterado: `camucrm/acoes.py` (novo), `camucrm/cli.py`
  (`cmd_marcar`, `cmd_tipo` refatorados), `camucrm/painel/views.py`
  (`marco_permitido`), `camucrm/painel/api.py` (`POST /api/marcos`,
  `POST /api/correcoes`)
- Testes alterados: `tests/test_acoes.py` (novo), testes existentes de CLI
  para `cmd_marcar`/`cmd_tipo` (ajustados ao novo caminho, comportamento
  idêntico), `tests/test_painel_api.py` (estendido)
- Bloqueado por: `painel-leitura`
- Bloqueia: —
