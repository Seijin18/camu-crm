# Delta: estagio-reabertura-manual-e-relogio

## ADDED Requirements

### Requirement: Desconsideração de recusa é registrada sem apagar o fato

Desconsiderar um `recusa_explicita` falso positivo DEVE gravar uma linha em
`correcoes` (campo `"recusa_explicita"`, com antes/depois) e NÃO DEVE
apagar ou alterar o fato original em `fatos`.

#### Scenario: Desconsiderar recusa preserva o fato original

- **WHEN** um operador desconsidera um `recusa_explicita=true` numa
  conversa
- **THEN** uma linha nova é gravada em `correcoes`
- **AND** o valor de `recusa_explicita` em `fatos` permanece inalterado

### Requirement: Recusa desconsiderada permite avanço de novo

Uma vez que uma desconsideração ativa existe para o `recusa_explicita` de
uma conversa, `_derive_b2c`/`_derive_b2b` NÃO DEVEM mais tratar esse fato
como terminal — a conversa DEVE poder avançar de estágio a partir do maior
estágio já alcançado antes da recusa, nunca a partir de S1/P0.

#### Scenario: Conversa volta a avançar após desconsideração

- **WHEN** uma conversa presa em estágio terminal por `recusa_explicita`
  tem essa recusa desconsiderada, e uma nova mensagem do cliente chega com
  evidência de avanço
- **THEN** a conversa avança de estágio a partir do maior estágio já
  alcançado antes da recusa

### Requirement: reabrir() recusa reabertura de recusa não-desconsiderada sozinha

`reabrir()` DEVE verificar, internamente, se o estado terminal da conversa
veio de `recusa_explicita` sem desconsideração ativa, e recusar a
reabertura nesse caso — independente de qualquer checagem feita pelo
chamador.

#### Scenario: Chamada direta a reabrir() sem guard externo ainda recusa recusa real

- **WHEN** `reabrir()` é chamada diretamente (sem passar pelo guard de
  `pipeline.py`) para uma conversa terminal por `recusa_explicita` sem
  desconsideração
- **THEN** a chamada não reabre a conversa

### Requirement: Desconsideração exige identificação de quem decidiu

Toda desconsideração de recusa, via CLI ou painel, DEVE exigir um
identificador de quem decidiu (`por`) — nunca uma ação anônima.

#### Scenario: Desconsiderar sem identificação é rejeitado

- **WHEN** uma tentativa de desconsiderar recusa não informa `por`
- **THEN** a ação é rejeitada

### Requirement: mudar_funil_conversa reconcilia contra o histórico

`acoes.mudar_funil_conversa` DEVE reconciliar o estágio atual contra
`eventos_estagio` (o mesmo caminho que `pipeline.recalcular` usa), não ler
`conversas.estagio` diretamente, antes de decidir o `de` do evento a
gravar.

#### Scenario: Estágio divergente entre cache e histórico grava o de correto

- **WHEN** `conversas.estagio` (coluna cache) diverge do estágio real
  reconciliado a partir de `eventos_estagio`
- **THEN** `mudar_funil_conversa` grava o evento com `de` igual ao estágio
  reconciliado, não ao valor da coluna cache

### Requirement: Avanço causado pela Camu não classifica QUENTE

A classificação de temperatura por "avançou de estágio hoje" NÃO DEVE
classificar QUENTE quando o avanço foi causado inteiramente por ação da
Camu (prévia enviada, preço apresentado, proposta B2B) — apenas avanço
causado por fato do cliente conta para essa classificação.

#### Scenario: Avanço 100% por outbound não vira QUENTE

- **WHEN** uma conversa avança de estágio hoje e a causa do avanço é
  inteiramente uma ação registrada da Camu (`causada_por="camu"`)
- **THEN** a conversa não é classificada QUENTE por esse motivo

### Requirement: Timestamp futuro não congela bola_com

`rules/sinais.py` DEVE clampar `enviada_em` a `min(timestamp, agora())`
antes de decidir qual mensagem é a mais recente para fins de `bola_com`.

#### Scenario: Timestamp futuro não impede mensagem real subsequente de contar

- **WHEN** uma mensagem com timestamp futuro é seguida por uma mensagem
  real com timestamp presente
- **THEN** `bola_com` reflete a mensagem real subsequente, não fica preso
  ao timestamp futuro
