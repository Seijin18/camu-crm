# Tasks — backfill seguro para reexecução

## 1. Implementação — idempotência de mensagens

- [ ] 1.1 `camucrm/backfill.py::importar_conversas`: gerar `externa_id`
      sintético estável (hash de contato+texto+timestamp) para mensagem sem
      id de origem (→ Requirement "Reimportar dump sem externa_id não
      duplica mensagem").

## 2. Implementação — chunking e ordenação

- [ ] 2.1 `camucrm/extraction/extractor.py`/`backfill.py`: dividir
      histórico grande em blocos de tamanho administrável para a chamada de
      LLM, preservando ordem cronológica entre blocos (→ Requirement
      "Histórico grande não estoura numa chamada só").
- [ ] 2.2 `camucrm/db.py` (`mensagens_novas` ou consumo em `Extrator`):
      ordenar por `enviada_em`, não só `id`, ao montar o corpus para o LLM
      (→ Requirement "Ordem de leitura bate com enviada_em").

## 3. Implementação — trilha de backfill

- [ ] 3.1 `camucrm/pipeline.py::_trilha_de_backfill`: checar por par `(de,
      para)`, não só `para`, ao decidir o que já está registrado (→
      Requirement "Trilha de backfill considera origem e destino").

## 4. Testes (incluindo herdado do change 1)

- [ ] 4.1 `tests/test_backfill.py`: reimportar o mesmo dump duas vezes não
      duplica mensagem (→ Requirement "Reimportar dump sem externa_id não
      duplica mensagem").
- [ ] 4.2 `tests/test_backfill.py` (teste de regressão, herdado do change
      `literalidade-e-idempotencia-da-extracao`): `make backfill --forcar`
      executado duas vezes não muda a contagem de `objecoes` — confirma que
      a correção de `gravar_objecao` cobre também o caminho de backfill,
      sem exigir código adicional aqui.
- [ ] 4.3 `tests/test_backfill.py`: histórico de 1000+ mensagens não estoura
      numa única chamada de LLM (→ Requirement "Histórico grande não
      estoura numa chamada só").
- [ ] 4.4 `tests/test_backfill.py`: mensagens com `id` de inserção divergente
      de `enviada_em` são lidas na ordem cronológica correta (→ Requirement
      "Ordem de leitura bate com enviada_em").
- [ ] 4.5 `tests/test_pipeline.py` (ou equivalente): `_trilha_de_backfill`
      não confunde trilhas de mesma `para` mas `de` diferente após backfill
      reexecutado com funil trocado (→ Requirement "Trilha de backfill
      considera origem e destino").
- [ ] 4.6 Suíte completa verde (`make test`).
