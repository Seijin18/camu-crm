# Delta: painel-mensagens-recentes-e-acoes-seguras

## ADDED Requirements

### Requirement: Mensagens recentes aparecem por padrão

`GET .../mensagens` DEVE trazer, por padrão (sem `desde_id`), as mensagens
MAIS RECENTES de uma conversa, com paginação real via cursor "antes de X" e
`total` no payload — nunca as mais antigas quando a conversa tem mais
mensagens que o limite de exibição.

#### Scenario: Conversa com mais de 200 mensagens mostra as recentes

- **WHEN** `GET .../mensagens` é chamado sem `desde_id` para uma conversa
  com mais de 200 mensagens
- **THEN** as mensagens retornadas são as mais recentes, não as mais
  antigas
- **AND** o payload indica que existem mais mensagens além das exibidas

### Requirement: Kanban e fila expõem contagem real

As rotas de kanban e fila DEVEM expor o `total` real de conversas abertas,
mesmo quando o resultado exibido é cortado pelo `limite` de exibição.

#### Scenario: Corte pelo limite não esconde o total real

- **WHEN** o número de conversas abertas excede o `limite` de exibição do
  kanban ou da fila
- **THEN** a resposta inclui o `total` real, não apenas a lista cortada

### Requirement: Corte prioriza conversas negligenciadas

Quando kanban ou fila precisam cortar por `limite`, o corte DEVE priorizar
manter visíveis as conversas mais NEGLIGENCIADAS (sem toque humano recente),
cortando primeiro as mais recentes ou já sendo atendidas.

#### Scenario: Corte remove conversas recentes antes das negligenciadas

- **WHEN** o número de conversas abertas excede o limite e inclui tanto
  conversas negligenciadas quanto conversas recentemente tocadas
- **THEN** as conversas negligenciadas permanecem visíveis, e as mais
  recentes/já atendidas são cortadas primeiro

### Requirement: Ações concorrentes no mesmo card não corrompem marcos_manuais

`acoes.marcar_marco` e `acoes.mudar_funil_conversa` DEVEM impedir, por trava
(`SELECT ... FOR UPDATE` ou coluna de versão), que duas escritas
concorrentes na mesma conversa produzam `marcos_manuais` contraditório.

#### Scenario: Duas marcações concorrentes não produzem estado contraditório

- **WHEN** duas requisições quase simultâneas tentam marcar a mesma
  conversa com marcos contraditórios (ex. "ganho" e "perdido")
- **THEN** apenas uma delas é aplicada — a outra é recusada ou serializada
  de forma consistente, nunca as duas gravadas

### Requirement: mudar_funil_conversa persiste temperatura

`acoes.mudar_funil_conversa` DEVE persistir `temperatura` junto com
`estagio`, reutilizando o mesmo `recalcular(persistir=True)` que
`marcar_marco` já usa — nunca um `UPDATE` parcial que só toca `estagio`.

#### Scenario: Mudança de funil também atualiza temperatura

- **WHEN** `mudar_funil_conversa` é chamada para uma conversa
- **THEN** tanto `estagio` quanto `temperatura` em `conversas` refletem o
  resultado de `recalcular`, sem esperar a próxima mensagem chegar

### Requirement: Vínculo de rascunho não é sobrescrito por corrida

`db.py::vincular_rascunho` DEVE incluir `WHERE mensagem_id IS NULL` na
cláusula do `UPDATE` — uma segunda tentativa de vínculo não DEVE sobrescrever
um `mensagem_id` já vinculado.

#### Scenario: Segunda reconciliação não sobrescreve vínculo existente

- **WHEN** um rascunho já tem `mensagem_id` vinculado e uma segunda
  reconciliação tenta vincular um `mensagem_id` diferente ao mesmo
  rascunho
- **THEN** o vínculo original permanece — a segunda tentativa não altera a
  linha
