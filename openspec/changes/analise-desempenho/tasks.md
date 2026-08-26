# Tasks — tela de análise de desempenho do funil

## 1. Implementação

- [ ] 1.1 `camucrm/metrics.py`: `AMOSTRA_MINIMA` constante única (→
      Requirement "Porcentagem some abaixo da amostra mínima").
- [ ] 1.2 `camucrm/metrics.py`/`camucrm/db.py`: conversão de todo par
      adjacente de estágio.
- [ ] 1.3 `camucrm/db.py`: consulta "onde as conversas morrem" (maior rank
      alcançado em conversas com `resultado IS NOT NULL`).
- [ ] 1.4 `camucrm/metrics.py` (verificar se já existe `tempo_por_estagio` —
      reusar): garantir filtro `origem != 'backfill'` no SQL (invariante 4
      do `CLAUDE.md` — o filtro mora no SQL, não em checagem opcional) (→
      Requirement "Backfill fora de métrica de tempo").
- [ ] 1.5 `camucrm/db.py`: objeção por estágio, usando `objecoes.estagio`
      (hoje descartado por `distribuicao_objecoes`).
- [ ] 1.6 `camucrm/db.py`: padrão de correções (contagem por par de/para de
      funil).
- [ ] 1.7 `camucrm/db.py`: retorno por número de follow-up (1º vs 2º
      toque).
- [ ] 1.8 `camucrm/db.py`: bloco de rascunhos — opção 1 vs opção 2 com
      avanço de estágio em 72h; aceito sem edição vs editado; viés de
      posição — bloqueado com contador enquanto `n` de envios vinculados
      < limiar (ex.: 30) (→ Requirement "Bloco de rascunhos nasce
      bloqueado").
- [ ] 1.9 `camucrm/painel/api.py`: `GET /api/analise` reunindo as consultas
      acima, cada porcentagem acompanhada de `n`, suprimida ("sem amostra")
      quando `n < AMOSTRA_MINIMA` (→ Requirement "Toda porcentagem vem com
      n"; → Requirement "Porcentagem some abaixo da amostra mínima").
- [ ] 1.10 `camucrm/painel/static/*`: tela `/funciona` — tabelas HTML,
      barras em CSS, sem biblioteca de gráfico, sem linha de tendência; nota
      fixa de que a tela não afirma acurácia de extração antes de
      `ground-truth-marcos` (→ Requirement "Sem linha de tendência"; →
      Requirement "Sem afirmação de acurácia de extração antes do ground
      truth").

## 2. Testes

- [ ] 2.1 `tests/test_painel_views.py` (estendido): porcentagem some quando
      `n < AMOSTRA_MINIMA`; `n` sempre presente quando exibida (→
      Requirement "Toda porcentagem vem com n"; → Requirement "Porcentagem
      some abaixo da amostra mínima").
- [ ] 2.2 `tests/integration/`: cada agregação nova conferida à mão contra
      dados semeados (corretude de SQL não é provável por fake).
- [ ] 2.3 Suíte completa verde.
