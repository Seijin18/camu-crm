# Tasks

- [x] 1. `camucrm/prospeccao.py`
  - [x] 1.1 `calcular_tier(nota: float | None, avaliacoes: int | None) -> str`
        — `A` (nota>=4.5 e avaliacoes>=100), `B` (nota>=4.0 e
        avaliacoes>=30), senão `C`.
  - [x] 1.2 `eh_provavel_loja_de_racao(nome: str | None) -> bool` — regex
        case-insensitive por `ração`/`racao` (com/sem acento e cedilha).
  - [x] 1.3 Testes unitários das duas funções (limites das faixas, `None`,
        nomes com e sem acento/cedilha, falso positivo tipo "traçado").
- [x] 2. `camucrm/db.py`
  - [x] 2.1 Schema: `ALTER TABLE prospeccoes ADD COLUMN IF NOT EXISTS
        provavel_loja_racao BOOLEAN` com comentário explicando a origem
        (change `tier-calculado-na-importacao`).
  - [x] 2.2 `importar_prospeccoes`: substitui `linha.get("tier_origem")`
        pela chamada a `calcular_tier`; grava `eh_provavel_loja_de_racao`
        na coluna nova. Comentário explícito no código sobre a divergência
        (coluna do CSV `tier_origem` passa a ser ignorada).
  - [x] 2.3 `_PROSPECCAO_SELECT`, `listar_prospeccoes`, `ProspeccaoRegistro`:
        campo novo `provavel_loja_racao` (posição final, mesmo padrão das
        adições anteriores — `enviado_em` etc.).
- [x] 3. `camucrm/painel/views.py`
  - [x] 3.1 `prospeccao_para_json` expõe `provavel_loja_racao`.
- [x] 4. Testes existentes
  - [x] 4.1 `tests/test_prospeccao.py`: fixture `_linha`/testes de
        `importar_prospeccoes` param `tier_origem` do CSV passa a ser
        ignorado — atualizar asserts para o valor calculado.
  - [x] 4.2 `tests/test_painel_api.py`/`tests/test_painel_views.py`: campo
        novo no JSON de `/api/prospeccao`, se coberto por fixture completa.
- [x] 5. `make test` verde.
