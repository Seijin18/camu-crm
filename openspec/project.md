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
- **Base legal para prospecção B2B fria** (change `prospeccao-b2b-shortlist`,
  tabela `prospeccoes`, fora do modelo de conversa da §9 de propósito). §12
  registra que nenhuma base legal cobre "lista fria comprada ou raspada" —
  decisão do usuário: **legítimo interesse (art. 10, LGPD) cobre este caso
  específico** porque é pessoa jurídica (petshop), contato comercial B2B
  (proposta de parceria), com dado já público (telefone comercial, endereço).
  **Não se estende a qualquer lista de consumidor (B2C) raspada** — essa
  continua sem base nenhuma, e a capability é estritamente B2B por desenho
  (sem campo que aceite funil B2C). Decisão registrada aqui e no `design.md`
  do change, não escondida.
- **Prospecção não envia mensagem** (mesmo change) — **decisão REVERTIDA em
  2026-08-28** pelo change `envio-prospeccao-pela-evolution-api`, a pedido
  explícito do usuário. Registro original preservado abaixo por histórico;
  ver o item seguinte para o estado atual.
- **Envio de prospecção pela Evolution API** (change
  `envio-prospeccao-pela-evolution-api`, reverte a decisão acima). A aba de
  prospecção ganhou um botão que envia de fato, sem sair do painel: popup
  com telefone e mensagem pré-preenchidos e editáveis, "Enviar" chama
  `POST /prospeccao/{id}/enviar`. A garantia "`camucrm.painel` nunca importa
  `camucrm.transport`" deixou de valer para o pacote inteiro e passou a
  valer com uma exceção nomeada: só `camucrm/painel/envio.py` importa
  transporte, e é o único módulo autorizado a fazê-lo (provado por AST em
  `tests/test_painel_api.py`, não por convenção). O que continua intacto é
  a garantia que importa — §1/§10, envio é sempre humano: `enviar_prospeccao`
  recusa (422) qualquer chamada sem `aprovado_por` preenchido, antes de
  tocar rede; o que muda é só ONDE esse clique de aprovação acontece. O
  link `wa.me` continua existindo — os dois caminhos coexistem, o operador
  escolhe. Consequência aceita: o processo do painel passa a carregar
  `EVOLUTION_API_KEY` quando esse botão é usado (antes, ausente de
  propósito). Colunas novas `prospeccoes.enviado_em`/`enviado_por`/
  `enviado_erro`, distintas de `aberto_em`/`aberto_por` (intenção de clique
  no link, sem confirmação) — estas são confirmação real, só gravadas
  depois que a Evolution API respondeu.

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

## Auditoria de custo de LLM (2026-08-27)

Pedido do usuário depois de observar picos de uso de tokens. Revisão dos três
pontos onde o LLM é chamado (`extraction/`, `drafts.py`, `summaries.py`) e de
tudo que os aciona (webhook, CLI, painel, backfill, eval). Dois problemas
concretos, cada um virando change próprio:

| Change | Achado | O que NÃO resolve |
|---|---|---|
| `backfill-cobertura-por-prompt` | `extrair_historico`/`camucrm extrair --forcar` sempre relê a conversa inteira (`forcar=True` ignora o watermark) — reexecutar `make backfill` custa 100% da base aberta de novo, mesmo sem o prompt ter mudado. Motivador direto do change `backfill-seguro-para-reexecucao`, que tornou reexecutar seguro, mas não barato | Não muda o que acontece na primeira vez que uma conversa é vista por uma versão de prompt nova — aí a releitura total continua sendo o comportamento certo |
| `extracao-em-lote-por-janela` | `webhook.py::_extrair` dispara uma chamada de LLM por evento recebido, sem agrupar rajadas de mensagens fragmentadas do WhatsApp — cada chamada paga ~737 tokens fixos de `system_prompt()` contra ~65 tokens de conteúdo real numa mensagem só | Não muda a temperatura (já calculada ao vivo, direto dos timestamps de `mensagens`, sem depender de extração) nem os timestamps de estágio (já carimbados pelo momento da evidência, não do processamento) |

Nenhum dos dois muda `rules/` nem a divisão de três lugares do LLM (§1). Ordem
de implementação: `backfill-cobertura-por-prompt` primeiro (mais isolado,
maior confiança de causa), `extracao-em-lote-por-janela` depois.

## Migração para Supabase e otimização de N+1 (2026-08-28)

Outra sessão migrou `CAMU_DB_DSN` de Postgres local para um projeto Supabase
(pooler `aws-0-us-east-2.pooler.supabase.com`), sem intervenção do usuário
nesta sessão. `start.sh`/`status.sh` ganharam detecção do banco real (antes
assumiam Postgres local incondicionalmente via docker — o que escondeu, na
prática, que o painel estava conectando em outro lugar).

**Achado, investigado por eliminação:** o painel "não carregava" a fila
contra o Supabase — não travamento, latência real. `pipeline.recalcular`
faz ~10 idas ao banco por conversa (`fatos_da_conversa`, `listar_mensagens`,
`fato_registrado_em` × N, `ultimo_followup_em`, `ultimo_avanco_em`/
`_causada_por`, `estagio_maximo_alcancado`, `estagio_corrente`, `marco_em`
× N, `recusa_desconsiderada`); `_carregar_candidatos`/`_carregar_fechadas`
(painel) e `camucrm fila` chamavam isso uma conversa de cada vez. Contra
Postgres local (latência ~0) isso sempre foi de graça; contra o Supabase
(~0,5-0,7s por ida, medido), a fila com 4 conversas abertas levava 34-48s —
confirmado que a rota SEMPRE terminava (espera de ~120s), só devagar demais
para um navegador não desistir.

**Correção:** `Database.contexto_para_recalculo` (seis consultas em lote,
`WHERE conversa_id = ANY(%s)`, uma por tabela envolvida) + `pipeline.
recalcular_lote`, que reusa `recalcular` sem duplicar a lógica de decisão —
`pipeline._ConversaCacheada` intercepta só as leituras (servidas do lote
pré-carregado), delegando toda escrita ao `Database` real. Medido depois:
34-48s → 2-3s no painel, 44s → 5s em `camucrm fila`. Equivalência com o
caminho antigo provada contra Postgres real, não só por fake — ver
`tests/integration/test_recalculo_em_lote_postgres.py` (fatos, trilha de
eventos com reabertura, follow-up + recusa desconsiderada, marco manual, e
várias conversas no mesmo lote não se misturando).

Fora de escopo desta correção: `recalcular` de UMA conversa (webhook,
extração, ações do painel) continua fazendo as ~10 idas de sempre — N=1 não
tem N+1 para otimizar, e `_ConversaCacheada` existe só para o caminho em
lote.

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
4. **`prospeccao-b2b-shortlist`** — pedido do usuário, sem dependência das
   outras três. Lista de petshops levantada externamente, anterior a
   qualquer conversa — capability nova, separada do modelo de conversa da
   §9 de propósito (ver "Decisões que divergem" acima para a base legal e a
   decisão de nunca enviar pela API).
5. **`importacao-conversas-whatsapp`** — implementado em 2026-08-27
   (proposto e concluído no mesmo dia), pedido do usuário: contato deixou
   de acontecer só pelo número da Camu, e a
   exportação de conversa do próprio WhatsApp (`.txt`) precisa de um lado
   de importação para essas conversas entrarem no mesmo funil/regras. Sem
   dependência das anteriores. Reaproveita `backfill.importar_conversas`
   para gravar as mensagens e a rota já existente `POST
   /conversas/{id}/extrair` para extrair (`origem='live'`, timestamp real
   por transição — ver `design.md` do change para o porquê de não ser
   `'backfill'`) — disparo só pelo painel, decisão do usuário, sem comando
   CLI dedicado.
6. **`ingestao-restrita-por-instancia`** — implementado em 2026-08-27
   (proposto e concluído no mesmo dia), pedido do usuário: número pessoal
   (dele e do Felipe) vai virar instância própria
   da Evolution API, ao vivo, além do número da Camu. Sem a restrição,
   qualquer amigo/familiar que mandasse mensagem viraria "contato" no CRM.
   Decisão confirmada com o usuário: a restrição ("só contato já conhecido
   ou vindo de `prospeccoes`") é por INSTÂNCIA
   (`CAMU_INSTANCIAS_RESTRITAS`), nunca global — a instância da Camu
   continua aceitando DM nova de qualquer um, que é como o funil B2C entra
   hoje (§12). Sem dependência das anteriores. **Verificado em produção em
   2026-08-28** (`tasks.md` 7.2): instância `pessoal-marcos` registrada,
   webhook apontado, teste real confirmou a restrição disparando e o
   payload excluído de `eventos_recebidos_bruto` — sem divergência do
   formato assumido em `design.md`.

O painel não é mais candidato — é change ativo, antecipado. Ver os seis
changes `painel-leitura`, `painel-tempo-real`, `acoes-no-painel`,
`rascunho-registrado`, `resumo-conversa` e `analise-desempenho` em
`openspec/changes/`.
