# Tasks — literalidade e idempotência da extração

## 1. Implementação — corpus e fronteira de mensagem

- [ ] 1.1 `camucrm/extraction/contract.py::build_corpus`: trocar o separador
      de junção de mensagens por um caractere fora de `\s` (ex. `"\x00"`) que
      sobrevive a `_fold` (→ Requirement "Fronteira entre mensagens
      sobrevive à normalização").
- [ ] 1.2 `camucrm/extraction/contract.py::_fold`: confirmar (com teste) que
      o novo separador não é colapsado pela normalização de espaço (→
      Requirement "Fronteira entre mensagens sobrevive à normalização").

## 2. Implementação — direção da evidência

- [ ] 2.1 `camucrm/extraction/contract.py`/`extractor.py`: definir o mapa
      campo → direção exigida (CLIENTE: `foto_pet_recebida`,
      `intencao_compra_explicita`, `recusa_explicita`,
      `autorizou_envio_material`, `visita_aceita`; CAMU: `preco_apresentado`,
      `previa_enviada`) (→ Requirement "Fato de cliente exige evidência do
      lado do cliente").
- [ ] 2.2 Verificar a evidência apenas contra o corpus do lado exigido —
      trecho que só existe do lado errado não valida o campo (→ Requirement
      "Fato de cliente exige evidência do lado do cliente").

## 3. Implementação — idempotência

- [ ] 3.1 `camucrm/db.py`: escrita de `ultima_mensagem_processada_id` usa
      `GREATEST` (mesmo padrão de `ultimo_inbound`/`ultimo_outbound`) (→
      Requirement "Watermark de extração nunca regride").
- [ ] 3.2 `camucrm/db.py::gravar_objecao`: índice único em `(conversa_id,
      categoria, estagio, md5(coalesce(trecho, '')))` com `ON CONFLICT DO
      NOTHING` (→ Requirement "Gravação de objeção é idempotente").

## 4. Testes

- [ ] 4.1 Teste de fronteira: evidência que só existe fundida por causa do
      colapso de `\n` (texto que atravessa duas mensagens) não valida
      nenhum fato (→ Requirement "Fronteira entre mensagens sobrevive à
      normalização").
- [ ] 4.2 Teste de direção: evidência de uma mensagem `out` (Camu) não valida
      um fato que exige fala do cliente (→ Requirement "Fato de cliente
      exige evidência do lado do cliente").
- [ ] 4.3 Teste de watermark: escrita concorrente de
      `ultima_mensagem_processada_id` com valor menor que o atual não
      regride a coluna (→ Requirement "Watermark de extração nunca
      regride").
- [ ] 4.4 `tests/integration/`: reprocessamento concorrente (ou chamada
      dupla) de `gravar_objecao` com os mesmos dados não duplica a linha (→
      Requirement "Gravação de objeção é idempotente").
- [ ] 4.5 `make backfill --forcar` executado duas vezes não muda a contagem
      de `objecoes` (→ Requirement "Gravação de objeção é idempotente").
- [ ] 4.6 Suíte completa verde (unitária sem Postgres; integração à parte
      com Postgres).
