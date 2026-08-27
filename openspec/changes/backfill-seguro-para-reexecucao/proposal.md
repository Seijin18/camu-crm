# Backfill seguro para reexecução

## Why

Auditoria completa de `backfill.py` encontrou uma série de problemas que só
importam quando um backfill é de fato reexecutado — cenário real (importar
um dump atualizado, corrigir um erro e reimportar, rodar `make backfill`
mais de uma vez), mas menos urgente que os problemas de tráfego ao vivo
cobertos pelos changes 1–4 desta auditoria:

1. Reimportar um dump sem `externa_id` duplica mensagens a cada rodada —
   já autodocumentado no próprio código.
2. `forcar=True` duplica `objecoes` a cada reexecução de `make backfill` —
   mesma causa raiz do problema de idempotência de `gravar_objecao`
   corrigido em `literalidade-e-idempotencia-da-extracao`. Este change
   depende daquele para a parte de objeções — não deveria exigir código
   adicional aqui, só teste de regressão específico do caminho de backfill.
3. Sem chunking para históricos grandes — uma conversa longa vira uma única
   chamada de LLM, que pode estourar contexto (extração vazia, silenciosa)
   ou degradar recall.
4. Ordem de `id` (inserção) vs. `enviada_em` (real) podem divergir em dumps
   não estritamente ordenados/multi-fonte — o LLM pode ler a conversa fora
   de ordem cronológica.
5. `_trilha_de_backfill` pula por estágio de destino (`para`) sem considerar
   a origem (`de`) — canto raro (funil trocado + backfill reexecutado), de
   severidade baixa.

## What Changes

- `backfill.py::importar_conversas`: gerar um `externa_id` sintético
  estável (ex. hash de contato+texto+timestamp) para mensagens sem id,
  tornando reimportação idempotente — mesma estratégia de fallback de hash
  usada em `ingestao-a-prova-de-falha` para dedupe de evento sem
  `externa_id`.
- Confirmar (teste de regressão, não código adicional) que a correção de
  `gravar_objecao` do change `literalidade-e-idempotencia-da-extracao`
  também resolve a duplicação em `extrair_historico --forcar`.
- `extractor.py`/`backfill.py`: chunking para históricos grandes em vez de
  uma chamada monolítica de LLM — divide o histórico em blocos de tamanho
  administrável, mantendo a ordem cronológica entre blocos.
- `db.py::mensagens_novas` (ou o consumo em `Extrator`): ordenar por
  `enviada_em` (não só `id`) para bater com o que `construir_sinais` já
  usa — evita que o LLM leia a conversa fora de ordem cronológica quando
  `id`/`enviada_em` divergem.
- `pipeline.py::_trilha_de_backfill`: checar por par `(de, para)`, não só
  `para`, ao decidir o que já está registrado.

## Impact

- Specs afetadas: `backfill-seguro-para-reexecucao` (nova)
- Código alterado: `camucrm/backfill.py` (`importar_conversas`, chunking),
  `camucrm/extraction/extractor.py` (chunking), `camucrm/db.py`
  (`mensagens_novas` ou equivalente — ordenação por `enviada_em`),
  `camucrm/pipeline.py` (`_trilha_de_backfill`)
- Testes alterados: `tests/test_backfill.py` (reimportar o mesmo dump duas
  vezes não duplica mensagem; histórico de 1000+ mensagens não estoura numa
  chamada só; ordenação por `enviada_em` quando diverge de `id`),
  `tests/test_e2e.py` ou teste dedicado (herdado do change 1: `make
  backfill --forcar` duas vezes não muda contagem de objeções)
- Bloqueado por: `literalidade-e-idempotencia-da-extracao` (a idempotência
  de `gravar_objecao` é resolvida lá; este change só adiciona o teste de
  regressão do caminho de backfill)
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- Corrigir `_trilha_de_backfill` para o canto raro de funil trocado +
  backfill reexecutado além da checagem por par `(de, para)` — qualquer
  refinamento adicional além dessa checagem fica para uma iteração
  futura, se o cenário se mostrar mais que teórico.
