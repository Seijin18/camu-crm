# Delta: resumo-conversa

## ADDED Requirements

### Requirement: Resumo é folha do grafo

Nenhuma regra de `rules/` DEVE ler `resumos_conversa`. A tabela existe só
para leitura humana.

#### Scenario: Apagar resumos_conversa não muda estado de nenhuma conversa

- **WHEN** a tabela `resumos_conversa` é apagada inteira
- **THEN** estágio, temperatura e composição da fila de todas as conversas
  permanecem idênticos

### Requirement: Resumo nunca afirma estágio, temperatura ou preço

`validar_resumo` DEVE rejeitar qualquer resumo cujo texto contenha um token
de `TODOS_ESTAGIOS`, um token de `TEMPERATURAS`, ou o preço definido em
`drafts._PRECO`.

#### Scenario: Resumo citando estágio é rejeitado

- **WHEN** o texto gerado pelo LLM contém um token de `TODOS_ESTAGIOS` ou
  `TEMPERATURAS`
- **THEN** `validar_resumo` rejeita e uma retentativa é feita com o motivo

#### Scenario: Resumo citando o preço é rejeitado

- **WHEN** o texto gerado contém o valor de `drafts._PRECO`
- **THEN** `validar_resumo` rejeita e uma retentativa é feita com o motivo

### Requirement: Geração só ao clicar, nunca automática

Nenhuma rota `GET` DEVE gerar resumo como efeito colateral. Geração só
acontece via `POST /api/resumos` explícito.

#### Scenario: Rota GET nunca gera resumo

- **WHEN** qualquer rota `GET` do painel é chamada, incluindo a que exibe o
  detalhe da conversa
- **THEN** nenhum resumo novo é gerado como efeito colateral dessa chamada

### Requirement: Cache por versão de prompt e mensagem

Gerar resumo duas vezes sem mensagem nova NÃO DEVE criar duas linhas —
protegido por índice único em `(conversa_id,
coalesce(ultima_mensagem_id, 0), prompt_versao)`. `?forcar=true` substitui a
linha existente via `ON CONFLICT ... DO UPDATE`.

#### Scenario: Gerar sem mensagem nova não duplica linha

- **WHEN** `POST /api/resumos` é chamado duas vezes seguidas para a mesma
  conversa sem mensagem nova entre as chamadas
- **THEN** existe uma única linha em `resumos_conversa` para aquela
  combinação de conversa e `prompt_versao`

#### Scenario: forcar=true substitui a linha existente

- **WHEN** `POST /api/resumos?forcar=true` é chamado com um resumo já
  existente para o mesmo cursor
- **THEN** a linha existente é atualizada via `ON CONFLICT ... DO UPDATE`,
  não duplicada

### Requirement: Falha de LLM não derruba a tela

Quando o LLM está indisponível, a resposta DEVE conter os blocos
determinísticos (fatos, linha do tempo, objeções, correções, follow-ups) com
`resumo: null`, nunca um erro 500.

#### Scenario: LLM indisponível devolve blocos determinísticos

- **WHEN** o provedor de LLM está indisponível no momento de `POST
  /api/resumos`
- **THEN** a resposta é 200 com os blocos determinísticos preenchidos e
  `resumo: null`

### Requirement: Importadores de summaries são um conjunto fechado

Apenas `camucrm.painel.api` e `camucrm.cli` DEVEM importar
`camucrm.summaries`. Um módulo fora desse conjunto importando `summaries`
DEVE quebrar o teste de guarda.

#### Scenario: Importador fora do conjunto fechado quebra o teste

- **WHEN** um módulo diferente de `camucrm.painel.api` ou `camucrm.cli`
  importa `camucrm.summaries`
- **THEN** o teste de guarda por `ast.parse` falha

### Requirement: Purga remove prosa do resumo

`purgar_mensagens_antigas` DEVE apagar `resumo` e `proximo_passo` de
`resumos_conversa` associados às mensagens purgadas (§12).

#### Scenario: Purga apaga prosa do resumo

- **WHEN** `purgar_mensagens_antigas` remove mensagens antigas cujo
  `ultima_mensagem_id` de resumo aponta para elas
- **THEN** `resumo` e `proximo_passo` da linha correspondente são apagados
