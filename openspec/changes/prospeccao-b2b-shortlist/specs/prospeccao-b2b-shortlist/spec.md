# Delta: prospeccao-b2b-shortlist

## ADDED Requirements

### Requirement: Shortlist separada de contatos/conversas

Uma linha de prospecção NÃO DEVE aparecer em nenhuma tela ou métrica do
funil de conversas (kanban, fila, lista de conversas, `/api/metricas`,
`/api/o-que-funciona`) enquanto não existir um `contato`/`conversa` real
com o mesmo telefone. A tela de prospecção DEVE ser uma aba própria do
painel, nunca mesclada com essas telas.

#### Scenario: Importar planilha não altera kanban/fila

- **WHEN** uma planilha de prospecção é importada com N linhas novas
- **THEN** o kanban, a fila e a lista de conversas continuam mostrando
  exatamente as mesmas conversas de antes, sem nenhuma das N linhas

### Requirement: Importação nunca descarta linha em silêncio

Uma linha da planilha cujo telefone não pode ser normalizado (vazio, sem
dígitos suficientes) DEVE ser reportada explicitamente no resultado da
importação (contagem + motivo), nunca omitida sem indicação.

#### Scenario: Linha com telefone ilegível é reportada

- **WHEN** a planilha importada tem uma linha com telefone vazio ou sem
  dígitos suficientes
- **THEN** o resultado da importação inclui essa linha na contagem de
  inválidas, com o motivo, e ela não vira uma linha de `prospeccoes`

### Requirement: Reimportar a mesma planilha atualiza, não duplica

Uma linha de prospecção é identificada por `telefone_hash` (mesmo hash
usado em `contatos`). Reimportar uma planilha com uma linha de telefone já
existente DEVE atualizar os campos dessa linha, não criar uma segunda.

#### Scenario: Reimportação idempotente

- **WHEN** a mesma planilha (ou uma versão atualizada, mesmo telefone) é
  importada duas vezes
- **THEN** existe exatamente uma linha de `prospeccoes` para aquele
  telefone depois da segunda importação

### Requirement: Detecção de conversão sem estado próprio

Uma linha de prospecção cujo `telefone_hash` bate com um `contatos.telefone_hash`
existente DEVE ser identificável como "já é conversa" na leitura, sem
depender de um campo ou job de sincronização que possa ficar desatualizado.

#### Scenario: Prospecção vira conversa real

- **WHEN** um contato/conversa é criado (via `ingest.ingerir`) com o mesmo
  telefone de uma linha existente em `prospeccoes`
- **THEN** a próxima leitura de `listar_prospeccoes` para essa linha expõe
  o `contato_id`/`conversa_id` correspondente

### Requirement: Conversão usa tipo B2B da origem curada, não inferência de conteúdo

Quando uma mensagem inbound chega de um telefone presente em `prospeccoes`,
o `contato` novo criado por `ingest.ingerir` DEVE nascer com `tipo=b2b`,
mesmo que o `tipo_padrao` do chamador seja B2C. Isso não é inferência de
conteúdo de conversa (proibida por §1) — é uso de uma classificação já
explicitamente declarada pelo operador ao importar a planilha como
prospecção B2B.

#### Scenario: Resposta de petshop da shortlist nasce B2B

- **WHEN** chega uma mensagem inbound de um telefone que está em
  `prospeccoes`
- **THEN** o contato criado tem `tipo=b2b`, independentemente do
  `tipo_padrao` default de `ingest.ingerir`

### Requirement: Mensagem é template fixo, não geração por LLM

O texto da mensagem de prospecção DEVE vir de um template editável fora do
código (arquivo de texto com um placeholder `{nome}`), nunca de uma chamada
a LLM. Esta capability NÃO DEVE introduzir uma quarta superfície de LLM.

#### Scenario: Texto da mensagem não chama modelo nenhum

- **WHEN** o link de WhatsApp de uma linha de prospecção é montado
- **THEN** nenhuma chamada a `camucrm.llm`/qualquer provedor de modelo
  acontece nesse caminho

### Requirement: Disparo é link do WhatsApp, nunca envio pela API

O botão/link de disparo de uma linha de prospecção DEVE abrir
`https://api.whatsapp.com/send/?phone=...&text=...` (ou equivalente),
nunca chamar uma rota de envio própria do painel. `camucrm.painel` NÃO DEVE
importar `camucrm.transport` por causa desta capability — a mesma garantia
que já vale para o resto do painel.

#### Scenario: Nenhuma rota de envio nasce com esta capability

- **WHEN** o código desta capability é revisado
- **THEN** nenhuma rota nova do painel chama `transport.enviar`, e
  `camucrm.painel` continua sem importar `camucrm.transport`

### Requirement: Abertura de link é registrada

Quando o operador clica "abrir WhatsApp" para uma linha, o sistema DEVE
registrar quem e quando clicou (`aberto_em`/`aberto_por`) — como intenção
registrada, não como confirmação de envio (que o sistema não pode conhecer,
já que o envio acontece fora dele, dentro do WhatsApp).

#### Scenario: Clique registra abertura, não envio

- **WHEN** o operador clica "abrir WhatsApp" numa linha de prospecção
- **THEN** `aberto_em`/`aberto_por` são gravados para aquela linha
- **AND** nenhum campo do sistema afirma que a mensagem foi de fato
  enviada ou entregue
