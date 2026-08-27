# Conversa fechada manualmente continua na aba Conversas

## Why

`GET /api/conversas` (aba "Conversas" do painel) e `GET /api/kanban` chamam
o mesmo `_carregar_candidatos`, que só enxerga `db.listar_conversas_abertas`
— "conversas sem `resultado`" (`db.py:1142`). Isso produz uma inconsistência
que apareceu ao vivo hoje: a conversa #1149 (Colher de Patas) chegou a `PX`
**automaticamente** (fato `recusa_explicita`, `resultado` continua `NULL`) e
segue visível nas duas abas; a conversa #1151 (Rações Mezenga), fechada com
`camucrm marcar perdido` (marco manual, que grava `resultado='perdido'`),
sumiu inteiramente da aba Conversas.

Do ponto de vista de quem opera o painel, as duas conversas "acabaram" do
mesmo jeito — recusa explícita captada pela extração ou recusa dita
diretamente pro operador e registrada à mão são o mesmo evento de negócio,
só com proveniência diferente. A diferença de visibilidade hoje é acidente
de implementação (o filtro é sobre a coluna `resultado`, não sobre "essa
conversa ainda importa pra tela"), não uma decisão de produto — nada em
`docs/04-crm-conversas-definicoes.md` pede que uma conversa suma da aba de
consulta ao ser fechada manualmente.

## What Changes

- `camucrm/db.py`: nova função `listar_conversas_fechadas` (ou parâmetro em
  `listar_conversas_abertas`) que traz conversas com `resultado IS NOT
  NULL`, respeitando o mesmo filtro de `apenas_teste` já usado hoje. Usada
  **só** pela rota `/api/conversas` — `/api/kanban` e a fila do dia
  continuam batendo exclusivamente em `listar_conversas_abertas`.
- `camucrm/painel/api.py::listar_conversas`: passa a combinar abertas +
  fechadas na resposta (mantendo `total` real, ordenação e paginação já
  existentes).
- `camucrm/painel/views.py::card_conversa`: expõe `resultado` no card
  (`None` para conversa aberta, `"ganho"`/`"perdido"` para fechada
  manualmente) — hoje o campo nem chega ao JSON.
- `camucrm/painel/static/app.js` + `app.css`: indicador visual (badge)
  distinto para card com `resultado` preenchido — precisa ser visualmente
  diferente do terminal automático (SX/PX sem `resultado`), porque são
  eventos com proveniência diferente e o operador precisa saber qual é
  qual.

## Impact

- Specs afetadas: `marco-manual-visivel-na-aba-conversas` (nova)
- Código alterado: `camucrm/db.py` (`listar_conversas_fechadas` ou
  equivalente), `camucrm/painel/api.py` (`listar_conversas`),
  `camucrm/painel/views.py` (`card_conversa`), `camucrm/painel/static/*`
  (badge de encerrada manualmente)
- Testes alterados: `tests/test_painel_api.py` (conversa com marco
  ganho/perdido continua em `GET /api/conversas`, some de `GET
  /api/kanban`), `tests/test_painel_views.py` (`card_conversa` expõe
  `resultado`)
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Kanban não ganha conversas fechadas manualmente.** As colunas do
  kanban são organizadas por `estagio`, e uma conversa fechada por marco
  manual não necessariamente tem `estagio` terminal (`camucrm marcar
  perdido` grava só `resultado`, não move `estagio` — ver #1151, que ficou
  em `P1` com `resultado='perdido'`). Misturar `resultado` nas colunas de
  `estagio` é mudança de modelo maior, não pedida aqui.
- **Fila do dia continua sem conversas fechadas.** Fila é follow-up
  acionável; não faz sentido sugerir contato numa conversa já encerrada.
- **Nenhum fluxo novo de reabertura.** Reverter um marco continua sendo só
  via `camucrm corrigir`/`desconsiderar-recusa` já existentes — este change
  não adiciona um botão "reabrir" no painel.
