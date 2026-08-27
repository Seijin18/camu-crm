# Tasks — ações humanas compartilhadas entre CLI e painel

## 1. Implementação

- [x] 1.1 `camucrm/acoes.py`: função de marcar marco manual (marco →
      resultado → recalcular) extraída de `cli.cmd_marcar` (`cli.py:203`)
      (→ Requirement "Ação humana compartilhada entre CLI e painel").
- [x] 1.2 `camucrm/acoes.py`: função de mudar tipo/funil (set_tipo +
      set_funil + registrar_correcao + mudar_funil + evento) extraída de
      `cli.cmd_tipo` (`cli.py:219`) (→ Requirement "Correção é sempre
      gravada").
- [x] 1.3 `camucrm/cli.py`: `cmd_marcar` e `cmd_tipo` passam a chamar
      `acoes.py`; comportamento observável idêntico.
- [x] 1.4 **DIVERGÊNCIA (instrução explícita do operador, sobrepondo este
      arquivo):** `marco_permitido(funil, marco)` pura foi implementada em
      `camucrm/acoes.py`, não em `camucrm/painel/views.py`. Motivo: `views.py`
      é camada de apresentação do painel (dict de resposta JSON) e
      `marco_permitido` precisa valer também para a CLI sem o painel
      importar `views` fora do seu próprio processo — colocá-la em `acoes.py`
      (já consumido pelos dois caminhos) evita esse acoplamento. Recusa
      `consignacao_assinada`/`primeira_reposicao` fora do B2B; `ganho`/
      `perdido` valem nos dois funis (→ Requirement "Marco incompatível com
      o funil é recusado").
- [x] 1.5 `camucrm/painel/api.py`: **rotas efetivamente criadas** —
      `POST /api/conversas/{id}/marcos` (marco → `acoes.marcar_marco` →
      `marcos_manuais`), `POST /api/conversas/{id}/funil` (mudança de funil →
      `acoes.mudar_funil_conversa` → `correcoes` + reclassificação de
      estágio) e `POST /api/conversas/{id}/correcoes` (correção avulsa →
      `db.registrar_correcao`). Divergência de forma em relação ao texto
      original desta tarefa (que descrevia `/api/marcos` e `/api/correcoes`
      "achatados", com a correção acoplada à troca de funil): a instrução
      explícita do operador pediu as três rotas aninhadas em
      `/conversas/{id}/...`, e uma rota `/correcoes` própria e genérica —
      mais consistente com o padrão já usado por `GET /api/conversas/{id}`
      e mais correto: nem toda correção humana é mudança de funil. Todas
      recusam com HTTP 422 e `{"erro": "...", "regra": "§N"}` quando a ação é
      inválida (→ Requirement "Coluna derivada recusa drop com 422").
- [x] 1.6 `camucrm/painel/static/app.js`: drag-and-drop no kanban via
      `draggable`/`dragstart`/`dragover`/`drop`; colunas com
      `aceita_drop:false` (vindo de `painel-leitura`) pintam o alvo como
      inválido (`.alvo-invalido`, `app.css`) e recusam sem chamar a API;
      drop em coluna de marco chama `POST .../marcos`; drop no kanban do
      outro funil chama `POST .../funil`; erro do servidor (422) aparece
      como banner no topo do kanban (`mostrarErroKanban`), nunca
      silenciosamente ignorado.

## 2. Testes

- [x] 2.1 `tests/test_acoes.py`: correção sempre gravada (§7, todo drop
      entre funis grava `correcoes` mesmo sem UI); conversa B2C recusa
      `consignacao_assinada` (→ Requirement "Correção é sempre gravada";
      → Requirement "Marco incompatível com o funil é recusado"). Também
      cobre `marco_permitido` isoladamente para todas as combinações
      funil×marco.
- [x] 2.2 Comportamento de `cmd_marcar`/`cmd_tipo` continua idêntico após a
      refatoração — não havia teste de CLI dedicado antes deste change;
      `tests/test_acoes.py` cobre a mesma sequência de efeitos que a CLI
      agora delega a `acoes.py`, e `make test` roda a suíte inteira
      (→ Requirement "Ação humana compartilhada entre CLI e painel").
- [x] 2.3 `tests/test_painel_api.py` (estendido, classe `TesteRotasDeAcao`):
      `POST /api/conversas/{id}/marcos` com marco incompatível com o funil
      devolve 422 com `regra: "§3"`; casos de sucesso e de conversa/funil
      inexistente também cobertos para as três rotas novas (→ Requirement
      "Coluna derivada recusa drop com 422").
- [x] 2.4 Suíte completa verde: `make test` → 283 testes, OK (13 skipped,
      pré-existentes — dependem de Postgres/rede fora do escopo unitário).
