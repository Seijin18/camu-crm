# Tasks — mensagens recentes e ações seguras no painel

## 1. Implementação — mensagens recentes e paginação

- [ ] 1.1 `camucrm/db.py::listar_mensagens_registradas`: comportamento
      padrão (sem `desde_id`) passa a trazer as mensagens MAIS RECENTES (→
      Requirement "Mensagens recentes aparecem por padrão").
- [ ] 1.2 `camucrm/db.py::listar_mensagens_registradas`: cursor real
      ("antes de X") para paginar para trás, e `total` no payload (→
      Requirement "Mensagens recentes aparecem por padrão").
- [ ] 1.3 `camucrm/painel/api.py` + `static/*`: indicar na tela quando há
      mais mensagens que as exibidas (→ Requirement "Mensagens recentes
      aparecem por padrão").

## 2. Implementação — contagem real de kanban e fila

- [ ] 2.1 `camucrm/db.py::listar_conversas_abertas`: expor `total` real de
      conversas abertas, mesmo cortado pelo `limite` (→ Requirement "Kanban
      e fila expõem contagem real").
- [ ] 2.2 Inverter a prioridade de corte quando necessário — manter visíveis
      as conversas mais NEGLIGENCIADAS, cortar as mais recentes/já sendo
      atendidas (→ Requirement "Corte prioriza conversas negligenciadas").
- [ ] 2.3 Rotas de kanban e fila no painel: propagar `total` na resposta (→
      Requirement "Kanban e fila expõem contagem real").

## 3. Implementação — concorrência e persistência de ações

- [ ] 3.1 `camucrm/acoes.py::marcar_marco`/`mudar_funil_conversa`: trava
      contra escrita concorrente (`SELECT ... FOR UPDATE` na linha de
      `conversas` dentro da mesma transação, ou coluna de versão conferida
      no `UPDATE`) (→ Requirement "Ações concorrentes no mesmo card não
      corrompem marcos_manuais").
- [ ] 3.2 `camucrm/acoes.py::mudar_funil_conversa`: persistir `temperatura`
      junto com `estagio`, reusando `recalcular(persistir=True)` (→
      Requirement "mudar_funil_conversa persiste temperatura").
- [ ] 3.3 `camucrm/db.py::vincular_rascunho`: adicionar `WHERE mensagem_id
      IS NULL` na cláusula do `UPDATE` (→ Requirement "Vínculo de rascunho
      não é sobrescrito por corrida").

## 4. Testes

- [ ] 4.1 `tests/test_painel_leitura.py`: mensagens recentes aparecem por
      padrão em conversa com >200 mensagens (→ Requirement "Mensagens
      recentes aparecem por padrão").
- [ ] 4.2 `tests/test_painel_leitura.py`: kanban/fila expõem contagem real
      mesmo cortando pelo limite (→ Requirement "Kanban e fila expõem
      contagem real").
- [ ] 4.3 `tests/test_acoes.py`: duas ações concorrentes no mesmo card não
      produzem `marcos_manuais` contraditório — uma é recusada ou
      serializada (→ Requirement "Ações concorrentes no mesmo card não
      corrompem marcos_manuais").
- [ ] 4.4 `tests/test_acoes.py`: `mudar_funil_conversa` persiste temperatura
      junto com estágio (→ Requirement "mudar_funil_conversa persiste
      temperatura").
- [ ] 4.5 `tests/test_rascunhos_registro.py`: corrida entre duas
      reconciliações não sobrescreve um `mensagem_id` já vinculado (→
      Requirement "Vínculo de rascunho não é sobrescrito por corrida").
- [ ] 4.6 `tests/integration/`: trava de concorrência (`SELECT ... FOR
      UPDATE`) verificada contra Postgres real (→ Requirement "Ações
      concorrentes no mesmo card não corrompem marcos_manuais").
- [ ] 4.7 Suíte completa verde (unitária sem Postgres; integração à parte
      com Postgres).
