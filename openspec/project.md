# camu-crm — contexto do projeto

## Propósito

CRM de conversas de WhatsApp da Camu (peças personalizadas com a foto do pet).
Acompanha as conversas para converter mais vendas nos dois funis — B2C (DM) e
B2B (petshops, consignação) — produzindo uma fila diária de no máximo 10 nomes.

Complementa o sistema de atendimento via LLM já em construção (WhatBot,
Evolution API), que hoje lê e categoriza contatos mas não tem as definições de
funil, temperatura e follow-up.

Fonte de verdade das definições: `docs/04-crm-conversas-definicoes.md`.

## Stack

- **Python 3.12**, sem framework. Pacote único `camucrm/`.
- **Postgres** via `psycopg` + `psycopg_pool` (`camucrm/db.py`), na porta 5433
  (5432 é do WhatBot).
- **LLM:** Gemini (`google-genai`), trocável por um adaptador em `camucrm/llm.py`.
  `CAMU_LLM_PROVIDER=fake` roda tudo offline.
- **WhatsApp:** Evolution API, isolada atrás de `camucrm/transport/`. O padrão
  é `console` (dry-run).
- Infra local: `docker-compose.yml` (só Postgres), `Makefile`.

## Convenções

Ver `CLAUDE.md` — testes (`unittest` puro, E2E único em `tests/test_e2e.py`,
integração fora de `make test`), idioma, camadas e os cinco invariantes.

## Estado da implementação

Ordem de §13 do documento de definições, por dependência:

| # | Passo | Estado |
|---|---|---|
| 1 | Schema e taxonomias | Implementado — `taxonomia.py`, `db.py`. **Falta revisão do Marcos** |
| 2 | Contrato de extração + `fatos` | Implementado — `extraction/` |
| 3 | Backfill com marcação de origem | Implementado — `backfill.py`, `pipeline.py` |
| 4 | Eval de 30 conversas | Máquina pronta (`evaluation/`); **o dataset depende do Marcos** |
| 5 | Regras de estágio e temperatura | Implementado — `rules/` |
| 6 | Fila com o teto de 2 | Implementado — `rules/fila.py` + constraint em `db.py` |
| 7 | Rascunhos com 2 opções | Implementado — `drafts.py` |
| 8 | Painel | Em andamento, antecipado — changes `painel-leitura`, `painel-tempo-real`, `acoes-no-painel`, `rascunho-registrado`, `resumo-conversa`, `analise-desempenho` |
| — | Webhook de ingestão | Implementado nos commits `5a407af` e `d54d432`, sem virar change |

Os passos 1 e 4 são os únicos que exigem o Marcos e não podem ser delegados.
São também os que determinam se o resto vale alguma coisa.

**Nota sobre a antecipação do painel**: §13 lista o painel como último passo,
e §6 supõe histórico acumulado antes dele fazer sentido. A antecipação foi
pedido explícito do usuário, e a tensão com §6/§13 é assumida, não escondida
— cada `proposal.md` dos changes `painel-*` cita essa nota. A mitigação: o
painel não tem nenhuma rota de envio nem segura credencial da Evolution API
(`camucrm.painel` nunca importa `camucrm.transport`) — `camucrm enviar`
continua o único caminho de envio de mensagem.

**Nota sobre `webhook-ingestao`**: implementado sem passar pelo fluxo
OpenSpec (commits `5a407af` "feat: receptor de webhook da Evolution API +
correções de concorrência" e `d54d432` "feat: extração ao receber,
classificação B2B/B2C e correção do estágio-cache"). Registrado aqui para
que a ausência de um change correspondente em `openspec/changes/` não seja
lida como "não feito".

## Decisões que divergem ou estendem o documento

Todas justificadas no ponto de uso; listadas aqui para não se perderem.

- **`fatos.mensagem_em`** (adição ao modelo da §9). O momento em que um fato foi
  *extraído* é sempre posterior a todas as mensagens do bloco que o produziu.
  Usá-lo como "quando o preço foi apresentado" tornaria S5 ("respondeu ao
  preço") e P3 ("msg 2 após autorização") inalcançáveis numa única passada. A
  coluna guarda o momento da mensagem que carrega a evidência, que é o dado
  real e o mesmo no replay e no backfill.
- **Tabela `followups`** (adição, §6). O `CHECK` em
  `conversas.followups_enviados` protege o contador, mas um contador pode ser
  reescrito por engano. Uma linha por follow-up com `numero ∈ {1,2}` e `UNIQUE`
  por conversa torna o terceiro envio irrepresentável, não apenas proibido.
- **Tabela `marcos_manuais`** (adição, §3). S6, P5 e P6 não derivam de fato
  nenhum. Guardar o momento é o que torna P5→P6 (§14) mensurável; guardar quem
  marcou é o que torna a correção rastreável.
- **Trilha de backfill** (`rules/estagio.py::trilha`). Uma conversa histórica
  que recebeu a foto e esfriou deriva direto para `SX`. Gravar só o estado
  final apagaria o fato de ela ter chegado em S2 — e com ele a métrica de
  conversão que §8 diz que o backfill *deve* produzir. O backfill grava a
  trilha inteira; os timestamps continuam sem valor, e é por isso que tudo sai
  com `origem='backfill'`.
- **Temperatura de conversa sem inbound.** §5 define MORNO por "última mensagem
  dele <48h", mas um petshop em P1 nunca falou. O silêncio passa a ser contado
  a partir da nossa primeira mensagem, o que mantém a escala completa em vez de
  deixar B2B recém-abordado fora dela.
- **Reabertura de conversa terminal** (`rules/estagio.py::reabrir`). §3 diz que
  estágio nunca regride, e fechar por timeout de 14 dias é comum. Cliente que
  volta a falar reabre no **maior estágio já alcançado**, não em S1 — tratá-lo
  como lead novo apagaria o compromisso que ele já tinha assumido. Recusa
  explícita é fechamento duro e não reabre por esta via.
- **Envio exige `aprovado_por`** (`transport/base.py`). §1 diz "envio: humano,
  sempre" e §10 proíbe disparo automático. Um parâmetro obrigatório com o nome
  de quem autorizou transforma isso em propriedade de tipo, não em disciplina.
- **Terceira superfície de LLM** (`camucrm/summaries.py`, change
  `resumo-conversa`). §1/`CLAUDE.md` fixam "exatamente dois lugares" para o
  LLM. `extraction/` alimenta `fatos`, que alimenta as regras — corretude
  estrutural, um erro vira estágio errado sistematicamente. `drafts.py` e
  `summaries.py` são terminais: a saída não retroalimenta `fatos` nem regra
  nenhuma, um erro custa uma leitura ruim, nunca um estágio errado. A regra
  original permanece íntegra — se `rules/` importar `llm`, a arquitetura
  vazou, e isso é testado por `ast.parse`, não por convenção.
- **Tabelas `rascunhos` e `resumos_conversa`** (adições ao modelo da §9,
  changes `rascunho-registrado` e `resumo-conversa`), na mesma forma de
  `followups` e `marcos_manuais` acima. `rascunhos` registra o que hoje
  `drafts.gerar` produz e descarta — sem isso, não há como medir se a opção 1
  ou a opção 2 converte melhor. `resumos_conversa` é folha do grafo: nenhuma
  regra a lê, e apagar a tabela inteira não muda estágio, temperatura nem
  fila de nenhuma conversa — existe só para leitura humana mais rápida.

## Correções pendentes — auditoria completa do pipeline (2026-08)

Antes de operar com petshops e consumidores de verdade, o usuário pediu uma
auditoria completa do pipeline (recepção → ingestão → extração → regras →
fila/rascunho → painel → purga) para garantir que nenhuma mensagem some,
nenhum dado seja corrompido e a tela nunca minta sobre o estado real do
banco. Três agentes de exploração cobriram o pipeline inteiro por leitura
completa (não amostra); os achados mais críticos foram confirmados por
leitura direta adicional, não só relato de agente. O resultado são 9
changes novos, agrupados por causa raiz, na ordem de prioridade/dependência
abaixo — mais a ampliação de `mensagem-sem-texto-preservada` (já existente)
para cobrir um segundo caso de mensagem descartada, encontrado na mesma
auditoria.

| # | Change | Severidade | Por que existe | O que NÃO resolve |
|---|---|---|---|---|
| 1 | `literalidade-e-idempotencia-da-extracao` | 🔴 crítico | `_fold` colapsa o `\n` que protege a fronteira entre mensagens (contradiz o próprio docstring de `build_corpus`); evidência não distingue lado (cliente vs. Camu); watermark de extração sem `GREATEST`; `gravar_objecao` sem proteção de idempotência | Não mexe em `rules/`; não cobre chunking de histórico grande (isso é o item 9) |
| 2 | `mensagem-sem-texto-preservada` (ampliação) | 🔴 crítico | Já cobria áudio/figurinha/contato/localização; auditoria ampliou para `ephemeralMessage`/`viewOnceMessage`/`viewOnceMessageV2` (texto puro, não só mídia) e `deviceSentMessage` (eco de outro dispositivo) | `editedMessage`/`protocolMessage` (REVOKE) continuam fora — ver backlog abaixo |
| 3 | `identificacao-e-relogio-confiaveis` | 🔴/🟡 | `@lid`/`@broadcast`/`status@broadcast` não filtrados (risco de contato fantasma ou histórico splitado); timestamp futuro "trava" `ultimo_inbound`/`ultimo_outbound` para sempre | Não faz reconciliação de LID↔PN já existente (ver backlog) |
| 4 | `ingestao-a-prova-de-falha` | 🔴 estrutural | `webhook.get_db()` sem `ensure_schema()` no boot; exceção em `ingerir()` engolida sem fila de reprocessamento; sem transação única contato→conversa→mensagem; dedupe parcial; `cmd_ingerir` sem `--transporte` finge sucesso | Não implementa fila externa (Redis/SQS) — staging fica em Postgres, ver `design.md` do change |
| 5 | `estagio-reabertura-manual-e-relogio` | 🔴/🟡 | Falso positivo de `recusa_explicita` é irreversível por design, hoje sem nenhum caminho de recuperação; `reabrir()` não valida sozinha; `mudar_funil_conversa` lê estágio cache em vez de reconciliar; "avançou hoje" classifica QUENTE mesmo quando quem avançou foi a Camu | Não enfraquece a prioridade de `recusa_explicita` como primeira condição verificada — só adiciona uma exceção explícita e registrada |
| 6 | `painel-mensagens-recentes-e-acoes-seguras` | 🔴 | `listar_mensagens_registradas` mostra as mensagens MAIS ANTIGAS em conversa >200 mensagens; kanban/fila cortam sem expor `total`; ações concorrentes sem trava podem corromper `marcos_manuais`; `vincular_rascunho` sem `WHERE mensagem_id IS NULL` | Não implementa paginação completa de kanban/fila além de expor `total` |
| 7 | `purga-cobre-rascunhos-sem-vinculo` | 🔴 conformidade §12 | Purga nunca anonimiza rascunho com `mensagem_id IS NULL` — a maioria dos rascunhos gerados sobrevive com texto pessoal em claro, contrariando a própria docstring da função | Não muda a política de quando purgar, só o alcance da anonimização já prometida |
| 8 | `backfill-seguro-para-reexecucao` | 🟡 | Reimportar dump sem `externa_id` duplica mensagem; sem chunking para histórico grande; ordem de `id` vs. `enviada_em` pode divergir; `_trilha_de_backfill` não considera origem | **Bloqueado por `literalidade-e-idempotencia-da-extracao`** — a idempotência de `gravar_objecao` é resolvida lá; este change só adiciona teste de regressão do caminho de backfill |

Duas features entram como changes à parte, fora desta tabela de correções,
mas com a mesma disciplina de `proposal.md`/`tasks.md`/`spec.md`:

- **`contatos-de-teste-isolados`** — feature pedida durante o planejamento:
  contato marcado como teste (manual, nunca inferido) some do
  kanban/fila/conversas/métricas por padrão, só aparecendo quando "modo
  teste" é ativado no painel. Sem dependência das correções acima; faz
  sentido entrar antes do item 6 (`painel-mensagens-recentes-e-acoes-
  seguras`) — os dois tocam as mesmas rotas de leitura do painel.
- **`ground-truth-no-painel`** — substitui o candidato `ground-truth-marcos`
  abaixo (ver nota na seção seguinte). Rotular pelo painel em vez de editar
  `data/eval/conversas.jsonl` à mão, puxando mensagens reais de uma conversa
  existente. Pode entrar logo após o item 1 desta tabela — desbloquear a
  métrica de acurácia é o que dá sentido a rodar o eval com confiança depois
  das correções de `_fold`/corpus por direção.

Ordem de implementação recomendada: 1 → 2 → 3 → 4 antes de liberar tráfego
real de produção (protegem contra dado errado/perdido desde a primeira
mensagem); 5 → 6 → 7 podem seguir logo depois, já com o sistema em operação
real; 8 é o de menor urgência (só importa quando um backfill for de fato
reexecutado). As duas features podem entrar em paralelo, nos pontos
indicados acima.

### Itens deliberadamente fora de escopo desta auditoria (backlog, não silenciados)

- **`editedMessage`/`protocolMessage` (REVOKE) ignorados** — edição/
  apagamento do cliente não reflete no CRM. Impacto: retenção maior que o
  esperado, não perda de dado. Sem change dedicado por enquanto.
- **Fragilidade de `rank_estagio`** (`rules/estagio.py`) buscar em ambos os
  dicionários de funil sem tomar `funil` como parâmetro — nunca exercitada
  na prática porque rótulos `S*`/`P*` não colidem. Registrado como nota de
  design, não como bug ativo.
- **Payload em lote da Evolution API** — não é item à parte; é tarefa de
  investigação dentro do change `ingestao-a-prova-de-falha` (confirmar
  contra documentação/comportamento real antes de decidir se vale
  desmembrar).

## Próximos changes candidatos

`midia-foto-pet` continua à frente **em valor** — não depende do painel, e
segue bloqueando afirmações mais fortes do que o painel pretende fazer. O
custo aceito de o painel ter sido antecipado (ver nota acima) é registrado
de forma verificável, não só prometido: **a tela `/funciona` (change
`analise-desempenho`) fica proibida de afirmar qualquer coisa sobre
acurácia de extração até `ground-truth-no-painel` entrar** — conversão de
estágio e tempo por estágio podem ser exibidos, porque não dependem do
eval.

`ground-truth-marcos` não é mais candidato — o pedido do usuário durante o
planejamento da auditoria de 2026-08 (rotular pelo painel em vez de editar
o JSONL à mão) transformou esse candidato num change com desenho e
implementação próprios: **`ground-truth-no-painel`**, na tabela acima. Deixa
de ser pendência externa manual (esperar o Marcos rotular 30 conversas num
editor de texto) e vira um fluxo do próprio painel.

Nesta ordem de dependência:

1. **`mensagem-sem-texto-preservada`** — já ativo (ver tabela acima),
   ampliado pela auditoria de 2026-08.
2. **`ground-truth-no-painel`** — as 30 conversas rotuladas (§7), agora
   pelo painel. Bloqueia qualquer afirmação sobre qualidade de extração,
   incluindo a tela `/funciona` do painel.
3. **`midia-foto-pet`** — S2 é o estágio-chave e hoje depende de o cliente
   escrever algo junto da foto. Tratar mídia traz retenção e LGPD (§12) junto,
   e por isso é capability própria.

O painel não é mais candidato — é change ativo, antecipado. Ver os seis
changes `painel-leitura`, `painel-tempo-real`, `acoes-no-painel`,
`rascunho-registrado`, `resumo-conversa` e `analise-desempenho` em
`openspec/changes/`.
