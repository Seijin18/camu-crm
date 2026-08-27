# Contatos de teste isolados

## Why

Testes manuais desta sessão (contatos reais do próprio time, ex. "Marcos
Shiba", "Felipe Baz Mitsuishi") já aparecem misturados no kanban e na fila
reais nos prints trocados com o usuário — vão poluir tanto a operação diária
quanto as métricas assim que o volume real de petshops e consumidores
começar. É pedido direto do usuário, não achado de auditoria: contato de
teste precisa sumir do kanban/fila/conversas/métricas por padrão, só
aparecendo quando um "modo teste" é ativado explicitamente no painel — os
dois modos nunca se misturam na mesma tela.

Marcação é sempre manual, pelo operador — mesmo princípio do §1 (nenhuma
classificação de negócio por inferência automática) estendido aqui: "teste"
também não é adivinhado.

## What Changes

- Schema: `contatos.e_teste BOOLEAN NOT NULL DEFAULT FALSE`. A marca é por
  CONTATO, não por conversa — todas as conversas passadas e futuras de um
  contato de teste ficam de teste automaticamente, sem marcar uma por uma.
- `db.py::marcar_contato_teste(contato_id, e_teste, *, por)` — grava em
  `correcoes` (§7: toda correção fica registrada), não em `marcos_manuais`
  (que é especificamente sobre S6/P5/P6/perdido, um conceito diferente de
  "teste").
- Filtro por padrão em TODA leitura agregada que hoje lê `conversas`/
  `contatos` sem filtro — mesma disciplina de "nada passa despercebido"
  desta auditoria:
  - `db.listar_conversas_abertas` (kanban, fila)
  - a consulta por trás de `GET /api/conversas`
  - `metrics.conversao` / `metrics.metricas_chave` (§14)
  - `metrics.tempo_por_estagio`
  - `metrics.saude_taxonomia` / `distribuicao_objecoes` /
    `objecao_por_estagio`
  - `metrics.padrao_correcoes`
  - `metrics.retorno_por_followup`
  - `metrics.onde_morrem`
  - `metrics.ab_rascunhos`
  - `GET /api/o-que-funciona` (agrega todas as anteriores — conferir que
    nenhuma escapa do filtro por estar hardcoded fora das funções acima)
- Cada função acima ganha um parâmetro de modo (ex. `incluir_teste: bool` ou
  `apenas_teste: bool`, nunca os dois ligados ao mesmo tempo) propagado a
  partir da rota do painel.
- **O que NÃO é afetado** — processamento continua normal, só a apresentação
  filtra: extração de fatos, regras de estágio/temperatura, geração de
  rascunho e de resumo continuam rodando normalmente para conversas de
  teste. É isso que torna o contato de teste útil para testar o pipeline de
  verdade — a flag é de visibilidade/agregação, não de processamento.
- CLI: comando novo `camucrm marcar-teste <contato_id> [--desfazer]`
  (dedicado, não reaproveita `camucrm corrigir` — "teste" não é correção de
  classificação de negócio, é uma flag operacional distinta). `camucrm fila`
  ganha `--incluir-teste`/`--somente-teste`.
- Painel: botão no detalhe da conversa ("marcar/desmarcar contato de
  teste"); toggle "Modo teste" no topo do painel (mesmo padrão de posição
  dos outros controles, ao lado do token/operador), propagado como
  parâmetro em toda rota de leitura. Ligado: todas as telas (kanban, fila,
  conversas, métricas, "o que funciona") mostram só teste. Desligado: só
  não-teste.

## Impact

- Specs afetadas: `contatos-de-teste-isolados` (nova)
- Código alterado: `camucrm/db.py` (`SCHEMA` — coluna `e_teste`,
  `marcar_contato_teste`, `listar_conversas_abertas` e demais consultas com
  parâmetro de modo), `camucrm/metrics.py` (todas as funções listadas
  acima), `camucrm/cli.py` (`marcar-teste`, `fila --incluir-teste/--somente-
  teste`), `camucrm/painel/api.py` (propagação do parâmetro de modo em
  todas as rotas de leitura), `camucrm/painel/views.py`,
  `camucrm/painel/static/*` (toggle "Modo teste", botão no detalhe)
- Testes alterados: `tests/test_contatos_teste.py` (novo — cada função da
  lista, com `FakeDatabase` tendo um contato normal e um de teste, prova
  exclusão por padrão e mostra-só-teste no modo ligado, nunca os dois
  juntos), teste específico de que extração/regras/rascunho/resumo
  continuam rodando para um contato de teste (a flag não desliga
  processamento)
- Bloqueado por: nenhum (pode entrar em paralelo com os changes de correção)
- Bloqueia: nenhum, mas faz sentido entrar antes de
  `painel-mensagens-recentes-e-acoes-seguras` — os dois tocam as mesmas
  rotas de leitura do painel, e fazer este primeiro evita retrabalho de
  tocar a mesma rota duas vezes

## Fora de escopo (decisão explícita)

- Qualquer inferência automática de "isso parece um teste" — marcação é
  sempre manual, sem exceção.
- Desligar processamento (extração/regras/rascunho/resumo) para contato de
  teste — o valor do modo teste é justamente testar o pipeline de verdade.
