# Tasks — filtro e ordenação por relevância na aba Prospecção

## 1. Implementação — dados

- [ ] 1.1 `camucrm/prospeccao.py`: `ORDENS_PROSPECCAO` (dict fechado,
      `nome`/`relevancia`/`nota`/`avaliacoes` → fragmento `ORDER BY`) e
      `ordem_prospeccao_valida(ordenar) -> str` (normaliza para `nome`
      quando a chave não existe) (→ Requirement "Ordenação por relevância,
      nota ou avaliações").
- [ ] 1.2 `camucrm/db.py::listar_prospeccoes`: parâmetro `ordenar: str =
      "nome"`, `ORDER BY` monta a partir de `ORDENS_PROSPECCAO` via
      `camucrm.prospeccao.ordem_prospeccao_valida` (→ Requirement
      "Ordenação por relevância, nota ou avaliações"; → Requirement "Padrão
      inalterado quando `ordenar` não é informado").

## 2. Implementação — API

- [ ] 2.1 `camucrm/painel/api.py::listar_prospeccao`: parâmetro `ordenar:
      str = "nome"` repassado para `db.listar_prospeccoes` (→ Requirement
      "Ordenação por relevância, nota ou avaliações").

## 3. Implementação — UI

- [ ] 3.1 `app.js::renderizarProspeccao`: `<select id="ordenar-
      prospeccao">` com as opções nome/relevância/nota/avaliações, ao lado
      dos filtros já existentes; `change` dispara `carregar()` (→
      Requirement "Ordenação por relevância, nota ou avaliações").

## 4. Testes

- [ ] 4.1 `tests/test_prospeccao.py`: `ordem_prospeccao_valida` — as quatro
      chaves válidas retornam o fragmento esperado; chave desconhecida
      (incluindo string vazia e `None`) cai em `nome` (→ Requirement
      "Padrão inalterado quando `ordenar` não é informado").
- [ ] 4.2 `tests/test_db_prospeccao.py` (ou arquivo equivalente já
      existente para prospecção): `listar_prospeccoes(ordenar="relevancia")`
      devolve tier A antes de B antes de C, desempatando por nota e depois
      avaliações, contra um fixture com tiers misturados (→ Requirement
      "Ordenação por relevância, nota ou avaliações").
- [ ] 4.3 `tests/test_painel_api.py`: `GET /api/prospeccao?ordenar=nota`
      repassa o parâmetro; `GET /api/prospeccao` sem `ordenar` mantém a
      ordem por nome de hoje, sem regressão (→ Requirement "Padrão
      inalterado quando `ordenar` não é informado").
- [ ] 4.4 Suíte completa (`make test`) sem regressão.

## 5. Sincronização

- [ ] 5.1 Ao concluir, confirmar que a implementação bateu com o
      `proposal.md`; registrar aqui qualquer divergência.
