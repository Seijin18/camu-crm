# Mensagens recentes e ações seguras no painel

## Why

Auditoria completa do painel (`camucrm/painel/`, `acoes.py`) confirmou por
leitura direta quatro problemas que fazem a tela mentir sobre o estado real
do banco ou permitir corrupção silenciosa por concorrência:

1. **`GET /conversas/{id}/mensagens` mostra as mensagens MAIS ANTIGAS**, não
   as recentes, para qualquer conversa com mais de 200 mensagens (`ORDER BY
   id` com `desde_id or 0`, sem paginação real nem indicação de
   truncamento). Confirmado por leitura direta. O operador pode achar que
   está vendo o estado atual da conversa e não está.
2. **Kanban e fila cortam em `limite=200`, ordenados por `atualizado_em
   DESC`** — cortam PRIMEIRO exatamente as conversas mais negligenciadas
   (que nunca tiveram `atualizado_em` tocado pelo painel, só por mensagem
   real ou ação humana), sem expor `total` em lugar nenhum da tela. O
   operador não sabe que existem mais conversas do que as exibidas.
3. **`acoes.marcar_marco`/`mudar_funil_conversa` sem trava contra ação
   concorrente no mesmo card** — duas requisições quase simultâneas (duas
   abas/pessoas) podem gravar `marcos_manuais` contraditório (a mesma
   conversa "ganho" E "perdido"), permanente e sem detecção.
4. **`db.py::vincular_rascunho` sem `WHERE mensagem_id IS NULL`** — corrida
   entre duas reconciliações pode sobrescrever um vínculo já feito,
   silenciosamente (afeta a métrica agregada de A/B de rascunho, não a
   conversa ao vivo).

Um problema correlato, menor: `mudar_funil_conversa` nunca persiste
`temperatura` e só persiste `estagio` condicionalmente — a coluna cache fica
desalinhada até a próxima mensagem chegar.

## What Changes

- `db.py::listar_mensagens_registradas`: mudar o comportamento padrão (sem
  `desde_id`) para trazer as mensagens MAIS RECENTES, com paginação real
  (cursor "antes de X") e `total` no payload.
- `GET .../mensagens` e a tela correspondente em `app.js`: indicar quando há
  mais mensagens que as exibidas (contagem/indicador de truncamento).
- `db.py::listar_conversas_abertas` / rotas de kanban e fila: expor `total`
  real de conversas abertas, mesmo quando cortado pelo `limite`; inverter a
  prioridade de corte quando for preciso cortar — manter visíveis as mais
  NEGLIGENCIADAS, cortar as mais recentes/já sendo atendidas.
- `acoes.marcar_marco`/`mudar_funil_conversa`: trava contra escrita
  concorrente na mesma conversa — `SELECT ... FOR UPDATE` na linha de
  `conversas` dentro da mesma transação de leitura+validação+escrita (ou
  coluna de versão conferida no `UPDATE`).
- `acoes.mudar_funil_conversa`: persistir `temperatura` junto com `estagio`
  — reusar o mesmo `recalcular(persistir=True)` que `marcar_marco` já usa,
  em vez de um `UPDATE` parcial manual.
- `db.py::vincular_rascunho`: adicionar `WHERE mensagem_id IS NULL` na
  cláusula do `UPDATE`, para uma corrida não sobrescrever um vínculo já
  feito.

## Impact

- Specs afetadas: `painel-mensagens-recentes-e-acoes-seguras` (nova)
- Código alterado: `camucrm/db.py` (`listar_mensagens_registradas`,
  `listar_conversas_abertas`, `vincular_rascunho`), `camucrm/acoes.py`
  (`marcar_marco`, `mudar_funil_conversa`), `camucrm/painel/api.py`
  (rotas de mensagens/kanban/fila com `total`), `camucrm/painel/views.py`,
  `camucrm/painel/static/*` (indicador de truncamento, contagem real)
- Testes alterados: `tests/test_painel_leitura.py` (ou equivalente já
  existente — mensagens recentes por padrão em conversa com >200
  mensagens; kanban/fila expõem contagem real), `tests/test_acoes.py`
  (duas ações concorrentes no mesmo card não produzem `marcos_manuais`
  contraditório; `mudar_funil_conversa` persiste temperatura),
  `tests/test_rascunhos_registro.py` (vincular_rascunho não sobrescreve
  vínculo já feito), `tests/integration/` (trava de concorrência contra
  Postgres real)
- Bloqueado por: nenhum (não é bloqueio técnico, mas `contatos-de-teste-
  isolados` faz sentido entrar antes — os dois tocam as mesmas rotas de
  leitura do painel, evitando retrabalho)
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- Paginação real de kanban/fila além de expor `total` — se o volume real
  exigir paginação completa da lista (não só da lista de mensagens de uma
  conversa), fica registrado como observação futura, não item deste change.
- SSE: catch-up de reconexão limitado a 200 mensagens por ciclo — corner
  case de volume hoje implausível, registrado como observação de baixa
  prioridade em `openspec/project.md`, não corrigido aqui.
