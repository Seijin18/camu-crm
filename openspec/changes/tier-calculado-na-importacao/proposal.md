# Tier calculado na importação, não copiado da planilha

## Why

Hoje `prospeccoes.tier_origem` é um valor que chega pronto na planilha do
usuário (change `prospeccao-b2b-shortlist`) — o sistema só guarda o que a
coluna `tier_origem` do CSV já trouxer, sem critério próprio. Isso faz o
tier depender inteiramente de como a planilha foi montada externamente:
inconsistente entre remessas, e sem forma de recalcular se o critério mudar.

O usuário pediu um cálculo determinístico a partir de dois sinais que já
existem em toda linha (`nota`, `avaliacoes`), mais um sinal adicional por
regex sobre o nome — lojas que vendem só ração têm menor prioridade como
lead (o produto da Camu é personalização com foto do pet, não ração; uma
loja assim tende a ser um contato de menor retorno para esta abordagem
comercial).

## What Changes

- `camucrm/prospeccao.py` (módulo puro, sem SQL/I/O, mesmo padrão de
  `nome_curto`/`normalizar_telefone_br`) ganha:
  - `calcular_tier(nota, avaliacoes) -> "A" | "B" | "C"`: `A` exige
    `nota >= 4.5` **e** `avaliacoes >= 100`; `B` exige `nota >= 4.0` **e**
    `avaliacoes >= 30`; qualquer outra combinação (inclusive `nota`/
    `avaliacoes` ausentes) cai em `C`. Critério é **E**, não **OU** — nota
    alta com poucas avaliações não é sinal forte o bastante sozinho, e
    vice-versa.
  - `eh_provavel_loja_de_racao(nome) -> bool`: regex case-insensitive sobre
    o nome (`ração`/`racao`, com e sem cedilha/acento, como palavra ou
    prefixo — cobre "Ração e Cia", "Racao Center", "Casa da Ração") — sinal
    isolado, **não** entra no cálculo do tier (decisão do usuário: só uma
    flag ao lado, quem lê decide o que fazer com ela — não há informação
    hoje para saber se o rebaixamento automático seria correto em todos os
    casos, ex. petshop grande que também vende ração no nome).
- `db.importar_prospeccoes` **para de ler `tier_origem` do CSV** e passa a
  chamar `calcular_tier(nota, avaliacoes)` para toda linha — divergência
  explícita do comportamento anterior (coluna `tier_origem` do arquivo
  passa a ser ignorada; ver nota no código). Nome da coluna do banco
  continua `tier_origem` (evita migração/rename e não quebra filtro
  existente `GET /api/prospeccao?tier=`), mas o significado muda: deixa de
  ser "o que a planilha disse" e passa a ser "o que o sistema calculou".
  Também grava `provavel_loja_racao` (coluna nova, `BOOLEAN`).
- `Database.listar_prospeccoes`/`ProspeccaoRegistro`/`views.
  prospeccao_para_json` expõem `provavel_loja_racao` (mesmo padrão de
  `tier_origem`/`status_origem`) — só leitura, nenhum filtro novo no
  painel por enquanto (fora de escopo, ver abaixo).

## Impact

- Specs afetadas: `tier-calculado-na-importacao` (nova), estende
  `prospeccao-b2b-shortlist` (a linha de `prospeccoes` ganha um campo, o
  requirement de importação existente não muda de forma, só a origem do
  valor de `tier_origem`)
- Código alterado: `camucrm/prospeccao.py`, `camucrm/db.py`
  (`importar_prospeccoes`, `listar_prospeccoes`, schema, `ProspeccaoRegistro`),
  `camucrm/painel/views.py` (`prospeccao_para_json`)
- Testes: `tests/test_prospeccao.py` (funções puras novas + fixture de
  importação atualizada), `tests/test_painel_api.py`/`tests/
  test_painel_views.py` (campo novo no JSON)
- Bloqueado por: nenhum (estende `prospeccao-b2b-shortlist`, já implementado)
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Filtro por `provavel_loja_racao` no painel** — a flag é exposta na
  leitura, mas `GET /api/prospeccao` não ganha um parâmetro novo de filtro
  nesta mudança.
- **Rebaixar tier automaticamente por regex de nome** — decisão explícita
  do usuário: a flag fica separada, não interfere no cálculo de `A`/`B`/`C`.
- ~~**Recalcular linhas já importadas antes desta mudança**~~ — decisão
  revertida depois de implementado: em produção, as 497 linhas já
  existentes tinham `tier_origem=NULL` (a coluna nunca tinha sido
  preenchida por nenhuma importação anterior a este change) e ficaram
  mostrando "tier ?" no painel indefinidamente, porque reimportar a
  planilha inteira não é algo que acontece automaticamente. Adicionado
  `Database.recalcular_tiers_prospeccoes()` (idempotente, só regrava quem
  muda) + CLI `camucrm recalcular-tiers-prospeccao`, rodado uma vez contra
  produção (497 corrigidas). Ver tasks.md item 6.
