# Delta: prospeccao-popup-operador

## ADDED Requirements

### Requirement: Ação sem operador mostra popup de escolha em vez de erro

Ao clicar "Marcar como já enviado", "Não é número de WhatsApp" ou
"Desfazer" numa linha da Prospecção sem um operador válido selecionado no
topo, o sistema DEVE mostrar um popup para escolher o operador antes de
executar a ação, em vez de deixar a chamada falhar e mostrar o erro do
servidor.

#### Scenario: Clicar ação sem operador mostra popup

- **WHEN** o dropdown "Quem está operando" do topo está vazio
- **AND** o operador clica "Marcar como já enviado" numa linha da
  Prospecção
- **THEN** um popup aparece pedindo para escolher o operador, e a ação só é
  executada depois de uma escolha confirmada

#### Scenario: Cancelar o popup não executa a ação

- **WHEN** o popup de escolha de operador está aberto
- **AND** o operador fecha o popup (Cancelar, Escape, ou clique fora)
sem escolher
- **THEN** nenhuma chamada é feita ao servidor, e a linha permanece como
  estava

#### Scenario: Operador já selecionado não mostra popup

- **WHEN** o dropdown "Quem está operando" do topo já tem um valor válido
- **AND** o operador clica em qualquer uma das três ações
- **THEN** a ação é executada direto, sem popup nenhum

### Requirement: Escolha de operador dentro de popup atualiza o dropdown do topo

Sempre que um operador é escolhido dentro de um popup do painel (o de
"quem está operando" ou o de "Enviar pela Evolution API"), o `<select>` de
operador no cabeçalho DEVE refletir o valor escolhido imediatamente, sem
exigir recarregar a página.

#### Scenario: Escolha no popup de operador reflete no topo

- **WHEN** o operador escolhe um nome no popup "Quem está operando?" e
  confirma
- **THEN** o `<select>` do cabeçalho passa a mostrar esse nome selecionado

#### Scenario: Escolha no popup de envio também reflete no topo

- **WHEN** o operador preenche "Aprovado por" dentro do popup "Enviar pela
  Evolution API" com um nome diferente do que estava no topo, e confirma o
  envio com sucesso
- **THEN** o `<select>` do cabeçalho passa a mostrar esse nome selecionado
