# Tasks — filtro e ordenação por relevância na aba Prospecção

## 1. Implementação — dados

- [x] 1.1 `camucrm/prospeccao.py`: `ORDENS_PROSPECCAO` (dict fechado,
      `nome`/`relevancia`/`nota`/`avaliacoes` → fragmento `ORDER BY`) e
      `ordem_prospeccao_valida(ordenar) -> str` (normaliza para `nome`
      quando a chave não existe) (→ Requirement "Ordenação por relevância,
      nota ou avaliações").
- [x] 1.2 `camucrm/db.py::listar_prospeccoes`: parâmetro `ordenar: str =
      "nome"`, `ORDER BY` monta a partir de `ORDENS_PROSPECCAO` via
      `camucrm.prospeccao.ordem_prospeccao_valida` (→ Requirement
      "Ordenação por relevância, nota ou avaliações"; → Requirement "Padrão
      inalterado quando `ordenar` não é informado").

## 2. Implementação — API

- [x] 2.1 `camucrm/painel/api.py::listar_prospeccao`: parâmetro `ordenar:
      str = "nome"` repassado para `db.listar_prospeccoes` (→ Requirement
      "Ordenação por relevância, nota ou avaliações").

## 3. Implementação — UI

- [x] 3.1 `app.js::renderizarProspeccao`: `<select id="ordenar-
      prospeccao">` com as opções nome/relevância/nota/avaliações, ao lado
      dos filtros já existentes; `change` dispara `carregar()` (→
      Requirement "Ordenação por relevância, nota ou avaliações"). Feito
      depois de `painel-preserva-estado-em-refresh` ter passado por
      `renderizarProspeccao` primeiro (o `<select>` nasceu já lendo/gravando
      `estadoFiltrosProspeccao.ordenar`, sem precisar de código de
      persistência duplicado).

## 4. Testes

- [x] 4.1 `tests/test_prospeccao.py`: `ordem_prospeccao_valida` — as quatro
      chaves válidas retornam o fragmento esperado; chave desconhecida
      (incluindo string vazia e `None`) cai em `nome` (→ Requirement
      "Padrão inalterado quando `ordenar` não é informado").
- [x] 4.2 `tests/test_prospeccao.py::TesteOrdenacaoRelevancia` (não um
      arquivo `test_db_prospeccao.py` separado — não existia convenção de
      arquivo por tabela no repo; ficou junto do resto de prospecção):
      `listar_prospeccoes(ordenar="relevancia")` devolve tier A antes de B
      antes de C, desempatando por nota e depois avaliações, contra um
      fixture com tiers misturados (→ Requirement "Ordenação por
      relevância, nota ou avaliações"). Exigiu ensinar `FakeDatabase.
      listar_prospeccoes` (`tests/fakes.py`) a ordenar pelas mesmas quatro
      chaves de `ORDENS_PROSPECCAO`.
- [x] 4.3 `tests/test_painel_api.py`: `GET /api/prospeccao?ordenar=nota`
      repassa o parâmetro; `GET /api/prospeccao` sem `ordenar` mantém a
      ordem por nome de hoje, sem regressão (→ Requirement "Padrão
      inalterado quando `ordenar` não é informado").
- [x] 4.4 Suíte completa (`make test`) sem regressão — 740 testes, OK.

## 5. Sincronização

- [x] 5.1 Ao concluir, confirmar que a implementação bateu com o
      `proposal.md`; registrar aqui qualquer divergência. Sem divergência —
      implementado como proposto: dict fechado + normalização em
      `prospeccao.py`, parâmetro repassado por `db.py` e `api.py`, select
      em `app.js`, padrão `nome` inalterado quando `ordenar` está ausente.
      A única nota é organizacional, não de comportamento: os testes de
      `listar_prospeccoes(ordenar=...)` ficaram em
      `tests/test_prospeccao.py::TesteOrdenacaoRelevancia` (não um
      `test_db_prospeccao.py` à parte — arquivo que não existia e não é
      convenção deste repo).
