# Tasks — mensagens recentes e ações seguras no painel

## 1. Implementação — mensagens recentes e paginação

- [x] 1.1 `camucrm/db.py::listar_mensagens_registradas`: comportamento
      padrão (sem `desde_id`) passa a trazer as mensagens MAIS RECENTES (→
      Requirement "Mensagens recentes aparecem por padrão").
- [x] 1.2 `camucrm/db.py::listar_mensagens_registradas`: cursor real
      ("antes de X") para paginar para trás, e `total` no payload (→
      Requirement "Mensagens recentes aparecem por padrão").
- [x] 1.3 `camucrm/painel/api.py` + `static/*`: indicar na tela quando há
      mais mensagens que as exibidas (→ Requirement "Mensagens recentes
      aparecem por padrão").

## 2. Implementação — contagem real de kanban e fila

- [x] 2.1 `camucrm/db.py::listar_conversas_abertas`: expor `total` real de
      conversas abertas, mesmo cortado pelo `limite` (→ Requirement "Kanban
      e fila expõem contagem real").
- [x] 2.2 Inverter a prioridade de corte quando necessário — manter visíveis
      as conversas mais NEGLIGENCIADAS, cortar as mais recentes/já sendo
      atendidas (→ Requirement "Corte prioriza conversas negligenciadas").
- [x] 2.3 Rotas de kanban e fila no painel: propagar `total` na resposta (→
      Requirement "Kanban e fila expõem contagem real").

## 3. Implementação — concorrência e persistência de ações

- [x] 3.1 `camucrm/acoes.py::marcar_marco`/`mudar_funil_conversa`: trava
      contra escrita concorrente (`SELECT ... FOR UPDATE` na linha de
      `conversas` dentro da mesma transação, ou coluna de versão conferida
      no `UPDATE`) (→ Requirement "Ações concorrentes no mesmo card não
      corrompem marcos_manuais").
- [x] 3.2 `camucrm/acoes.py::mudar_funil_conversa`: persistir `temperatura`
      junto com `estagio`, reusando `recalcular(persistir=True)` (→
      Requirement "mudar_funil_conversa persiste temperatura").
- [x] 3.3 `camucrm/db.py::vincular_rascunho`: adicionar `WHERE mensagem_id
      IS NULL` na cláusula do `UPDATE` (→ Requirement "Vínculo de rascunho
      não é sobrescrito por corrida").

## 4. Testes

- [x] 4.1 Mensagens recentes aparecem por padrão em conversa com >200
      mensagens (→ Requirement "Mensagens recentes aparecem por padrão").
      Divergência de arquivo (já prevista no proposal.md, "ou equivalente
      já existente"): não há `tests/test_painel_leitura.py` no repo — as
      rotas do painel já têm suíte própria em `tests/test_painel_api.py`,
      e é lá que este teste (e o de `antes_de`) foi adicionado, para não
      criar um segundo arquivo cobrindo a mesma rota.
- [x] 4.2 Kanban/fila expõem contagem real mesmo cortando pelo limite (→
      Requirement "Kanban e fila expõem contagem real"). Em
      `tests/test_painel_api.py` (mesma nota de 4.1).
- [x] 4.3 `tests/test_acoes.py`: duas ações concorrentes no mesmo card não
      produzem `marcos_manuais` contraditório — uma é recusada ou
      serializada (→ Requirement "Ações concorrentes no mesmo card não
      corrompem marcos_manuais"). Cobertura em duas camadas: contra
      `FakeDatabase` (determinístico, prova a recusa de `_marco_conflitante`)
      e contra Postgres real em 4.6 (prova a serialização de verdade).
- [x] 4.4 `tests/test_acoes.py`: `mudar_funil_conversa` persiste temperatura
      junto com estágio (→ Requirement "mudar_funil_conversa persiste
      temperatura").
- [x] 4.5 `tests/test_rascunhos_registro.py`: corrida entre duas
      reconciliações não sobrescreve um `mensagem_id` já vinculado (→
      Requirement "Vínculo de rascunho não é sobrescrito por corrida").
- [x] 4.6 `tests/integration/test_acoes_concorrentes_postgres.py`: trava de
      concorrência (`SELECT ... FOR UPDATE`) verificada contra Postgres
      real, para `marcar_marco` (marcos contraditórios concorrentes) e
      `mudar_funil_conversa` (N chamadas concorrentes para o mesmo
      funil-alvo gravam uma única correção) (→ Requirement "Ações
      concorrentes no mesmo card não corrompem marcos_manuais").

      Este teste, ao rodar pela primeira vez, expôs uma DIVERGÊNCIA real na
      primeira versão da implementação (corrigida antes de fechar o
      change): `mudar_funil_conversa` fazia `fatos_da_conversa`/
      `carregar_sinais`/`estagio_de_partida` (nenhuma delas aceita `conn=`)
      DENTRO do mesmo `with db.transacao()` que segurava a linha travada —
      sob concorrência real (N chamadas simultâneas na mesma conversa),
      isso pedia uma segunda conexão do pool por thread enquanto a primeira
      continuava presa esperando a trava, esgotando o pool
      (`PoolTimeout`/`DeadlockDetected` reproduzidos ao rodar este teste com
      `max_size` padrão). Mesmo problema, mais sutil, em `marcar_marco`:
      `db.marcos_da_conversa(conversa_id)` também pedia conexão nova de
      dentro da transação. Correção: `marcos_da_conversa` ganhou `conn=`
      (chamado com `conn=conn`); em `mudar_funil_conversa`, a reclassificação
      de estágio e `recalcular(persistir=True)` passaram a rodar DEPOIS que
      a transação da trava já commitou (mesmo padrão que `marcar_marco` já
      usava) — a trava protege só a parte que precisa dela (as três
      escritas), e o resto é idempotente (`gravar_evento_estagio` é
      `ON CONFLICT DO NOTHING`) e não precisa ficar preso atrás dela.
- [x] 4.7 Suíte completa verde (unitária sem Postgres; integração à parte
      com Postgres). `make test`: 582 testes, OK. `make test-db`: 56 testes,
      OK (rodado com Postgres real via `make db-up` + `make init`).
