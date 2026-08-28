# Ingestão restrita por instância — número pessoal e do Felipe

## Why

O usuário e o Felipe também abordam petshops fora do número da Camu — hoje
resolvido só por importação manual do `.txt` exportado (change
`importacao-conversas-whatsapp`). O pedido agora é ir além: conectar os dois
números pessoais como instâncias próprias da Evolution API, ao vivo, mandando
evento para o mesmo webhook que já recebe o número da Camu.

Sem nenhuma mudança de código, isso quebraria o §1: `ingest.ingerir` hoje
cria `contato`/`conversa` para **qualquer** remetente que mande mensagem, sem
checar quem é — é assim que um consumidor nunca visto vira lead B2C pelo
número da Camu, o que é correto e intencional ali. No número pessoal, o mesmo
comportamento criaria um "contato" pra cada amigo, familiar ou grupo que
mandar mensagem — poluindo kanban, fila e métricas com gente que nunca teve
relação comercial nenhuma com a Camu.

## Decisão de arquitetura (ver `design.md` para o raciocínio completo)

**A restrição é por instância, não global.** Confirmado explicitamente com o
usuário: a instância da Camu continua aceitando qualquer mensagem nova sem
restrição — é a porta de entrada principal do funil B2C hoje (§12: "B2C: o
cliente iniciou o contato"). Só as instâncias listadas em
`CAMU_INSTANCIAS_RESTRITAS` (nova variável de ambiente, nomes separados por
vírgula) ganham a regra nova: só criam/atualizam `contato`/`conversa` para um
telefone que **já é `contato` conhecido** OU **já está em `prospeccoes`**
(a mesma tabela do change `prospeccao-b2b-shortlist`). Telefone desconhecido
numa instância restrita é ignorado por inteiro — nenhum `contato`, nenhuma
`conversa`, nenhuma `mensagem` — confirmado com o usuário ("ignorar
totalmente... como se o sistema nem tivesse recebido"). **Revisão de
2026-08-27**, também pedida explicitamente: o payload cru fica staged só
até a decisão terminar — se o motivo for restrição de instância, a linha
é excluída na hora, não segue a retenção padrão (ver `design.md`, Decisão
2).

Sem instância nenhuma listada (variável ausente, o padrão), **nada muda**: é
o comportamento de hoje, com uma única instância implícita e sem restrição —
zero risco de regressão pra quem não configurar a variável nova.

## What Changes

- `camucrm/config.py`: `CAMU_INSTANCIAS_RESTRITAS` (CSV de nomes de instância
  da Evolution API) + `instancias_restritas() -> frozenset[str]`, vazio por
  padrão.
- `camucrm/db.py`: `contato_por_telefone_hash(hash) -> Contato | None` —
  leitura pura, sem upsert (mesmo padrão de `prospeccao_por_telefone_hash`)
  — necessária porque `ingest.ingerir` precisa saber se o telefone JÁ é
  contato ANTES de decidir se cria um novo.
- `camucrm/ingest.py::ingerir`: novo parâmetro `instancia: str | None`. Se a
  instância está em `instancias_restritas()` e o telefone não é `contato`
  conhecido nem está em `prospeccoes`, devolve `ResultadoIngestao(ignorada=
  True, ignorada_por_restricao_instancia=True)` sem tocar
  `contatos`/`conversas`/`mensagens`. Mesma checagem para as duas direções
  (inbound e o eco `fromMe` de uma mensagem enviada pelo próprio número
  pessoal) — nenhum tratamento especial por direção.
- `camucrm/webhook.py`: extrai `payload.get("instance")` do corpo do evento
  (campo padrão da Evolution API em `messages.upsert`) e passa para
  `ingerir(..., instancia=...)`. Quando o resultado vem com
  `ignorada_por_restricao_instancia=True`, chama `db.excluir_evento_bruto`
  em vez de `marcar_evento_bruto_processado` — o payload staged não
  sobrevive à decisão (revisão de 2026-08-27).
- `camucrm/db.py::excluir_evento_bruto(evento_id)`: `DELETE` imediato de
  uma linha de `eventos_recebidos_bruto`, sem esperar
  `purgar_eventos_brutos_antigos`.
- `camucrm/cli.py::cmd_ingerir`: flag `--instancia` opcional, mesmo caminho
  que o webhook usa — os dois nunca podem divergir (docstring de
  `ingest.py`).

## Impact

- Specs afetadas: `ingestao-restrita-por-instancia` (nova)
- Código alterado: `camucrm/config.py`, `camucrm/db.py`, `camucrm/ingest.py`,
  `camucrm/webhook.py`, `camucrm/cli.py`
- Código reaproveitado, sem alteração: `camucrm/db.py::
  prospeccao_por_telefone_hash` (já existe, change `prospeccao-b2b-
  shortlist`)
- Testes novos/estendidos: `tests/test_ingest.py`, `tests/test_webhook.py`,
  `tests/fakes.py` (novo método fake), extensão de `tests/test_cli.py` se
  existir cobertura de `cmd_ingerir`
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Registrar as instâncias novas na Evolution API** (QR code do número
  pessoal e do Felipe) — passo de infraestrutura/operação fora do
  repositório, não deste change.
- **Envio pela CRM a partir dos números pessoais.** `transport/evolution.py`
  continua com uma única instância configurada para `enviar()`
  (`EVOLUTION_INSTANCE`). Os números pessoais só ALIMENTAM o CRM (leitura);
  o envio continua sendo feito manualmente pelo próprio WhatsApp de quem
  está operando, exatamente como já acontecia antes deste change (e como o
  documento nunca previu envio automático de jeito nenhum, §10).
- **Painel para configurar `CAMU_INSTANCIAS_RESTRITAS`.** Variável de
  ambiente, mesmo padrão de `CAMU_PLAYBOOK`/`CAMU_MENSAGEM_PROSPECCAO` — não
  uma tela nova.
- **Confiar em qualquer sinal além do nome da instância** (ex.: heurística
  de conteúdo da mensagem) para decidir se uma conversa é comercial — seria
  inferência de decisão de negócio, proibida por §1. A instância de origem é
  metadado de transporte, não conteúdo interpretado.
