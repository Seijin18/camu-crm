# Design — importação de conversas via exportação do WhatsApp

## Decisão 1: extração usa `origem='live'`, não `'backfill'`

**Revisão de 2026-08-27**: a primeira versão deste `design.md` propunha
reaproveitar `origem='backfill'` (raciocínio abaixo, riscado, mantido para
não apagar o porquê da mudança). Releitura mais cuidadosa de `pipeline.py`
mostrou que a premissa estava errada.

~~`extraction/extractor.py::processar_conversa` chama `rules.estagio.
recalcular(..., agora=agora, ...)`, e `agora` é o momento em que o
processamento roda — não o timestamp da mensagem que causou a transição.
Isso vale igualmente para as duas origens.~~ **Falso.** `pipeline.py::
recalcular` trata as duas origens de forma bem diferente:

- **`origem='live'`** (`_avanco_ao_vivo` + `momentos_de_estagio`): cada
  `eventos_estagio.em` recebe o momento REAL do fato que disparou a
  transição — `sinais.primeiro_inbound`, `db.fato_registrado_em(...,
  "foto_pet_recebida")` etc., que por sua vez vêm de `fatos.mensagem_em`
  (o timestamp da MENSAGEM real, sempre gravado por `extractor.
  _persistir`/`momento_da_evidencia`, independente de origem). Só cai em
  `agora` quando não há timestamp de fato disponível.
- **`origem='backfill'`** (`_trilha_de_backfill`): `momentos = {}` **é
  fixado vazio de propósito** em `recalcular` — o backfill descarta
  timestamp real mesmo quando ele existiria, e grava `em=None` sempre. Essa
  é a causa raiz do "fora de métrica de tempo", não uma limitação técnica
  inevitável: é uma postura defensiva deliberada, adequada para um dump
  histórico de origem desconhecida (planilha antiga, export de outro CRM)
  onde a acurácia por mensagem não é confiável a ponto de virar métrica.

O `.txt` exportado do WhatsApp **não é esse caso**: cada linha carrega
timestamp real, com precisão de minuto, do próprio WhatsApp — a mesma
qualidade de dado que uma mensagem chegando ao vivo pelo webhook. Usar
`origem='backfill'` aqui jogaria fora informação real disponível, e pior:
excluiria essas conversas de métrica de tempo por estágio **para sempre**,
mesmo quando elas passam a ser o canal principal de contato com aquele
cliente (exatamente o cenário que motivou o change — "atualizar conforme o
que estamos fazendo além do número da Camu").

**Decisão corrigida: `Extrator.processar_conversa(conversa_id)` roda sem
`forcar`, com `origem='live'` (o padrão do método) — a mesma chamada que a
rota já existente `POST /conversas/{id}/extrair` (change
`extracao-em-lote-por-janela`) já faz.** Consequências:

- `eventos_estagio` desta conversa entra em métrica de tempo por estágio
  normalmente, com timestamp real — correto, porque o dado é real.
- **Zero rota nova para extração.** A importação chama a rota que já
  existe; o botão "extrair" do painel, nesta aba nova, é literalmente o
  mesmo `POST /conversas/{conversa_id}/extrair` que a aba de conversas já
  usa.
- **Reimportação incremental funciona de graça.** Se o operador reexportar
  a mesma conversa do WhatsApp semanas depois (mais mensagens acumuladas) e
  reimportar, a extração processa só o bloco novo
  (`conversa.ultima_mensagem_processada_id` como watermark) — o mesmo
  comportamento que uma conversa alimentada por webhook já tem. Isso não
  seria verdade com `forcar=True`/`origem='backfill'`: cada reimportação
  releria a conversa inteira e continuaria gravando timestamp fabricado.
- Chunking de histórico grande (`TAMANHO_MAXIMO_BLOCO`, §8) é ortogonal a
  `origem` — continua se aplicando igual, então uma primeira importação
  com centenas de mensagens é processada em blocos do mesmo jeito que o
  backfill original processaria.

Consequência prática (revisada): **nenhuma mudança em `camucrm/db.py`
(schema), `camucrm/rules/`, nem em `camucrm/pipeline.py`.** O trabalho novo
continua sendo só (1) o parser puro do `.txt`, e (2) a rota de upload — a
extração reaproveita uma rota que já existe, sem modificação.

## Decisão 2: parser separado do transporte, sem tocar `camucrm/transport/`

`whatsapp_export.py` é puramente `.txt` → lista de mensagens estruturadas —
sem rede, sem DB, sem LLM. Ele não é um adaptador de `Transporte`
(`camucrm/transport/base.py`): não envia, não recebe evento ao vivo, não
tem `aprovado_por`. É a mesma categoria de `backfill.importar_conversas` —
ingestão em lote de um dump —, só que o dump é texto de exportação do
WhatsApp em vez de JSON. A invariante 5 do `CLAUDE.md` ("envio exige
`aprovado_por`") não se aplica aqui porque esta capability não envia nada;
ela só lê e importa histórico.

## Decisão 3: upload não persiste o `.txt` bruto em disco

Mesmo padrão do upload de CSV em `prospeccao-b2b-shortlist`
(`arquivo.file.read()` em memória, `camucrm/painel/api.py:726`). O `.txt`
exportado contém conteúdo pessoal de conversa em claro — gravá-lo em disco
criaria uma cópia fora do modelo de retenção do §12 (`mensagens` de
conversa encerrada há >12 meses é descartada; um arquivo solto no disco do
servidor não seria alcançado por essa purga). Parse acontece em memória, só
o resultado estruturado (mensagens) entra no banco pelas rotas já
existentes de `registrar_mensagem`.

## Decisão 4: validação de direção é obrigatória, sem fallback silencioso

O `.txt` exportado não marca direção (`in`/`out`) — só nome do remetente por
linha. O parser recebe `nosso_nome` (o nome que aparece no export do lado
de quem está respondendo pela Camu) e classifica cada linha por
correspondência exata de nome. Se `nosso_nome` não aparecer em nenhuma
linha reconhecida do arquivo, a importação **falha com erro explícito**, em
vez de assumir uma direção padrão — a auditoria de 2026-08 já registrou
"evidência não distingue lado (cliente vs. Camu)" como achado crítico
(`literalidade-e-idempotencia-da-extracao`) em outro ponto do pipeline;
esta capability não pode reintroduzir o mesmo modo de falha por um caminho
novo.

## Decisão 5: grupo do WhatsApp é rejeitado, não importado parcialmente

Exportação de grupo tem linhas de entrada/saída de participante e mais de
dois remetentes possíveis — não mapeia para `contato` (uma pessoa/empresa
por conversa, `docs/04-crm-conversas-definicoes.md` §9). O parser detecta
esse formato (mais de 2 nomes distintos nas linhas reconhecidas, ou
presença de linha de sistema de grupo — "X adicionou Y", "X saiu") e recusa
o arquivo inteiro com erro explícito, em vez de importar só as linhas do
par nome_operador/telefone informado e descartar o resto em silêncio.

## Fluxo da rota de importação

```
POST /api/importacao-whatsapp
  multipart: arquivo (.txt), telefone, tipo, nome_operador, nome?, origem?
    │
    ▼
whatsapp_export.parse(texto, nosso_nome=nome_operador)
    │  → ParseResultado(mensagens=[...], nome_contato, midia_preservada, ignoradas)
    │  → falha explícita se: formato de grupo detectado, ou nome_operador
    │    não bate com nenhuma linha reconhecida
    ▼
registro = {"telefone": ..., "nome": nome or nome_contato, "tipo": tipo,
            "origem": origem or "whatsapp-manual", "mensagens": [...]}
    ▼
backfill.importar_conversas(db, [registro])   ← reaproveitado, sem mudança
    │  → idempotente por externa_id sintético (mesmo `_externa_id_sintetico`
    │    que já existe em backfill.py — reimportar o mesmo .txt não duplica)
    ▼
resposta: resumo (mensagens novas, mídia preservada, linhas ignoradas),
          conversa_id (pra habilitar o botão "extrair" no painel)


POST /conversas/{conversa_id}/extrair   ← rota JÁ EXISTENTE (change
                                           `extracao-em-lote-por-janela`),
                                           reaproveitada sem nenhuma mudança
    ▼
Extrator(db, criar_llm()).processar_conversa(conversa_id)
    ← origem='live' (padrão), sem forcar — processa só o bloco pendente
      (tudo, na primeira vez; só o delta numa reimportação depois)
```

## Formato reconhecido do `.txt` (v1)

Duas variantes cobertas (Android sem colchetes, iOS/variante com
colchetes), sempre pt-BR, `DD/MM/AA` ou `DD/MM/AAAA`:

```
17/03/24, 14:32 - Ana Petshop: oi, vocês fazem porta-chaves?
[17/03/24, 14:32:05] Ana Petshop: oi, vocês fazem porta-chaves?
```

Linha de continuação (não bate com o padrão acima) é anexada ao `texto` da
mensagem anterior com `\n` — mesma proteção de fronteira que o change
`literalidade-e-idempotencia-da-extracao` já exige em `build_corpus`; o
parser não pode produzir uma mensagem que funde duas mensagens reais numa
só.

Placeholders de mídia reconhecidos (lista inicial, extensível):
`<Mídia oculta>`, `image omitted`, `video omitted`, `audio omitted`,
`sticker omitted`, `GIF omitted`, `Contact card omitted`, `document
omitted` — tratados como mensagem sem texto preservada (mesmo contrato do
change `mensagem-sem-texto-preservada`), nunca descartados.

Linhas de sistema reconhecidas e ignoradas (contadas, não geram mensagem):
aviso de criptografia ponta-a-ponta, "Esta mensagem foi apagada"/"You
deleted this message", mudança de número, criação/alteração de nome de
grupo (esta última também é sinal para a Decisão 5 acima).

Qualquer linha que não bate com nenhum dos padrões acima (mensagem,
continuação, mídia, sistema) é reportada em `ignoradas` no resumo da
resposta — nunca descartada sem contagem.
