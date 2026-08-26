# Delta: analise-desempenho

## ADDED Requirements

### Requirement: Toda porcentagem vem com n

Qualquer taxa exibida na tela `/funciona` DEVE vir acompanhada do tamanho da
amostra (`n`) que a sustenta.

#### Scenario: Porcentagem exibida sempre tem n ao lado

- **WHEN** a tela `/funciona` exibe qualquer taxa (conversão, retorno por
  follow-up, aceite sem edição, etc.)
- **THEN** o `n` da amostra aparece junto ao número

### Requirement: Porcentagem some abaixo da amostra mínima

Quando `n < AMOSTRA_MINIMA`, a tela NÃO DEVE exibir o número — DEVE exibir
"sem amostra" no lugar.

#### Scenario: Amostra pequena mostra "sem amostra"

- **WHEN** uma taxa tem `n < AMOSTRA_MINIMA`
- **THEN** a tela mostra "sem amostra" em vez do percentual calculado

### Requirement: Backfill fora de métrica de tempo

Tempo por estágio DEVE excluir linhas com `origem='backfill'`, filtradas no
SQL (invariante 4 do `CLAUDE.md`).

#### Scenario: Tempo por estágio ignora eventos de backfill

- **WHEN** a mediana de tempo por estágio é calculada
- **THEN** a consulta SQL filtra `origem != 'backfill'` antes de agregar,
  não depois via checagem em Python

### Requirement: Sem linha de tendência

Nenhuma série temporal exibida DEVE desenhar linha de tendência sobre poucos
pontos.

#### Scenario: Nenhum gráfico da tela desenha tendência

- **WHEN** a tela `/funciona` é renderizada
- **THEN** nenhum elemento visual extrapola ou ajusta tendência sobre os
  dados exibidos

### Requirement: Bloco de rascunhos nasce bloqueado

Enquanto o número de envios vinculados por rascunho estiver abaixo do
limiar definido, a tela DEVE mostrar um contador de progresso, não um
gráfico vazio.

#### Scenario: Amostra de rascunhos abaixo do limiar mostra contador

- **WHEN** o número de rascunhos com `mensagem_id` vinculado é menor que o
  limiar configurado
- **THEN** a tela mostra "precisa de X envios vinculados; hoje há N", sem
  desenhar nenhum gráfico do bloco de rascunhos

### Requirement: Sem afirmação de acurácia de extração antes do ground truth

`/funciona` NÃO DEVE exibir nenhuma métrica de acurácia de extração até
`ground-truth-marcos` existir. Conversão e tempo por estágio continuam
visíveis, porque não dependem do eval.

#### Scenario: Tela não afirma acurácia de extração sem ground truth

- **WHEN** `ground-truth-marcos` ainda não foi implementado
- **THEN** `/funciona` não exibe nenhum número de acurácia de extração
- **AND** conversão de estágio e tempo por estágio continuam visíveis
  normalmente
