# Delta: painel-tempo-real (prospeccao-tempo-real-sem-pulo)

## MODIFIED Requirements

### Requirement: Token de mudança como cursor

O `token_de_mudanca` DEVE mudar sempre que uma mensagem nova é registrada,
um evento de estágio é gravado, `conversas.atualizado_em` muda, ou
`prospeccoes.atualizado_em` muda. O token DEVE ser uma string de quatro
partes separadas por `:` (`"m:e:c:p"`), e cada parte DEVE se mover de forma
independente — uma marca de triagem numa linha de prospecção move a quarta
parte e NÃO DEVE mover as três primeiras. O mesmo token DEVE servir como
cursor de reconexão (`?desde_id=N`), lido a partir da primeira parte, de
forma que um cliente que reconecta depois de ficar offline não perca
mensagens ocorridas durante a queda.

#### Scenario: Token muda com mensagem, evento de estágio ou atualização de conversa

- **WHEN** uma mensagem é registrada, ou um evento em `eventos_estagio` é
  gravado, ou `conversas.atualizado_em` avança
- **THEN** `token_de_mudanca` calculado depois é diferente do calculado antes

#### Scenario: Marca de triagem de prospecção move só a quarta parte

- **WHEN** uma linha de `prospeccoes` é marcada (aberta, envio registrado,
  "já enviado" manual, "não é WhatsApp") e nenhuma mensagem nem evento de
  estágio é gravado
- **THEN** a quarta parte de `token_de_mudanca` muda
- **AND** as três primeiras partes ficam iguais

#### Scenario: Reconexão com desde_id não perde eventos

- **WHEN** um cliente SSE reconecta informando `desde_id` do último evento
  que recebeu antes de cair
- **THEN** o stream entrega, antes de retomar o tempo real, tudo o que
  mudou entre `desde_id` e o momento da reconexão

## ADDED Requirements

### Requirement: Re-render do stream é recortado por rota

Ao receber um evento `mensagem` ou `mudanca` do stream, o painel MUST (DEVE)
recarregar a tela inteira (`renderizarRotaSegura`) SOMENTE quando a rota
atual reflete o stream de conversas — fila (`#/`), kanban, lista de conversas
ou detalhe de conversa. Nas demais rotas (prospecção, importações, ground
truth, métricas, "o que funciona"), um evento do stream NÃO DEVE limpar nem
remontar `#conteudo`. O botão "Atualizar" manual MUST continuar funcionando
em todas as rotas.

#### Scenario: Mensagem nova não redesenha a aba de métricas

- **WHEN** o operador está em `#/metricas` e o stream emite `mudanca` porque
  chegou uma mensagem de WhatsApp
- **THEN** `#conteudo` não é limpo nem remontado, e a posição de scroll não
  muda

#### Scenario: Mensagem nova ainda redesenha a fila

- **WHEN** o operador está em `#/` (fila) e o stream emite `mudanca` porque
  chegou uma mensagem
- **THEN** a fila é recarregada com os dados novos

### Requirement: Aba de prospecção sincroniza entre operadores sem perder o lugar

Quando a quarta parte do token muda e a rota atual é a lista de prospecção
(`#/prospeccao`), o painel MUST (DEVE) recarregar a lista de linhas a partir
do servidor, com os filtros atuais, SEM limpar `#conteudo` nem recriar os
campos de filtro. A posição de scroll e o conteúdo dos campos de filtro
MUST (DEVEM) ser preservados. Uma mudança que NÃO move a quarta parte (ex.:
mensagem de WhatsApp) NÃO DEVE recarregar a lista de prospecção.

#### Scenario: Operador B marca uma linha, operador A vê sem ser jogado pro topo

- **WHEN** o operador A está com `#/prospeccao` aberta e rolada para baixo, e
  o operador B marca uma linha como "já enviado"
- **THEN** a lista do operador A reflete a marca do B
- **AND** o scroll do operador A continua onde estava
- **AND** o que o operador A tinha digitado nos filtros continua lá

#### Scenario: Mensagem de WhatsApp não mexe na lista de prospecção

- **WHEN** o operador está em `#/prospeccao` e chega uma mensagem de WhatsApp
  numa conversa qualquer
- **THEN** a lista de prospecção não é recarregada
