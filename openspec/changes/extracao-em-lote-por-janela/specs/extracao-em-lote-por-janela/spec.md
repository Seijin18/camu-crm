# Delta: extracao-em-lote-por-janela

## ADDED Requirements

### Requirement: Gatilho híbrido decide quando extrair imediatamente

O webhook DEVE decidir se extrai imediatamente uma conversa com base em
dois limiares independentes: a contagem de mensagens pendentes desde a
última extração, e há quanto tempo a mensagem pendente mais antiga
espera. Atingir QUALQUER um dos dois é suficiente para disparar a
extração na hora.

#### Scenario: Contagem de pendentes atinge o limiar

- **WHEN** uma conversa acumula mensagens pendentes em quantidade igual ou
  maior que `CAMU_EXTRACAO_LIMIAR_MENSAGENS`
- **THEN** a extração dispara imediatamente, mesmo que a mensagem mais
  antiga pendente tenha chegado há poucos segundos

#### Scenario: Mensagem pendente espera além do teto

- **WHEN** a mensagem pendente mais antiga de uma conversa já espera tempo
  igual ou maior que `CAMU_EXTRACAO_TETO_ESPERA_MINUTOS`
- **THEN** a extração dispara imediatamente, mesmo com poucas mensagens
  pendentes

### Requirement: Abaixo dos limiares, a extração fica pendente para o cron

Quando nem a contagem nem o tempo de espera atingem seus limiares, o
webhook NÃO DEVE chamar o LLM — a mensagem permanece pendente
(`ultima_mensagem_processada_id` não avança) para ser processada por
`camucrm extrair` (execução periódica, fora do escopo deste webhook).

#### Scenario: Rajada curta não dispara chamada de LLM

- **WHEN** uma conversa recebe poucas mensagens em rápida sucessão, abaixo
  do limiar de contagem e dentro do teto de espera
- **THEN** nenhuma chamada de LLM é feita para essas mensagens no momento
  do recebimento

### Requirement: Limiares são configuráveis por ambiente

Os dois limiares do gatilho híbrido DEVEM ser configuráveis por variável
de ambiente, com valor padrão quando não configurados.

#### Scenario: Variável de ambiente ausente usa o padrão

- **WHEN** `CAMU_EXTRACAO_LIMIAR_MENSAGENS`/`CAMU_EXTRACAO_TETO_ESPERA_MINUTOS`
  não estão definidas
- **THEN** o sistema usa os valores padrão documentados, sem erro

#### Scenario: Variável de ambiente configurada é respeitada

- **WHEN** uma das variáveis de ambiente está definida com um valor válido
- **THEN** o gatilho usa esse valor em vez do padrão

### Requirement: Operador pode forçar extração imediata pelo painel

O painel DEVE oferecer uma ação que extrai a conversa imediatamente,
ignorando o gatilho híbrido — para quando o operador quer o estágio
atualizado na hora.

#### Scenario: Extração manual ignora os limiares

- **WHEN** o operador aciona a extração manual de uma conversa cujas
  mensagens pendentes não atingem nenhum dos dois limiares do gatilho
  híbrido
- **THEN** a extração é executada mesmo assim
