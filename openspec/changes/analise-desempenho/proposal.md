# Tela de análise de desempenho do funil

## Why

Hoje não há como responder "o que está funcionando" além dos três números da
§14. Isso é adiável em geral — antecipar métricas é a decoração que §7
condena — mas parte é respondível hoje sem depender de `rascunhos`
acumular: métricas determinísticas de estágio, objeção e correção. A parte
de A/B de rascunho depende do change `rascunho-registrado` já estar em
produção acumulando linhas o suficiente.

## What Changes

- Consultas agregadas novas, sem duplicar cálculo de regra:
  - Os três números da §14 (`metrics.metricas_chave`).
  - Conversão de todo par adjacente de estágio.
  - Onde as conversas morrem (maior rank de estágio alcançado, entre
    conversas com `resultado IS NOT NULL`).
  - Tempo mediano por estágio (só `live`, filtro `origem != 'backfill'` no
    SQL — invariante 4 do `CLAUDE.md`; reusar `metrics.tempo_por_estagio`
    se já cobre o filtro).
  - Objeção por estágio, usando `objecoes.estagio` (hoje coletado mas
    descartado por `distribuicao_objecoes`).
  - Saúde da taxonomia (`metrics.saude_taxonomia`).
  - Padrão de correções — contagem por par de/para de funil.
  - Retorno por número de follow-up (1º vs 2º toque).
- Bloco de rascunhos (depende do change `rascunho-registrado`): opção 1 vs
  opção 2 na mesma geração, com avanço de estágio em 72h; aceito sem edição
  vs editado; viés de posição. Nasce bloqueado com um contador ("precisa de
  30 envios vinculados; hoje há N") em vez de gráfico vazio, enquanto a
  amostra não atinge o limiar.
- `AMOSTRA_MINIMA` constante única em `metrics.py`. Toda porcentagem exibida
  sai com `n` ao lado; **nunca é exibida quando `n < AMOSTRA_MINIMA`**
  ("sem amostra" aparece no lugar do número).
- Nunca desenhar linha de tendência — tendência sobre poucos pontos é
  exatamente o modo de falha que §7 nomeia.
- Tabelas HTML e barras em CSS puro, sem biblioteca de gráfico.
- Tela nova `/funciona` no painel, servida pela mesma app estática.
- **Restrição herdada de `openspec/project.md`**: `/funciona` fica proibida
  de afirmar qualquer coisa sobre acurácia de extração até
  `ground-truth-marcos` entrar. Conversão e tempo por estágio podem ser
  exibidos porque não dependem do eval.

## Impact

- Specs afetadas: `analise-desempenho` (nova)
- Código alterado: `camucrm/metrics.py` (`AMOSTRA_MINIMA`, consultas
  agregadas novas), `camucrm/db.py` (SQL agregado), `camucrm/painel/api.py`
  (`GET /api/analise`), `camucrm/painel/views.py`, `camucrm/painel/static/*`
  (tela `/funciona`)
- Testes alterados: `tests/test_painel_views.py` (estendido — supressão por
  `AMOSTRA_MINIMA`), `tests/integration/` (agregações conferidas à mão)
- Bloqueado por: `rascunho-registrado`
- Bloqueia: —

## Fora de escopo (decisão explícita)

- Qualquer afirmação sobre acurácia de extração antes de
  `ground-truth-marcos` existir.
- Linha de tendência em qualquer série temporal.
- Biblioteca de gráfico ou qualquer build step.
