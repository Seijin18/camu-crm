# Delta: mensagem-sem-texto-preservada

## ADDED Requirements

### Requirement: Mensagem de mídia sem legenda gera evento, não silêncio

Uma mensagem do WhatsApp cujo conteúdo é áudio, figurinha, contato ou
localização (com ou sem legenda) DEVE gerar um `EventoRecebido` normal —
grava linha em `mensagens`, atualiza `bola_com` e `ultimo_inbound`/
`ultimo_outbound` — mesmo quando não há texto nem legenda para extrair.
Quando não há legenda, o texto gravado é um marcador fixo identificando o
tipo de conteúdo, nunca `None`/evento descartado.

#### Scenario: Áudio sem legenda vira mensagem registrada

- **WHEN** chega um evento cujo `message` é `audioMessage` sem `caption`
- **THEN** `receber()` devolve um `EventoRecebido` com texto igual ao
  marcador de áudio
- **AND** a ingestão grava a mensagem e atualiza `bola_com` da conversa

#### Scenario: Figurinha, contato e localização seguem a mesma regra

- **WHEN** chega um evento cujo `message` é `stickerMessage`,
  `contactMessage`, `locationMessage` ou `liveLocationMessage`
- **THEN** `receber()` devolve um `EventoRecebido` com o marcador
  correspondente, nunca `None`

### Requirement: Ruído de protocolo continua descartado

Mensagens que não representam conteúdo de conversa — reação
(`reactionMessage`), recibo, presença, ou qualquer tipo de `message` não
reconhecido — DEVEM continuar sendo ignoradas (`receber()` devolve `None`),
sem mudança de comportamento em relação ao que já existia antes deste
change.

#### Scenario: Reação continua sem gerar evento

- **WHEN** chega um evento cujo `message` é `reactionMessage`
- **THEN** `receber()` devolve `None`, como antes deste change

### Requirement: Marcador nunca vira evidência de fato

O marcador textual gravado para mídia sem legenda NÃO DEVE ser aceito como
evidência literal de nenhum fato do §2 — a conferência de literalidade
(`extraction/contract.py`) continua recusando qualquer `true` cuja evidência
seja apenas o marcador, pela mesma regra que já recusa qualquer trecho que
não apareça litteralmente associado ao fato alegado.

#### Scenario: Mensagem só com marcador de áudio não produz fato algum

- **WHEN** o único conteúdo novo da conversa é uma mensagem com o marcador
  de áudio
- **THEN** a extração sobre esse bloco não afirma nenhum fato com esse
  marcador como evidência

### Requirement: Envelope efêmero/view-once é desembrulhado recursivamente

Uma mensagem cujo tipo é `ephemeralMessage`, `viewOnceMessage` ou
`viewOnceMessageV2` DEVE ter seu `.message` interno extraído e reprocessado
pela mesma função de reconhecimento (`_texto_da_mensagem`), recursivamente.
O conteúdo real do envelope — texto puro ou mídia — DEVE ser tratado
exatamente como seria se tivesse chegado sem o envelope: texto puro é
preservado como texto; mídia sem legenda gera o marcador correspondente;
conteúdo não reconhecido resulta em `None`, do mesmo jeito que resultaria
sem o envelope.

#### Scenario: Texto puro dentro de mensagem efêmera não é descartado

- **WHEN** chega um evento cujo `message` é `ephemeralMessage` (ou
  `viewOnceMessage`/`viewOnceMessageV2`) envolvendo um texto puro
- **THEN** `receber()` devolve um `EventoRecebido` com esse texto, não
  `None`

#### Scenario: Mídia sem legenda dentro de envelope efêmero gera marcador

- **WHEN** o `.message` interno de um envelope efêmero/view-once é um dos
  tipos de mídia sem legenda cobertos por este change
- **THEN** `receber()` devolve um `EventoRecebido` com o marcador
  correspondente, do mesmo jeito que devolveria sem o envelope

### Requirement: deviceSentMessage é desembrulhado e conta como eco de saída

Uma mensagem cujo tipo é `deviceSentMessage` DEVE ter seu `.message` interno
extraído e reprocessado recursivamente, preservando `direcao=SAIDA` — é a
Camu falando por outro dispositivo linkado, não uma mensagem nova do
cliente.

#### Scenario: Eco de outro dispositivo continua contando como saída

- **WHEN** chega um evento cujo `message` é `deviceSentMessage` envolvendo
  texto
- **THEN** `receber()` devolve um `EventoRecebido` com esse texto e
  `direcao=SAIDA`
