# Design — importação de conversas via exportação do WhatsApp

## Decisão 1: reaproveitar `origem='backfill'`, não criar um terceiro valor

A tentação óbvia é achar que uma conversa importada do `.txt` merece uma
origem própria (`'importado'`?), já que — diferente do backfill histórico
original — cada mensagem carrega um timestamp real do WhatsApp, não um
timestamp inventado. Investigação no código mostra que essa distinção não
existe onde importa:

- `extraction/extractor.py::processar_conversa` chama
  `rules.estagio.recalcular(..., agora=agora, ...)`, e `agora` é o momento
  em que o processamento roda — **não** o timestamp da mensagem que causou
  a transição. Isso vale igualmente para o backfill original (que roda em
  lote, via `trilha()`, que deriva o estágio final a partir dos fatos
  acumulados) e para esta importação (mesma chamada, mesmo `forcar=True`).
- `eventos_estagio.em`, portanto, é sempre "quando processamos", nunca
  "quando aconteceu de verdade" — para as duas origens. A garantia que
  `metrics.py` já aplica a `origem='backfill'` ("fora de métrica de tempo")
  é exatamente a garantia certa aqui: se incluíssemos essas conversas em
  métrica de tempo por estágio, a duração medida seria "tempo até alguém
  exportar e importar o `.txt`", não "tempo até o cliente responder" —
  ruído idêntico ao que backfill já existe para excluir.
- `conversas.ultimo_inbound`/`ultimo_outbound` **não** vêm de
  `eventos_estagio` — vêm direto de `mensagens.enviada_em` via
  `db.registrar_mensagem` (`camucrm/db.py:1305`). Como o parser preserva o
  timestamp real de cada linha do `.txt`, temperatura (§5) e fila (§6)
  continuam corretas mesmo para conversa importada — não dependem de
  `origem`.

Conclusão: criar um terceiro valor de `origem` mudaria uma constraint de
schema (`CHECK (origem IN ('live', 'backfill'))`, `camucrm/db.py:556`) e um
enum em `rules/estagio.py` para representar uma distinção que não afeta
nenhum comportamento — todo o código que lê `origem` já trata os dois casos
de forma correta e idêntica. É complexidade sem consequência observável,
exatamente o que `CLAUDE.md` pede pra evitar. Reaproveitar `'backfill'` é o
desenho certo, não um atalho.

**Se essa premissa mudar** (por exemplo, se `recalcular` passar a aceitar
timestamp por transição em vez de `agora` — mudança que nenhum change ativo
propõe hoje), a distinção volta a fazer sentido e merece revisão nova.
Registrado aqui para não se perder.

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


POST /api/importacao-whatsapp/{conversa_id}/extrair   ← passo separado, opcional
    ▼
Extrator(db, criar_llm()).processar_conversa(
    conversa_id, origem=ORIGEM_BACKFILL, forcar=True
)   ← mesma chamada que `camucrm backfill --extrair` já faz, escopada a
      UMA conversa em vez de todas as abertas (`extrair_historico` itera até
      `limite=1000`; aqui o operador já sabe qual conversa acabou de importar)
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
