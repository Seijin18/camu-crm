# Tasks — mensagem sem texto não desaparece na ingestão

## 1. Implementação

- [x] 1.1 `camucrm/transport/evolution.py::_texto_da_mensagem`: adicionar o
      segundo estágio de reconhecimento — `audioMessage`, `stickerMessage`,
      `contactMessage`, `locationMessage`, `liveLocationMessage` devolvem um
      marcador fixo em português em vez de `None` (→ Requirement "Mensagem
      de mídia sem legenda gera evento, não silêncio").
- [x] 1.2 Manter `reactionMessage` e tipos não reconhecidos devolvendo
      `None` — nenhuma mudança de comportamento aqui (→ Requirement "Ruído
      de protocolo continua descartado").
- [x] 1.3 `camucrm/transport/evolution.py::_texto_da_mensagem`: desembrulhar
      `ephemeralMessage`, `viewOnceMessage` e `viewOnceMessageV2` — extrair
      `.message` interno e reaplicar `_texto_da_mensagem` recursivamente
      sobre ele, preservando tanto texto puro quanto o marcador de mídia do
      item 1.1 (→ Requirement "Envelope efêmero/view-once é desembrulhado
      recursivamente").
- [x] 1.4 `camucrm/transport/evolution.py::_texto_da_mensagem`: desembrulhar
      `deviceSentMessage` do mesmo jeito (extrair `.message`, reaplicar
      recursivamente), preservando `direcao=SAIDA` (→ Requirement
      "deviceSentMessage é desembrulhado e conta como eco de saída").
- [x] 1.5 Confirmar que um `.message` interno não reconhecido, dentro de
      qualquer um dos envelopes acima, resulta em `None` — a recursão não
      inventa marcador para conteúdo que a chamada direta também não
      reconheceria (→ Requirement "Envelope efêmero/view-once é
      desembrulhado recursivamente").
- [x] 1.6 Atualizar o docstring de `_tipo_de_midia` para parar de descrevê-lo
      como gancho morto — ele passa a ser consultado (ainda que
      indiretamente, via a mesma lista de chaves) pelo marcador.

## 2. Testes

- [x] 2.1 `tests/test_transport.py`: um teste por tipo (`audioMessage`,
      `stickerMessage`, `contactMessage`, `locationMessage`,
      `liveLocationMessage`) confirmando que `receber()` devolve
      `EventoRecebido` com o marcador esperado, não `None` (→ Requirement
      "Mensagem de mídia sem legenda gera evento, não silêncio").
- [x] 2.2 Confirmar que `test_evento_sem_texto_e_ignorado` (reactionMessage)
      continua passando sem alteração (→ Requirement "Ruído de protocolo
      continua descartado").
- [x] 2.3 `tests/test_transport.py`: texto puro dentro de `ephemeralMessage`
      (e `viewOnceMessage`/`viewOnceMessageV2`) não é descartado — devolve
      `EventoRecebido` com o texto real, não `None` nem marcador (→
      Requirement "Envelope efêmero/view-once é desembrulhado
      recursivamente").
- [x] 2.4 `tests/test_transport.py`: mídia sem legenda dentro de um envelope
      efêmero/view-once ainda gera o marcador correspondente (recursão
      preserva o item 1.1) (→ Requirement "Envelope efêmero/view-once é
      desembrulhado recursivamente").
- [x] 2.5 `tests/test_transport.py`: `deviceSentMessage` com texto interno
      continua contando como eco `out` — `EventoRecebido` com
      `direcao=SAIDA` (→ Requirement "deviceSentMessage é desembrulhado e
      conta como eco de saída").
- [x] 2.6 `tests/test_ingest.py` ou extensão de `tests/test_e2e.py`: um
      áudio inbound grava mensagem, atualiza `bola_com` para "cliente" e
      `ultimo_inbound`, e não produz fato nenhum na extração (marcador não é
      evidência literal) (→ Requirement "Marcador nunca vira evidência de
      fato").
- [x] 2.7 Suíte completa verde (`make test`).
