# Delta: tier-calculado-na-importacao

## ADDED Requirements

### Requirement: Tier é calculado a partir de nota e avaliações, não copiado da planilha

Ao importar uma linha de prospecção, o sistema DEVE calcular `tier_origem`
a partir de `nota` e `avaliacoes` daquela linha, ignorando qualquer valor
de tier presente na planilha. `A` exige `nota >= 4.5` **e**
`avaliacoes >= 100`; `B` exige `nota >= 4.0` **e** `avaliacoes >= 30`;
qualquer outra combinação, incluindo `nota`/`avaliacoes` ausentes, resulta
em `C`.

#### Scenario: Nota alta com poucas avaliações não vira tier A

- **WHEN** uma linha é importada com `nota=4.8` e `avaliacoes=12`
- **THEN** a linha é gravada com `tier_origem="B"` se `nota>=4.0`, ou
  `"C"` caso não bata nenhuma das duas faixas — nunca `"A"`, porque
  `avaliacoes < 100`

#### Scenario: Coluna tier_origem do CSV é ignorada

- **WHEN** uma linha é importada com a coluna `tier_origem="A"` no CSV,
  mas `nota=3.0` e `avaliacoes=5`
- **THEN** a linha é gravada com `tier_origem="C"` — o valor do CSV nunca
  chega ao banco

### Requirement: Nome de loja de ração é sinalizado, sem afetar o tier

Ao importar uma linha, o sistema DEVE marcar `provavel_loja_racao=true`
quando o nome bate num regex de "loja de ração" (case-insensitive, com e
sem acento/cedilha). Este sinal NÃO DEVE alterar o `tier_origem` calculado
pelo requirement acima — são campos independentes.

#### Scenario: Nome com "ração" marca a flag sem mudar o tier

- **WHEN** uma linha é importada com `nome="Ração e Cia"`, `nota=4.9`,
  `avaliacoes=500`
- **THEN** a linha é gravada com `tier_origem="A"` **e**
  `provavel_loja_racao=true`
