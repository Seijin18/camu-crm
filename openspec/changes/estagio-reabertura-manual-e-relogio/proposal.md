# Reabertura manual de estágio e relógio de temperatura confiável

## Why

Auditoria completa de `rules/estagio.py`, `rules/temperatura.py` e
`rules/sinais.py` confirmou o pior caso que a §7 do documento descreve: um
falso positivo de `recusa_explicita` é **irreversível por design**. É a
primeira condição verificada em `_derive_b2c`/`_derive_b2b`, fatos são
monotônicos (nunca revertidos automaticamente), e o próprio mecanismo de
reabertura por timeout (`pipeline.py:206`) exclui explicitamente esse
caminho ("recusa não reabre") — decisão correta para recusa real, mas hoje
não existe NENHUMA forma de recuperação, nem manual, para o caso em que a
extração errou. Um lead quente abandonado por engano de LLM fica preso para
sempre.

A mesma auditoria encontrou três problemas correlatos, menores mas reais:

- `reabrir()` não valida sozinha por que a conversa é terminal — a garantia
  "recusa não reabre" vive inteira no chamador (`pipeline.py`), não na
  função. Um novo chamador futuro (painel, `acoes.py`) que não replicar o
  guard reabre uma recusa explícita silenciosamente.
- `acoes.mudar_funil_conversa` (`acoes.py:160-164`) lê `conversas.estagio`
  (coluna cache) direto, ao contrário de `pipeline.recalcular`, que sempre
  reconcilia contra o histórico (`eventos_estagio`) antes de decidir. Se as
  duas fontes divergirem — risco real via a regressão de watermark corrigida
  em `literalidade-e-idempotencia-da-extracao` — o evento gravado por essa
  função carrega um `de` que não bate com a verdade.
- "Avançou de estágio hoje" classifica QUENTE mesmo quando quem avançou o
  estágio foi a própria Camu (prévia enviada, preço apresentado, proposta
  B2B) — contraria a premissa central da §5 ("reciprocidade, não atividade
  nossa"). Não afeta a fila (protegida por `bola_com`), mas a etiqueta
  exibida no painel fica enganosa.
- Sem clamping de `enviada_em` em `rules/sinais.py`: mensagem com timestamp
  futuro vira "a última" na ordenação e não é superada por mensagens reais
  subsequentes, congelando `bola_com` errado — mesma classe de problema do
  clamp de `identificacao-e-relogio-confiaveis`, mas na camada de regras em
  vez de recepção.

Este change mexe diretamente no invariante §3 ("estágio nunca regride") e na
decisão já registrada em `project.md` ("recusa explícita é fechamento duro e
não reabre por esta via") — daí o `design.md`.

## What Changes

- **Mecanismo de reabertura manual** (decisão já tomada com o usuário): uma
  ação nova, via `correcoes` (campo `"recusa_explicita"`, registrando
  antes/depois da desconsideração), que `rules/estagio.py::_derive_b2c`/
  `_derive_b2b` passam a consultar para ignorar um `recusa_explicita=true`
  marcado como desconsiderado, reabrindo no maior estágio já alcançado
  (mesmo padrão de `reabrir()`, nunca em S1/P0). Exposta na CLI (comando
  novo, ver `design.md` para o motivo de não reaproveitar `camucrm
  corrigir`) e no painel (botão no detalhe da conversa, exigindo `por`).
- `reabrir()` ganha checagem própria (não só do chamador) de que a conversa
  não está terminal por `recusa_explicita` não-desconsiderada — estrutural,
  não só documentado em comentário.
- `acoes.mudar_funil_conversa`: usar a mesma reconciliação contra o
  histórico que `pipeline.recalcular`/`_avanco_ao_vivo` já usam, em vez de
  ler `conversas.estagio` cru.
- `rules/temperatura.py`: distinguir avanço de estágio causado pelo cliente
  do causado pela Camu (mapa já implícito em `_derive_b2c`/`_derive_b2b`,
  formalizado como metadado do `Transicao`/`Derivacao`) antes de classificar
  QUENTE por "avançou hoje".
- `rules/sinais.py`: clamping de `enviada_em` (mesma política de
  `identificacao-e-relogio-confiaveis` — `min(timestamp, agora())`) antes de
  decidir qual é "a última" mensagem para `bola_com`.

## Impact

- Specs afetadas: `estagio-reabertura-manual-e-relogio` (nova)
- Código alterado: `camucrm/rules/estagio.py` (`_derive_b2c`, `_derive_b2b`,
  `reabrir`), `camucrm/rules/temperatura.py`, `camucrm/rules/sinais.py`,
  `camucrm/db.py` (gravação da desconsideração em `correcoes`),
  `camucrm/cli.py` (comando novo), `camucrm/acoes.py`
  (`mudar_funil_conversa`), `camucrm/painel/api.py` (rota nova),
  `camucrm/painel/static/*` (botão no detalhe)
- Testes alterados: `tests/test_rules_estagio.py` (recusa desconsiderada
  permite avançar de novo; `reabrir()` recusa sozinha reabrir recusa não-
  desconsiderada), `tests/test_acoes.py` (`mudar_funil_conversa` com
  `conversas.estagio` divergente do histórico grava o `de` correto),
  `tests/test_rules_temperatura.py` (avanço 100% por outbound não vira
  QUENTE), `tests/test_e2e.py` (estendido com o ciclo completo de reabertura
  manual)
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- Reabertura automática de recusa explícita por qualquer heurística — a
  reabertura é sempre uma ação humana explícita, nunca inferida.
- Mudar a regra "recusa explícita é primeira condição verificada" — o change
  adiciona uma exceção explícita e registrada (desconsideração), não
  enfraquece a prioridade da checagem em si.
