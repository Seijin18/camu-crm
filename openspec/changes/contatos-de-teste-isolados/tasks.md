# Tasks — contatos de teste isolados

## 1. Implementação — schema e marcação

- [x] 1.1 `camucrm/db.py`: coluna `contatos.e_teste BOOLEAN NOT NULL DEFAULT
      FALSE` em `SCHEMA` (→ Requirement "Marca de teste é por contato").
- [x] 1.2 `camucrm/db.py::marcar_contato_teste(contato_id, e_teste, *, por)`
      — grava também em `correcoes` (→ Requirement "Marcação de teste é
      sempre manual e registrada").
- [x] 1.3 `camucrm/cli.py`: comando novo `camucrm marcar-teste <contato_id>
      [--desfazer]` (→ Requirement "Marcação de teste é sempre manual e
      registrada").

## 2. Implementação — filtro por padrão nas leituras agregadas

- [x] 2.1 `camucrm/db.py::listar_conversas_abertas`: parâmetro de modo
      (`incluir_teste`/`apenas_teste`, nunca os dois juntos), exclui teste
      por padrão (→ Requirement "Leitura agregada exclui teste por padrão").
- [x] 2.2 Rota `GET /api/conversas`: propaga o parâmetro de modo (→
      Requirement "Leitura agregada exclui teste por padrão").
- [x] 2.3 `camucrm/metrics.py::conversao` / `metricas_chave` (§14): parâmetro
      de modo, exclui teste por padrão (→ Requirement "Leitura agregada
      exclui teste por padrão").
- [x] 2.4 `camucrm/metrics.py::tempo_por_estagio`: parâmetro de modo (→
      Requirement "Leitura agregada exclui teste por padrão").
- [x] 2.5 `camucrm/metrics.py::saude_taxonomia` / `distribuicao_objecoes` /
      `objecao_por_estagio`: parâmetro de modo (→ Requirement "Leitura
      agregada exclui teste por padrão").
- [x] 2.6 `camucrm/metrics.py::padrao_correcoes`: parâmetro de modo (→
      Requirement "Leitura agregada exclui teste por padrão").
- [x] 2.7 `camucrm/metrics.py::retorno_por_followup`: parâmetro de modo (→
      Requirement "Leitura agregada exclui teste por padrão").
- [x] 2.8 `camucrm/metrics.py::onde_morrem`: parâmetro de modo (→
      Requirement "Leitura agregada exclui teste por padrão").
- [x] 2.9 `camucrm/metrics.py::ab_rascunhos`: parâmetro de modo (→
      Requirement "Leitura agregada exclui teste por padrão").
- [x] 2.10 `GET /api/o-que-funciona`: propaga o parâmetro de modo a todas as
      funções agregadas acima; conferir que nenhum bloco da tela está
      hardcoded fora desse conjunto (→ Requirement "Leitura agregada exclui
      teste por padrão").

## 3. Implementação — CLI e painel

- [x] 3.1 `camucrm/cli.py::cmd_fila`: `--incluir-teste`/`--somente-teste` (→
      Requirement "Leitura agregada exclui teste por padrão").
- [x] 3.2 `camucrm/painel/static/*`: toggle "Modo teste" no topo do painel,
      ao lado do token/operador (→ Requirement "Modo teste nunca mistura as
      duas visões na mesma tela").
- [x] 3.3 `camucrm/painel/static/*`: botão no detalhe da conversa
      ("marcar/desmarcar contato de teste") (→ Requirement "Marcação de
      teste é sempre manual e registrada").
- [x] 3.4 `camucrm/painel/api.py`: propagar o parâmetro de modo (toggle) em
      toda rota de leitura do painel — kanban, fila, conversas, métricas,
      "o que funciona" (→ Requirement "Modo teste nunca mistura as duas
      visões na mesma tela").

## 4. Testes

- [x] 4.1 `tests/test_contatos_teste.py`: para cada função da seção 2, com
      `FakeDatabase` tendo um contato normal e um de teste — modo padrão
      exclui o de teste; modo teste mostra só o de teste; nunca os dois
      juntos (→ Requirement "Leitura agregada exclui teste por padrão").
- [x] 4.2 `tests/test_contatos_teste.py`: extração/regras/rascunho/resumo
      continuam rodando normalmente para uma conversa de contato de teste —
      a flag não desliga processamento (→ Requirement "Marcação de teste não
      afeta processamento").
- [x] 4.3 `tests/test_contatos_teste.py`: `marcar_contato_teste` grava linha
      em `correcoes`, não em `marcos_manuais` (→ Requirement "Marcação de
      teste é sempre manual e registrada").
- [x] 4.4 Suíte completa verde (`make test`).
