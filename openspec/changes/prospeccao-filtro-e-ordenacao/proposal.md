# Filtro e ordenação por relevância na aba Prospecção

## Why

`Database.listar_prospeccoes` (`db.py:3202`) já filtra por `zona`,
`bairro`, `nota_minima` e `tier`, mas a ordem de saída é fixa —
`ORDER BY p.nome` — sem parâmetro. Pedido do usuário: encontrar rápido as
lojas de um tier específico ou "mais relevantes" primeiro, sem precisar
ler a lista inteira em ordem alfabética. `tier_origem` já é calculado
deterministicamente (change `tier-calculado-na-importacao`, A/B/C por nota
+ avaliações), então "relevância" tem um critério pronto para compor —
falta só expor a ordenação.

A aba Conversas já tem esse padrão (`ordenar` com
`horas_esperando`/`nome`/`estagio`/`temperatura`, `app.js:358-363`) —
este change estende o mesmo padrão de UI para Prospecção, reaproveitando o
componente em vez de inventar um novo.

## What Changes

- `camucrm/prospeccao.py`: `ORDENS_PROSPECCAO` — mapa de chave pública para
  a expressão `ORDER BY`, mesmo padrão de constante fechada que
  `taxonomia.py` usa para listas fechadas do domínio:
  - `relevancia` (padrão quando o parâmetro é omitido? — não, ver "Fora de
    escopo": o padrão continua `nome`, sem mudar comportamento existente):
    `tier_origem ASC, nota DESC NULLS LAST, avaliacoes DESC NULLS LAST` —
    tier `A` antes de `B` antes de `C` (ordem alfabética do enum já serve,
    A<B<C), desempatando por nota e depois por volume de avaliações.
  - `nota`: `nota DESC NULLS LAST`
  - `avaliacoes`: `avaliacoes DESC NULLS LAST`
  - `nome` (padrão, comportamento atual, inalterado): `nome ASC`
- `Database.listar_prospeccoes(..., ordenar: str = "nome")`: troca o
  `ORDER BY p.nome` fixo por `ORDENS_PROSPECCAO[ordenar]` — valor fora do
  mapa cai em `nome` (mesma defesa que `rules/fila.py` já usa para entrada
  de UI não confiável).
- `GET /api/prospeccao` (`painel/api.py:809`) ganha `ordenar: str = "nome"`,
  repassado direto.
- Painel (`app.js::renderizarProspeccao`): `<select id="ordenar-
  prospeccao">` com as quatro opções acima, ao lado dos filtros existentes
  (zona/bairro/nota mínima/tier/só não convertidas) — dispara `carregar()`
  no `change`, mesmo padrão dos outros filtros da mesma tela.
- Reaproveita o mecanismo de persistência de filtro do change
  `painel-preserva-estado-em-refresh` (se já implementado) para o novo
  `ordenar` não se perder no refresh automático — se este change entrar
  primeiro, o `<select>` nasce com o comportamento atual de filtro (reseta
  em refresh, como os demais), sem regressão nova.

## Impact

- Specs afetadas: `prospeccao-filtro-e-ordenacao` (nova)
- Código: `camucrm/prospeccao.py`, `camucrm/db.py`
  (`listar_prospeccoes`), `camucrm/painel/api.py`
  (`listar_prospeccao`), `camucrm/painel/static/app.js`
- Testes: `tests/test_prospeccao.py` (mapa `ORDENS_PROSPECCAO`),
  `tests/test_painel_api.py` (`ordenar` na rota, valor inválido cai em
  `nome`), `tests/integration` não é necessário — nenhuma constraint de
  banco envolvida
- Bloqueado por: nenhum (independente de
  `painel-preserva-estado-em-refresh`, mas os dois se complementam — ver
  nota acima)

## Fora de escopo

- **Ordenar/filtrar fila e kanban.** A fila do dia é a priorização de
  negócio da §6 do documento de definições — ela já é uma ordenação
  (`rules/fila.py::montar_fila`), e deixar o operador reordenar
  manualmente contradiz o propósito da regra (§6: "priorizar a fila:
  regra determinística, política de negócio, não inferência" — §1). Kanban
  é agrupado por coluna de estágio; "ordenar" dentro de uma coluna não foi
  pedido e não tem sinal óbvio de utilidade sem dado de uso real. Se
  aparecer necessidade concreta depois, vira change próprio, não algo
  emendado aqui.
- **Trocar o padrão de `nome` para `relevancia`.** Muda comportamento
  visível de uma rota já em uso sem pedido explícito para isso — o operador
  escolhe `relevancia` quando quiser, o padrão não muda sozinho.
- **Ordenação combinável (multi-campo escolhido pelo operador).** Só as
  quatro opções fechadas acima; nada de "ordenar por A, depois por B"
  configurável — mesmo espírito de "campos fechados" que a extração já
  segue (§2 do documento de definições, por analogia).
