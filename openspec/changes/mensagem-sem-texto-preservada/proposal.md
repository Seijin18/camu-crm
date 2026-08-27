# Mensagem sem texto não desaparece na ingestão

## Why

Auditoria pedida pelo usuário antes de operar com petshops e consumidores de
verdade ("garanta que nenhuma mensagem vai deixar de ser acompanhada ou
excluída") encontrou um caso real, não coberto por teste: em
`transport/evolution.py::_texto_da_mensagem`, só `imageMessage`,
`videoMessage` e `documentMessage` têm um caminho que devolve algo (a
legenda, mesmo vazia). Para `audioMessage`, `stickerMessage`,
`contactMessage` e `locationMessage`/`liveLocationMessage`, a função devolve
`None`, e `receber()` descarta o evento inteiro antes de chegar em
`ingerir()` — nenhuma linha em `mensagens`, `bola_com` não muda,
`ultimo_inbound` não avança.

A mesma auditoria, na rodada que mapeou o pipeline inteiro (ver
`literalidade-e-idempotencia-da-extracao` e as demais deste conjunto),
encontrou um segundo caso **mais grave** porque não é limitado a mídia:
`ephemeralMessage`, `viewOnceMessage` e `viewOnceMessageV2` embrulham a
mensagem real (que pode ser **texto puro**, não só mídia) num envelope que
`_texto_da_mensagem` não desembrulha — qualquer cliente com "mensagens
temporárias" ativado no WhatsApp tem CADA mensagem descartada inteira,
texto incluso, pelo mesmo caminho que hoje só se sabia atingir áudio/
figurinha/contato/localização. `deviceSentMessage` (eco de uma mensagem
enviada por outro dispositivo linkado à mesma conta, ex. WhatsApp Web) sofre
do mesmo problema: o payload real fica dentro de `.message`, e sem
desembrulhar, o eco da própria Camu enviado por outro dispositivo desaparece
— o que corrompe `bola_com` na direção oposta (o sistema acha que a Camu
está esperando resposta havendo, na verdade, já respondido por outro
canal).

A consequência não é só "não avança de estágio" (o caso já registrado em
`midia-foto-pet`, para foto sem legenda). É pior: o sistema se comporta como
se o cliente não tivesse dito nada. Um petshop que responde com um áudio
continua contando, para `rules/temperatura.py`, como se estivesse quieto
desde a mensagem anterior — pode esfriar e sumir da fila enquanto o cliente
efetivamente respondeu. Isso é o oposto do invariante que o §5 pede
("reciprocidade e ritmo, não simpatia").

Diferente de `midia-foto-pet` (que trata retenção/LGPD para tratar a FOTO
como evidência de `foto_pet_recebida`), este change **não guarda mídia
nenhuma** — só garante que o fato "o cliente mandou algo aqui, deste tipo,
nesta hora" fica registrado, o que já é suficiente para corrigir `bola_com`
e o relógio de temperatura. Não interfere na extração de fatos: o marcador
gravado (ex. `[áudio recebido]`) não é evidência literal de nenhum fato do
§2, e a conferência de literalidade em `extraction/contract.py::_fold`
continua recusando qualquer fato que tente se basear nele.

## What Changes

- `camucrm/transport/evolution.py::_texto_da_mensagem` ganha um segundo
  estágio de reconhecimento: para os tipos de mídia sem legenda
  (`audioMessage`, `stickerMessage`, `contactMessage`, `locationMessage`,
  `liveLocationMessage`), devolve um marcador textual fixo em português
  (`[áudio recebido]`, `[figurinha recebida]`, `[contato recebido]`,
  `[localização recebida]`) em vez de `None`.
- **Desembrulhar `ephemeralMessage`/`viewOnceMessage`/`viewOnceMessageV2`**:
  o payload real está em `.message` dentro do envelope. A função extrai esse
  `.message` interno e reaplica `_texto_da_mensagem` recursivamente sobre
  ele — se o conteúdo real for texto puro, o texto é preservado como texto
  normal; se for mídia sem legenda, cai no marcador do item acima; a
  recursão é a mesma função, não um caminho paralelo, para não duplicar
  regra.
- **Desembrulhar `deviceSentMessage`**: o payload real (eco de outro
  dispositivo linkado) também está em `.message`. Mesmo desembrulho
  recursivo, preservando `direcao=SAIDA` (é a Camu falando, só que por outro
  dispositivo) — sem isso, o eco correspondente nunca chega a `ingerir()` e
  `bola_com` fica congelado como se a Camu não tivesse respondido.
- O que continua devolvendo `None` (evento descartado, comportamento
  inalterado): `reactionMessage`, mensagens de protocolo/recibo/presença, e
  qualquer chave de mensagem não reconhecida — incluindo um envelope de
  `ephemeralMessage`/`viewOnceMessage*`/`deviceSentMessage` cujo `.message`
  interno também não é reconhecido (a recursão devolve `None` da mesma forma
  que a chamada direta devolveria para esse mesmo conteúdo). Reação a
  mensagem não é evento de conversa — continua fora, com o mesmo teste de
  hoje (`test_evento_sem_texto_e_ignorado`).
- Nenhuma mudança em `ingest.py`, `db.py` ou no schema: o marcador e o texto
  desembrulhado percorrem o caminho que já existe para texto normal (grava
  mensagem, atualiza `bola_com`/`ultimo_inbound`, entra no delta de
  extração). A extração vê o marcador como texto comum e não encontra nele
  evidência de nenhum fato — comportamento correto e já garantido por
  `_fold`.
- `_tipo_de_midia` (hoje um gancho morto) passa a ser referenciado no
  docstring como "o que decide o marcador", deixando de ser código não
  utilizado.

## Impact

- Specs afetadas: `mensagem-sem-texto-preservada` (nova)
- Código alterado: `camucrm/transport/evolution.py`
- Testes alterados: `tests/test_transport.py` (áudio, figurinha, contato,
  localização passam a gerar `EventoRecebido` com o marcador; reação
  continua `None`; texto puro dentro de `ephemeralMessage`/
  `viewOnceMessage`/`viewOnceMessageV2` é preservado; `deviceSentMessage`
  continua contando como eco `out`)
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Guardar o áudio/figurinha/contato/localização em si** (binário, ou
  transcrição do áudio) — isso é `midia-foto-pet` e traz LGPD/retenção
  junto; aqui só o fato "algo chegou" é preservado, não o conteúdo.
- **Extrair fato a partir do marcador** (ex. inferir `foto_pet_recebida` de
  `[áudio recebido]`) — o marcador não é evidência literal de nada, e não
  deve virar uma.
- **`editedMessage`/`protocolMessage` (REVOKE)** — edição/apagamento do
  cliente continuam sem refletir no CRM; impacto é retenção maior que o
  esperado, não perda de dado, e fica registrado como item de backlog em
  `openspec/project.md`, não como escopo deste change.
