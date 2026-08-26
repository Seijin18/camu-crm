# Tasks — ações humanas compartilhadas entre CLI e painel

## 1. Implementação

- [ ] 1.1 `camucrm/acoes.py`: função de marcar marco manual (marco →
      resultado → recalcular) extraída de `cli.cmd_marcar` (`cli.py:203`)
      (→ Requirement "Ação humana compartilhada entre CLI e painel").
- [ ] 1.2 `camucrm/acoes.py`: função de mudar tipo/funil (set_tipo +
      set_funil + registrar_correcao + mudar_funil + evento) extraída de
      `cli.cmd_tipo` (`cli.py:219`) (→ Requirement "Correção é sempre
      gravada").
- [ ] 1.3 `camucrm/cli.py`: `cmd_marcar` e `cmd_tipo` passam a chamar
      `acoes.py`; comportamento observável idêntico.
- [ ] 1.4 `camucrm/painel/views.py`: `marco_permitido(funil, marco)` pura —
      recusa `consignacao_assinada` em B2C e o equivalente B2B/B2C inverso;
      usado por `acoes.py` (e portanto pela CLI também, corrigindo o marco
      órfão hoje aceito) (→ Requirement "Marco incompatível com o funil é
      recusado").
- [ ] 1.5 `camucrm/painel/api.py`: `POST /api/marcos` (drop em coluna
      terminal → `acoes.py` → `marcos_manuais`); `POST /api/correcoes`
      (mudança de funil → `acoes.py` → `correcoes`); ambos recusam com HTTP
      422 e `{"erro": "...", "regra": "§N"}` quando a coluna é derivada
      (→ Requirement "Coluna derivada recusa drop com 422").
- [ ] 1.6 `camucrm/painel/static/app.js`: drag-and-drop no kanban; desabilita
      visualmente colunas com `aceita_drop:false` (vindo de `painel-leitura`),
      mas o clique/drop que escapar disso ainda bate no 422 do servidor.

## 2. Testes

- [ ] 2.1 `tests/test_acoes.py`: correção sempre gravada (§7, todo drop
      entre funis grava `correcoes` mesmo sem UI); conversa B2C recusa
      `consignacao_assinada` (→ Requirement "Correção é sempre gravada";
      → Requirement "Marco incompatível com o funil é recusado").
- [ ] 2.2 Testes de CLI existentes para `cmd_marcar`/`cmd_tipo` continuam
      verdes após a refatoração (comportamento inalterado, implementação
      movida) (→ Requirement "Ação humana compartilhada entre CLI e
      painel").
- [ ] 2.3 `tests/test_painel_api.py` (estendido): `POST /api/marcos` em
      coluna derivada devolve 422 com `regra` citando §3 (→ Requirement
      "Coluna derivada recusa drop com 422").
- [ ] 2.4 Suíte completa verde.
