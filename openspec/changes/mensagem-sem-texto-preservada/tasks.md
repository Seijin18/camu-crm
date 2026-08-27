# Tasks — mensagem sem texto não desaparece na ingestão

## 1. Implementação

- [ ] 1.1 `camucrm/transport/evolution.py::_texto_da_mensagem`: adicionar o
      segundo estágio de reconhecimento — `audioMessage`, `stickerMessage`,
      `contactMessage`, `locationMessage`, `liveLocationMessage` devolvem um
      marcador fixo em português em vez de `None` (→ Requirement "Mensagem
      de mídia sem legenda gera evento, não silêncio").
- [ ] 1.2 Manter `reactionMessage` e tipos não reconhecidos devolvendo
      `None` — nenhuma mudança de comportamento aqui (→ Requirement "Ruído
      de protocolo continua descartado").
- [ ] 1.3 Atualizar o docstring de `_tipo_de_midia` para parar de descrevê-lo
      como gancho morto — ele passa a ser consultado (ainda que
      indiretamente, via a mesma lista de chaves) pelo marcador.

## 2. Testes

- [ ] 2.1 `tests/test_transport.py`: um teste por tipo (`audioMessage`,
      `stickerMessage`, `contactMessage`, `locationMessage`,
      `liveLocationMessage`) confirmando que `receber()` devolve
      `EventoRecebido` com o marcador esperado, não `None` (→ Requirement
      "Mensagem de mídia sem legenda gera evento, não silêncio").
- [ ] 2.2 Confirmar que `test_evento_sem_texto_e_ignorado` (reactionMessage)
      continua passando sem alteração (→ Requirement "Ruído de protocolo
      continua descartado").
- [ ] 2.3 `tests/test_ingest.py` ou extensão de `tests/test_e2e.py`: um
      áudio inbound grava mensagem, atualiza `bola_com` para "cliente" e
      `ultimo_inbound`, e não produz fato nenhum na extração (marcador não é
      evidência literal) (→ Requirement "Marcador nunca vira evidência de
      fato").
- [ ] 2.4 Suíte completa verde (`make test`).
