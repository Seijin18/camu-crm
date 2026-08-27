## Context

A cadeia recepção→ingestão hoje trata "falha de processamento" e "evento
benigno" com o mesmo resultado observável (nada acontece, `2xx` devolvido à
Evolution API). Isso é correto para o segundo caso e catastrófico para o
primeiro: uma falha real de processamento não tem hoje NENHUM jeito de ser
detectada depois do fato, porque nada do payload original sobrevive além de
uma linha de log.

## Decisão: staging do payload bruto antes de qualquer parsing

Tabela nova `eventos_recebidos_bruto`:

- `id` serial, `recebido_em timestamptz default now()`.
- `payload jsonb` — o corpo cru do webhook, sem qualquer transformação.
- `processado boolean default false`.
- `processado_em timestamptz null`.
- `erro text null` — mensagem da exceção, se a tentativa de processar
  falhou.
- `tentativas int default 0`.

Fluxo: `webhook.py` grava uma linha aqui **antes** de chamar `ingerir()`.
Se `ingerir()` levantar qualquer exceção, a linha permanece com
`processado=false` e `erro` preenchido — nada além do log é necessário para
saber que algo falhou, porque o dado para reprocessar já está ali. Em caso
de sucesso, `processado=true` e `processado_em` são gravados.

Por que uma tabela e não um arquivo/fila externa: mantém a mesma stack
(Postgres via `psycopg`), sem introduzir uma dependência nova (Redis, SQS)
para um volume que, no estágio atual do produto, não justifica
infraestrutura de fila dedicada. Se o volume crescer a ponto de precisar de
uma fila de verdade, esta tabela ainda serve como staging/log de auditoria,
não precisa ser descartada.

## Decisão: retenção da caixa de reprocessamento é de curto prazo

`eventos_recebidos_bruto` NÃO é histórico permanente. Reter permanentemente
todo payload bruto duplicaria `mensagens` num formato sem os invariantes de
`dedupe`/schema e cresceria sem limite. Decisão: reter só os últimos N dias
(constante nova, ex. `RETENCAO_EVENTOS_BRUTOS_DIAS`, sugestão inicial de 14
dias — mesmo horizonte de "reprocessamento operacional plausível" usado em
outras partes do sistema) de eventos **já processados com sucesso**; eventos
com falha (`processado=false`) NUNCA são apagados automaticamente até serem
resolvidos (reprocessados com sucesso ou descartados manualmente), porque
apagar um evento não processado seria repetir exatamente o bug que este
change corrige.

A purga desta tabela é um job separado (reaproveita o padrão de
`purgar_mensagens_antigas`, mas não anonimiza — deleta linhas processadas
antigas inteiras, porque não há razão de negócio para reter o payload cru
depois que o processamento já terminou com sucesso e a mensagem real já
está em `mensagens`).

## Decisão: `reprocessar-falhas` é manual, não automático

Um cron automático que reprocessa falhas silenciosamente esconderia
exatamente o sinal que este change existe para expor — se `ingerir()` falha
de forma recorrente (ex. bug real de parsing), um reprocessamento automático
tentaria e falharia de novo silenciosamente, sem que ninguém soubesse.
`camucrm reprocessar-falhas` é comando manual: lista o que falhou, tenta
reingerir, relata sucesso/falha por evento. Automação futura, se desejada,
fica fora de escopo desta primeira versão.

## Decisão: dedupe com fallback de hash para evento sem `key.id`

O índice único hoje é parcial: `WHERE externa_id IS NOT NULL`. Para eventos
sem `key.id` (payload malformado ou de uma integração que não preenche esse
campo), a estratégia é computar um hash estável do payload cru (ex.
`md5(payload::text)`) como um `externa_id` sintético quando o campo real
está ausente — mesma ideia que `backfill-seguro-para-reexecucao` usa para
mensagens sem id de origem. Isso estende a proteção de dedupe sem exigir dois
índices/dois caminhos de código.

## Alternativa descartada

Fila externa (Redis Streams, SQS) para o staging de eventos brutos —
rejeitada por introduzir uma dependência de infraestrutura nova sem que o
volume atual do produto justifique; Postgres já é a única dependência de
dado do projeto (`CLAUDE.md`: "Postgres via psycopg + psycopg_pool") e a
tabela de staging preserva essa propriedade.
