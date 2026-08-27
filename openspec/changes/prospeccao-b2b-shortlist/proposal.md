# Lista de prospecção B2B — shortlist separada, link de WhatsApp por clique

## Why

O usuário tem uma planilha de petshops levantada externamente (nome, bairro,
zona, telefone, nota, avaliações, site — ex. `camu-petshops-shortlist.csv`)
para abordar comercialmente. Isso é **anterior** a qualquer conversa: não
existe hoje `contato`/`conversa` para essas linhas, e o modelo de dados do
CRM (`docs/04-crm-conversas-definicoes.md`) só cobre conversa que já existe.
Sem um lugar próprio para essa lista, o operador teria que criar `contato`/
`conversa` manualmente por petshop antes de mandar a primeira mensagem — o
que polui a fila e o kanban com "leads" que nunca falaram, e mistura duas
fases fundamentalmente diferentes (prospecção fria vs. conversa em
andamento) na mesma tela.

## Duas decisões de arquitetura (ver `design.md` para o raciocínio completo)

1. **Base legal (§12).** O documento já registra que nenhuma base cobre lista
   fria raspada/comprada. Decisão do usuário: **legítimo interesse para
   contato comercial B2B** (petshops, pessoa jurídica, abordagem comercial),
   documentada explicitamente aqui e em `project.md` — **não** se estende a
   qualquer lista de consumidor (B2C) raspada, que continua sem base
   nenhuma. Esta capability só existe para o funil B2B.
2. **O painel continua sem enviar mensagem.** Os 6 changes do painel já
   garantem isso, com teste dedicado (`test_nao_existe_rota_de_envio`) e sem
   `camucrm.painel` importar `camucrm.transport`. O botão de disparo desta
   feature **abre o link `wa.me`/`api.whatsapp.com` com a mensagem
   pré-preenchida** — o humano aperta enviar dentro do próprio WhatsApp. Zero
   credencial da Evolution API no processo do painel, zero rota de envio
   nova, garantia existente intacta.

## What Changes

- Tabela nova `prospeccoes` (schema em `design.md`) — inteiramente separada
  de `contatos`/`conversas`. Guarda a linha da planilha (nome, telefone,
  bairro, zona, nota, avaliações, site, tier_origem, status_origem) mais
  `telefone_hash` (reuso de `db.hash_telefone`, mesmo padrão de `contatos`)
  para dedupe e para detectar conversão.
- `POST /api/prospeccao/importar` — upload de CSV, upsert por
  `telefone_hash` (reimportar a mesma planilha atualiza, não duplica).
  Linhas com telefone ilegível são reportadas explicitamente no resultado
  (contagem + motivo), nunca descartadas em silêncio.
- `GET /api/prospeccao` — lista com filtros (bairro, zona, nota mínima,
  tier, status: não iniciado / já aberto / já é conversa). Cada linha traz
  o link de WhatsApp pronto e o texto da mensagem, calculados no servidor a
  partir de um template editável (mesmo padrão de `docs/playbook-tom.md` —
  arquivo de texto, não código, não LLM: **este change não introduz nenhuma
  superfície de LLM nova**, é substituição de `{nome}` em template fixo).
- **Detecção de conversão sem estado próprio**: `GET /api/prospeccao` faz
  `LEFT JOIN` por `telefone_hash` contra `contatos` a cada leitura — se um
  petshop da shortlist já virou `contato`/`conversa` real (respondeu pelo
  mesmo número, via o webhook normal), a linha mostra isso e linka para
  `#/conversas/{id}`. Nenhum job de sincronização, nenhum campo que possa
  ficar desatualizado — é uma consulta, sempre correta no momento da leitura.
- Painel — duas abas novas: "Importar prospecção" (upload + relatório do
  que entrou/atualizou/falhou) e "Prospecção" (lista, filtros, botão "abrir
  WhatsApp" por linha, botão "copiar mensagem"). **Sempre separada de
  kanban/fila/conversas** — nunca aparece nessas telas, nunca conta para
  métricas do funil, porque ainda não é conversa.
- `ingest.ingerir`: quando o telefone da mensagem inbound bate com uma linha
  de `prospeccoes`, o `contato` novo nasce `tipo=b2b` em vez do padrão B2C —
  decisão explícita, não inferência de conteúdo de conversa (§1 continua
  intacto: a classificação vem da origem curada da shortlist, que já é
  declaradamente B2B, não de adivinhar por texto da mensagem).

## Impact

- Specs afetadas: `prospeccao-b2b-shortlist` (nova)
- Código alterado: `camucrm/db.py` (tabela + métodos), `camucrm/ingest.py`
  (default de tipo por match de shortlist), `camucrm/painel/api.py`,
  `camucrm/painel/views.py`, `camucrm/painel/static/*`, arquivo de template
  novo (ex. `docs/mensagem-prospeccao.md`)
- Testes novos: `tests/test_prospeccao.py`, extensão de
  `tests/test_painel_api.py`, extensão de `tests/test_ingest.py`
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Envio automatizado/em lote** — só disparo unitário por clique humano,
  por linha. Nada dispara sozinho, nada dispara em massa.
- **Envio real pela Evolution API** — decisão 2 acima; fica para uma
  proposta futura separada, se algum dia for reconsiderada (exigiria
  reabrir a garantia testada de "painel nunca envia").
- **Base legal para lista de consumidor (B2C) raspada** — continua sem
  cobertura nenhuma; esta capability é estritamente B2B.
- **Geração de mensagem por LLM** — o texto é template fixo com
  substituição de nome, não geração — mantém "LLM em exatamente 3 lugares"
  do CLAUDE.md intacto.
