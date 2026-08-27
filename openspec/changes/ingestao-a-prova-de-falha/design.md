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

## Investigação: `messages.upsert` pode chegar em lote?

Tarefa 4.1 de `tasks.md` — verificação antes de qualquer código, não
implementação às cegas.

**Conclusão: não há evidência de que a Evolution API entregue múltiplas
mensagens num único evento de webhook `messages.upsert`, e nenhuma mudança
de código foi feita por causa disso.**

O que foi checado (agente sem acesso a uma instância real da Evolution API
para reproduzir tráfego ao vivo — verificação por documentação oficial e
discussão pública, não por teste de carga):

- A documentação oficial de webhooks da Evolution API mostra o payload de
  `MESSAGES_UPSERT` com `data` como um objeto único (`key`, `pushName`,
  `message`, `messageType`, `messageTimestamp`, ...), nunca como array. Nenhum
  exemplo da documentação mostra lote.
- Busca nas issues públicas do repositório (`evolution-foundation/evolution-
  api`) por relatos de payload em lote não encontrou nenhum caso — as
  issues sobre `messages.upsert` discutem discrepância de nome de evento
  (`send.message` vs `messages.upsert`) e comportamento de eco, não formato
  de lote.
- Baileys (a biblioteca que a Evolution API usa por baixo) emite
  internamente `messages.upsert` com um array de mensagens — mas a camada de
  webhook da Evolution API, pelo que a documentação e as issues mostram,
  despacha uma chamada HTTP por mensagem, já desembrulhada.

**Por que isso não vira um teste automatizado**: a conclusão vem de
documentação e de discussão pública, não de uma instância real gerando
tráfego sob volume — não há como provar "nunca acontece" sem acesso a uma
instância de produção sob carga real. O comportameto defensivo já existente
em `EvolutionTransporte.receber` (`if not isinstance(dados, Mapping): return
None`) já cobre o pior caso SE a suposição acima se revelar errada no
futuro: um payload em lote não creditaria dados errados a um contato nem
duplicaria nada — cairia no mesmo caminho de "evento ignorado" que qualquer
payload malformado, silenciosamente, sem side-effect incorreto. Se um caso
real de lote aparecer em produção (visível pelo log "Webhook com corpo
não-JSON" nunca disparando, mas mensagens somem sem aparecer em
`eventos_recebidos_bruto` — o que este próprio change torna detectável,
porque agora HÁ staging do payload cru), a correção natural seria desmembrar
a lista em `EvolutionTransporte.receber` antes de devolver `EventoRecebido`,
ou (mais simples) fazer o webhook chamar `ingerir()` uma vez por item da
lista quando `data` for array. Ver comentário em
`camucrm/transport/evolution.py::receber`.

## Alternativa descartada

Fila externa (Redis Streams, SQS) para o staging de eventos brutos —
rejeitada por introduzir uma dependência de infraestrutura nova sem que o
volume atual do produto justifique; Postgres já é a única dependência de
dado do projeto (`CLAUDE.md`: "Postgres via psycopg + psycopg_pool") e a
tabela de staging preserva essa propriedade.
