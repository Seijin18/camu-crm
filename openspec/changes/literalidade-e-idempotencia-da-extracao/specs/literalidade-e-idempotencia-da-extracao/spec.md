# Delta: literalidade-e-idempotencia-da-extracao

## ADDED Requirements

### Requirement: Fronteira entre mensagens sobrevive à normalização

`build_corpus` DEVE unir mensagens com um separador que a normalização de
`_fold` NÃO DEVE colapsar em espaço comum. Um trecho de evidência que só
existe porque o fim de uma mensagem se juntou ao início da próxima NÃO DEVE
validar nenhum fato.

#### Scenario: Evidência que só existe fundida não valida fato

- **WHEN** duas mensagens consecutivas, nenhuma contendo isoladamente o
  trecho de evidência exigido, produzem esse trecho apenas quando concatenadas
  sem separador
- **THEN** a conferência de literalidade recusa o fato correspondente

### Requirement: Fato de cliente exige evidência do lado do cliente

Fatos que exigem fala do cliente (`foto_pet_recebida`,
`intencao_compra_explicita`, `recusa_explicita`, `autorizou_envio_material`,
`visita_aceita`) DEVEM ter sua evidência verificada apenas contra o corpus de
mensagens `in` (cliente). Fatos que exigem ação da Camu
(`preco_apresentado`, `previa_enviada`) DEVEM ter sua evidência verificada
apenas contra o corpus de mensagens `out` (Camu). Um trecho que existe
apenas do lado oposto ao exigido NÃO DEVE validar o campo.

#### Scenario: Evidência de mensagem da Camu não valida fato de cliente

- **WHEN** o único trecho que corresponde à evidência exigida por
  `recusa_explicita` (ou outro fato de direção cliente) aparece apenas em
  uma mensagem `out`
- **THEN** o fato não é confirmado

#### Scenario: Evidência de mensagem do cliente não valida fato da Camu

- **WHEN** o único trecho que corresponde à evidência exigida por
  `preco_apresentado` (ou `previa_enviada`) aparece apenas em uma mensagem
  `in`
- **THEN** o fato não é confirmado

### Requirement: Watermark de extração nunca regride

A escrita de `ultima_mensagem_processada_id` DEVE usar `GREATEST` contra o
valor já gravado, no mesmo padrão de `ultimo_inbound`/`ultimo_outbound`.

#### Scenario: Escrita concorrente não regride o watermark

- **WHEN** duas escritas de `ultima_mensagem_processada_id` ocorrem para a
  mesma conversa, uma com valor menor que o já persistido
- **THEN** o valor final persistido é o maior dos dois, nunca o menor

### Requirement: Gravação de objeção é idempotente

`gravar_objecao` DEVE ser protegida por índice único em `(conversa_id,
categoria, estagio, md5(coalesce(trecho, '')))` com `ON CONFLICT DO NOTHING`
— a mesma objeção, gravada mais de uma vez pelos mesmos dados, NÃO DEVE
produzir mais de uma linha.

#### Scenario: Reprocessamento não duplica objeção

- **WHEN** o mesmo bloco de conversa é processado duas vezes (reprocessamento
  concorrente, ou `forcar=True`) e produz a mesma objeção
- **THEN** existe apenas uma linha em `objecoes` para essa combinação de
  conversa, categoria, estágio e trecho

#### Scenario: Backfill forçado duas vezes não muda a contagem de objeções

- **WHEN** `make backfill --forcar` é executado duas vezes seguidas sobre o
  mesmo histórico
- **THEN** a contagem de linhas em `objecoes` é idêntica após a segunda
  execução
