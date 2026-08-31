# Delta: painel-refresh-ignora-contato-de-teste

## ADDED Requirements

### Requirement: Cursor de tempo real ignora contato de teste

`Database.token_de_mudanca` NÃO DEVE mudar de valor quando a única
alteração no banco (mensagem nova, evento de estágio novo, ou toque em
`conversas.atualizado_em`) pertence a uma conversa cujo contato tem
`e_teste = TRUE`.

#### Scenario: Mensagem para contato de teste não move o token

- **WHEN** o valor de `token_de_mudanca` é lido
- **AND** uma mensagem é registrada numa conversa cujo contato tem
  `e_teste = TRUE`
- **THEN** uma nova leitura de `token_de_mudanca` devolve o mesmo valor de
  antes

#### Scenario: Mensagem para contato real move o token normalmente

- **WHEN** o valor de `token_de_mudanca` é lido
- **AND** uma mensagem é registrada numa conversa cujo contato tem
  `e_teste = FALSE`
- **THEN** uma nova leitura de `token_de_mudanca` devolve um valor
  diferente do anterior

#### Scenario: Refresh automático do painel não dispara por conversa de teste

- **WHEN** o painel está conectado ao stream de tempo real, numa aba
  qualquer, sem "Modo teste" ativo
- **AND** uma mensagem é enviada ou recebida numa conversa de contato de
  teste
- **THEN** nenhum evento `mudanca`/`mensagem` chega ao cliente por causa
  dessa mensagem, e a aba não recarrega
