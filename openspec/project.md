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
| 8 | Painel | **Não feito, deliberadamente.** É o último; só faz sentido com histórico |

Os passos 1 e 4 são os únicos que exigem o Marcos e não podem ser delegados.
São também os que determinam se o resto vale alguma coisa.

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

## Próximos changes candidatos

Nesta ordem de dependência:

1. **`ground-truth-marcos`** — as 30 conversas rotuladas (§7). Bloqueia
   qualquer afirmação sobre qualidade de extração.
2. **`webhook-ingestao`** — hoje `camucrm ingerir` lê um payload do stdin.
   Falta o endpoint que recebe da Evolution API continuamente.
3. **`midia-foto-pet`** — S2 é o estágio-chave e hoje depende de o cliente
   escrever algo junto da foto. Tratar mídia traz retenção e LGPD (§12) junto,
   e por isso é capability própria.
4. **`painel`** — §13 passo 8. Só depois de haver histórico.
