# Delta: importacao-conversas-whatsapp

## ADDED Requirements

### Requirement: Parser puro, sem I/O

`camucrm/whatsapp_export.py` NÃO DEVE fazer chamada de rede, chamada a
`camucrm.llm`, nem acesso a `camucrm.db`. Recebe texto e devolve estrutura;
toda persistência acontece fora dele, via `backfill.importar_conversas` já
existente.

#### Scenario: Parser roda sem banco e sem LLM configurados

- **WHEN** `whatsapp_export.parse` é chamado com o texto de um `.txt`
  exportado e um `nosso_nome`
- **THEN** o resultado é produzido sem qualquer variável de ambiente de
  banco ou LLM precisar estar configurada

### Requirement: Direção exige correspondência de nome, nunca fallback silencioso

Se `nosso_nome` não aparecer em nenhuma linha reconhecida como mensagem do
arquivo, a importação DEVE falhar com um erro explícito. Nenhuma mensagem
NUNCA DEVE ser importada com direção assumida por padrão quando a
correspondência de nome falhar.

#### Scenario: Nome do operador não bate com nenhum remetente

- **WHEN** o arquivo `.txt` é importado com `nome_operador` que não aparece
  como remetente em nenhuma linha reconhecida
- **THEN** a rota retorna erro explícito, e nenhuma mensagem desse arquivo é
  gravada em `mensagens`

### Requirement: Mensagem sem texto (mídia) é preservada, não descartada

Uma linha de mídia (placeholder de imagem, áudio, vídeo, figurinha, contato
ou documento omitido) DEVE virar uma mensagem registrada com indicação de
que é mídia, nunca ser descartada silenciosamente — mesmo contrato do
change `mensagem-sem-texto-preservada`.

#### Scenario: Linha de mídia vira mensagem preservada

- **WHEN** o `.txt` importado tem uma linha `<Mídia oculta>` (ou variante
  reconhecida)
- **THEN** o resumo da importação conta essa linha como mídia preservada, e
  uma mensagem correspondente existe em `mensagens` após a importação

### Requirement: Linha não reconhecida é reportada, nunca descartada em silêncio

Uma linha do `.txt` que não corresponde a mensagem, continuação de
mensagem, mídia ou aviso de sistema conhecido DEVE ser contada em
`ignoradas` no resultado da importação.

#### Scenario: Linha em formato desconhecido é reportada

- **WHEN** o `.txt` importado contém uma linha que não bate com nenhum
  padrão reconhecido pelo parser
- **THEN** o resumo da importação inclui essa linha na contagem de
  `ignoradas`, com o conteúdo original disponível para conferência

### Requirement: Exportação de grupo é rejeitada por inteiro

Um arquivo `.txt` identificado como exportação de grupo (mais de dois
remetentes distintos nas linhas reconhecidas, ou presença de linha de
sistema de entrada/saída de participante) DEVE ser rejeitado inteiro, com
erro explícito — nunca importado parcialmente.

#### Scenario: Upload de exportação de grupo é recusado

- **WHEN** o operador envia um `.txt` de um grupo do WhatsApp
- **THEN** a importação é recusada com erro explícito, e nenhuma mensagem
  desse arquivo é gravada

### Requirement: Extração usa `origem='live'`, com timestamp real por transição

A extração de uma conversa importada por este caminho DEVE rodar com
`origem='live'` (o padrão de `Extrator.processar_conversa`, sem `forcar`),
reaproveitando a rota já existente `POST /conversas/{conversa_id}/extrair`
— nunca `origem='backfill'`. O `.txt` exportado carrega timestamp real por
mensagem; descartar isso excluiria essas conversas de métrica de tempo por
estágio permanentemente, o que não reflete a natureza do dado.

#### Scenario: Conversa importada entra em métrica de tempo por estágio

- **WHEN** uma conversa é importada por este caminho e depois extraída
  (via `POST /conversas/{conversa_id}/extrair`)
- **THEN** os eventos de estágio dessa conversa entram no cálculo de
  duração média por estágio de `metrics.py`, do mesmo jeito que qualquer
  outra conversa `origem='live'`

#### Scenario: Reimportação processa só o bloco novo

- **WHEN** a mesma conversa é reexportada do WhatsApp (com mensagens novas
  desde a última importação) e reimportada, e a extração é disparada de
  novo
- **THEN** só as mensagens novas (posteriores a
  `conversa.ultima_mensagem_processada_id`) são processadas pelo LLM — a
  mesma extração incremental que uma conversa alimentada por webhook já
  tem

### Requirement: Upload não persiste o arquivo bruto em disco

O conteúdo do `.txt` enviado DEVE ser processado em memória; o sistema
NÃO DEVE gravar uma cópia do arquivo bruto no disco do servidor.

#### Scenario: Nenhum arquivo novo aparece no disco após a importação

- **WHEN** um `.txt` é importado pela rota do painel
- **THEN** nenhum arquivo correspondente ao upload é criado no sistema de
  arquivos do servidor — só as mensagens estruturadas chegam ao banco

### Requirement: Extração é passo separado do upload

A extração via LLM (`Extrator.processar_conversa`) para uma conversa
importada DEVE ser disparada por uma ação separada do upload — o upload por
si só DEVE apenas gravar as mensagens e devolver o resumo do parse.

#### Scenario: Upload sozinho não chama LLM

- **WHEN** o operador faz upload de um `.txt` sem clicar em "extrair"
- **THEN** nenhuma chamada a `camucrm.llm` acontece, e as mensagens já
  aparecem gravadas na conversa
