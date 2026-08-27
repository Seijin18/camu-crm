# Tasks — identificação e relógio confiáveis na recepção

## 1. Implementação — filtro de JID

- [x] 1.1 `camucrm/transport/evolution.py::receber`: descartar eventos de
      `status@broadcast` e `@broadcast` (devolve `None`, mesmo tratamento de
      ruído de protocolo) (→ Requirement "Broadcast e status não criam
      evento").
- [x] 1.2 `camucrm/transport/evolution.py::receber`: recusar (logar e
      devolver `None`, não criar contato) evento cujo JID é `@lid` sem campo
      de PN confiável no payload (→ Requirement "JID sem PN confiável não
      cria contato fantasma").

## 2. Implementação — clamp de timestamp

- [x] 2.1 `camucrm/transport/evolution.py::_timestamp`: clampar timestamp
      futuro com `min(timestamp, agora())` antes de usá-lo em `enviada_em` e
      no `GREATEST` de `ultimo_inbound`/`ultimo_outbound` (→ Requirement
      "Timestamp futuro não trava o relógio da conversa").
- [x] 2.2 `camucrm/transport/evolution.py::_timestamp`: clampar timestamp
      anterior a uma data mínima sã (constante nova, ex.
      `TIMESTAMP_MINIMO_SAO`), sem descartar a mensagem em si — só o valor
      usado para ordenação/`GREATEST` é corrigido (→ Requirement "Timestamp
      futuro não trava o relógio da conversa").

## 3. Testes

- [x] 3.1 `tests/test_transport.py`: evento de `status@broadcast` não
      cria/atualiza contato nem conversa (→ Requirement "Broadcast e status
      não criam evento").
- [x] 3.2 `tests/test_transport.py`: evento de `@broadcast` não cria/atualiza
      contato nem conversa (→ Requirement "Broadcast e status não criam
      evento").
- [x] 3.3 `tests/test_transport.py`: evento `@lid` sem PN confiável é
      recusado — nenhum contato com `telefone_hash` de string vazia é criado
      (→ Requirement "JID sem PN confiável não cria contato fantasma").
- [x] 3.4 `tests/test_transport.py`: timestamp futuro (além da tolerância) é
      clampado a `agora()` antes de alimentar o `GREATEST` de
      `ultimo_inbound` (→ Requirement "Timestamp futuro não trava o relógio
      da conversa").
- [x] 3.5 `tests/test_transport.py` (ou `test_ingest.py`): mensagem real
      subsequente a um timestamp futuro clampado ainda atualiza
      `ultimo_inbound` corretamente — o clamp não deixa o relógio "preso" no
      valor futuro (→ Requirement "Timestamp futuro não trava o relógio da
      conversa").
- [x] 3.6 Suíte completa verde (`make test`).
