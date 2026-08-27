# Delta: contatos-de-teste-isolados

## ADDED Requirements

### Requirement: Marca de teste é por contato

A marca de teste DEVE ser armazenada em `contatos.e_teste`, não em
`conversas` — toda conversa passada e futura de um contato marcado como
teste é automaticamente considerada de teste, sem necessidade de marcar
conversa por conversa.

#### Scenario: Marcar um contato aplica a todas as suas conversas

- **WHEN** um contato com duas conversas (uma antiga, uma futura ainda não
  criada) é marcado como `e_teste=true`
- **THEN** a conversa antiga passa a ser tratada como teste
- **AND** uma conversa nova criada depois para o mesmo contato também é
  tratada como teste

### Requirement: Marcação de teste é sempre manual e registrada

Nenhuma inferência automática DEVE marcar um contato como teste — apenas
uma ação explícita do operador, via CLI (`camucrm marcar-teste`) ou painel.
Toda marcação e desmarcação DEVE gravar uma linha em `correcoes`
(§7), nunca em `marcos_manuais`.

#### Scenario: Marcar contato de teste grava em correções

- **WHEN** um operador marca um contato como teste (CLI ou painel)
- **THEN** uma linha nova é gravada em `correcoes` com quem marcou
- **AND** nenhuma linha é gravada em `marcos_manuais` por essa ação

### Requirement: Leitura agregada exclui teste por padrão

Toda função de leitura agregada listada neste change (kanban, fila,
`GET /api/conversas`, e cada função de `metrics.py` usada por
`GET /api/o-que-funciona`) DEVE excluir contatos marcados como teste por
padrão, e DEVE mostrar apenas contatos de teste quando o modo teste está
explicitamente ativado. Os dois modos NÃO DEVEM se misturar na mesma
resposta.

#### Scenario: Modo padrão exclui contato de teste

- **WHEN** qualquer uma das funções/rotas listadas é chamada sem o modo
  teste ativado, com um contato normal e um contato de teste na base
- **THEN** apenas o contato normal aparece no resultado

#### Scenario: Modo teste mostra apenas contato de teste

- **WHEN** a mesma função/rota é chamada com o modo teste ativado
- **THEN** apenas o contato de teste aparece no resultado
- **AND** o contato normal não aparece

### Requirement: Modo teste nunca mistura as duas visões na mesma tela

O toggle "Modo teste" do painel DEVE propagar o mesmo parâmetro de modo
para toda rota de leitura da tela ativa (kanban, fila, conversas, métricas,
"o que funciona") — nunca uma tela com parte dos blocos em um modo e parte
em outro.

#### Scenario: Ativar o toggle troca todos os blocos da tela

- **WHEN** o operador ativa "Modo teste" no painel
- **THEN** kanban, fila, conversas e métricas da tela passam a mostrar
  apenas contatos de teste, todos ao mesmo tempo

### Requirement: Marcação de teste não afeta processamento

Extração de fatos, regras de estágio/temperatura, geração de rascunho e de
resumo DEVEM continuar rodando normalmente para conversas de contato de
teste — a marca de teste é de visibilidade/agregação, não de
processamento.

#### Scenario: Pipeline processa conversa de teste normalmente

- **WHEN** uma mensagem chega para um contato marcado como teste
- **THEN** extração de fatos, derivação de estágio/temperatura e geração de
  rascunho/resumo rodam exatamente como rodariam para um contato normal
