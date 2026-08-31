# Delta: prospeccao-filtro-e-ordenacao

## ADDED Requirements

### Requirement: Ordenação por relevância, nota ou avaliações

`GET /api/prospeccao` DEVE aceitar um parâmetro `ordenar` com um dos
valores fechados `nome`, `relevancia`, `nota`, `avaliacoes`. `relevancia`
ordena por `tier_origem` (A antes de B antes de C), desempatando por `nota`
descendente e depois por `avaliacoes` descendente. `nota` e `avaliacoes`
ordenam cada um pelo próprio campo, descendente, valores nulos por último.

#### Scenario: Ordenar por relevância prioriza tier A

- **WHEN** a listagem tem lojas de tier `A`, `B` e `C` misturadas
- **AND** `GET /api/prospeccao?ordenar=relevancia` é chamado
- **THEN** todas as lojas de tier `A` aparecem antes de qualquer `B`, que
  aparecem antes de qualquer `C`

#### Scenario: Ordenar por nota

- **WHEN** `GET /api/prospeccao?ordenar=nota` é chamado
- **THEN** a lista vem ordenada por `nota` descendente, com linhas sem
  `nota` por último

### Requirement: Padrão inalterado quando `ordenar` não é informado

Quando `ordenar` está ausente ou não é uma das quatro chaves válidas, o
sistema DEVE ordenar por `nome` ascendente — o mesmo comportamento de
antes deste change.

#### Scenario: Requisição sem `ordenar` mantém o comportamento atual

- **WHEN** `GET /api/prospeccao` é chamado sem o parâmetro `ordenar`
- **THEN** a lista vem ordenada por nome, igual ao comportamento anterior
  a este change

#### Scenario: Valor desconhecido não quebra a rota

- **WHEN** `GET /api/prospeccao?ordenar=lixo` é chamado
- **THEN** a rota responde normalmente, ordenada por `nome` — nenhum erro
  500 nem exceção não tratada
