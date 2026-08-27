# Tasks — conversa fechada manualmente continua na aba Conversas

## 1. Implementação — dados

- [x] 1.1 `camucrm/db.py`: nova função `listar_conversas_fechadas` (mesma
      assinatura de filtro de `apenas_teste` que `listar_conversas_abertas`
      já usa) trazendo conversas com `resultado IS NOT NULL` (→ Requirement
      "Conversa fechada por marco manual continua na aba Conversas").
- [x] 1.2 `camucrm/painel/api.py::listar_conversas`: combina candidatos
      abertos + fechados só nesta rota (via `_carregar_fechadas`, nova);
      `/api/kanban` e a fila continuam chamando exclusivamente
      `_carregar_candidatos`/`listar_conversas_abertas` (→ Requirement
      "Kanban e fila continuam mostrando só conversas abertas").
- [x] 1.3 `camucrm/painel/views.py::card_conversa`: expõe `resultado` no
      card (`None` quando aberta, valor gravado quando fechada) (→
      Requirement "Conversa fechada por marco manual continua na aba
      Conversas").

## 2. Implementação — UI

- [x] 2.1 `camucrm/painel/static/app.js`: nova função `tagResultado`
      renderiza um indicador (badge) distinto para card com `resultado`
      preenchido, na aba Conversas e no detalhe da conversa (→ Requirement
      "Conversa fechada por marco manual continua na aba Conversas").
- [x] 2.2 `camucrm/painel/static/app.css`: `--ganho`/`--perdido` e
      `.tag.ganho`/`.tag.perdido` — visualmente diferentes de
      `.tag.encerrado` (estágio terminal automático, SX/PX sem `resultado`),
      para o operador distinguir proveniência (→ Requirement "Conversa
      fechada por marco manual continua na aba Conversas").

## 3. Testes

- [x] 3.1 `tests/test_painel_api.py::test_conversas_inclui_fechada_por_
      marco_manual_com_indicador`: conversa marcada `perdido` continua
      aparecendo em `GET /api/conversas`, com `resultado` no card, ao lado
      de uma conversa aberta com `resultado: null` (→ Requirement "Conversa
      fechada por marco manual continua na aba Conversas").
- [x] 3.2 `tests/test_painel_api.py::test_conversa_fechada_por_marco_
      manual_some_do_kanban`: a mesma conversa fechada não aparece em `GET
      /api/kanban` (→ Requirement "Kanban e fila continuam mostrando só
      conversas abertas").
- [x] 3.3 `tests/test_painel_api.py::test_conversa_fechada_por_marco_
      manual_some_da_fila`: conversa fechada manualmente não entra na fila
      do dia (→ Requirement "Kanban e fila continuam mostrando só
      conversas abertas"). Divergência de arquivo (já prevista no
      proposal.md): não existe `tests/test_fila.py` no repo — a rota
      `/api/fila` já tem cobertura própria em `tests/test_painel_api.py`,
      e é lá que este teste entrou, para não criar um segundo arquivo
      cobrindo a mesma rota.
- [x] 3.4 `tests/test_painel_views.py`: `card_conversa` expõe `resultado`
      corretamente para conversa aberta (`None`) e fechada (valor gravado)
      (→ Requirement "Conversa fechada por marco manual continua na aba
      Conversas").
- [x] 3.5 `tests/fakes.py::FakeDatabase`: `listar_conversas_fechadas` nova,
      espelhando `listar_conversas_abertas` com o filtro invertido — sem
      isso os testes de 3.1–3.3 não teriam como exercitar a rota via
      `FakeDatabase` (não estava no proposal.md original, mas é pré-
      requisito direto de 1.1).
- [x] 3.6 Suíte completa (`make test` / `python -m unittest discover -s
      tests -p 'test_*.py'`): 630 testes, sem regressão.

## 4. Sincronização

- [x] 4.1 Implementação bateu com o `proposal.md`, com duas divergências
      pequenas já documentadas nos itens 3.3 e 3.5 acima — ambas de
      localização de teste/fake, não de comportamento. Nenhum ajuste no
      proposal foi necessário além desta nota.
- [x] 4.2 Verificado ao vivo contra o painel real (`./start.sh`), não só
      pela suíte: `GET /api/conversas` volta a incluir a conversa #1151
      (Rações Mezenga, fechada com `camucrm marcar perdido`) com
      `"resultado": "perdido"`; `GET /api/kanban` continua sem ela.
