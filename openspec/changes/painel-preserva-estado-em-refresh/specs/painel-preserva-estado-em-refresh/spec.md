# Delta: painel-preserva-estado-em-refresh

## ADDED Requirements

### Requirement: Filtros sobrevivem ao refresh de tempo real

Os controles de filtro/ordenação das abas Conversas e Prospecção DEVEM
manter a seleção do operador quando um evento `mensagem`/`mudanca` do SSE
dispara um re-render da aba — independentemente de a mudança pertencer à
conversa/linha que o operador está filtrando.

#### Scenario: Filtro de estágio sobrevive a mensagem em outra conversa

- **WHEN** o operador está na aba Conversas com `filtro-estagio=S2`
  selecionado, e uma mensagem nova chega em uma conversa diferente daquela
  filtrada
- **THEN** depois do refresh automático, `filtro-estagio` continua `S2`

#### Scenario: Filtro de prospecção sobrevive a mensagem em conversa não relacionada

- **WHEN** o operador está na aba Prospecção com `tier=A` selecionado, e
  qualquer mensagem chega em qualquer conversa do sistema
- **THEN** depois do refresh automático, o filtro `tier=A` continua
  aplicado e a lista exibida reflete esse filtro

### Requirement: Refresh automático não apaga edição em andamento

Enquanto o operador tem um formulário de escrita com conteúdo não-vazio
aberto (ground truth novo/editar, importar prospecção, importar conversa do
WhatsApp), o sistema NÃO DEVE substituir o conteúdo da tela em reação a um
evento `mensagem`/`mudanca` do SSE. Em vez disso DEVE sinalizar que existe
atualização pendente, sem apagar o formulário, e o botão "Atualizar"
manual DEVE continuar disponível para aplicar a atualização por escolha do
operador.

#### Scenario: Formulário de ground truth não é apagado por mensagem em outra conversa

- **WHEN** o operador está em `#/groundtruth/novo` com texto digitado no
  campo de mensagens, e uma mensagem chega em qualquer conversa do sistema
- **THEN** o formulário e o texto digitado continuam intactos na tela
- **AND** um indicador visual mostra que há atualização disponível

#### Scenario: Refresh automático retoma normalmente após o formulário ser limpo ou enviado

- **WHEN** o formulário de ground truth é enviado com sucesso, cancelado, ou
  todos os campos voltam a vazio
- **THEN** o próximo evento `mensagem`/`mudanca` do SSE volta a disparar o
  refresh automático normalmente

### Requirement: Escrita em voo nunca é descartada silenciosamente

Enquanto uma chamada de escrita (`chamarApiEscrever`) para gerar rascunho,
registrar escolha de rascunho, ou desconsiderar recusa estiver em voo, o
sistema NÃO DEVE substituir o container que receberá o resultado dessa
chamada em reação a um evento `mensagem`/`mudanca` do SSE.

#### Scenario: Rascunho gerado aparece na tela mesmo com mensagem concorrente em outra conversa

- **WHEN** o operador clica "Gerar rascunho" numa conversa, e antes da
  resposta do LLM voltar uma mensagem chega em outra conversa
- **THEN** quando a resposta chegar, o rascunho gerado é exibido
  normalmente na tela — não é escrito num nó desconectado do DOM
