# Foto sem legenda avança S2 (mensagem-chave do funil B2C)

## Why

`foto_pet_recebida` (§2) é o único fato que deriva `S2` (§3, "Foto
recebida — Cliente mandou foto do pet"), e `S1→S2` é uma das três taxas
que §14 elege como "a métrica que justifica o sistema". Hoje esse fato só
pode nascer de `extraction/contract.py`, que exige **evidência literal**
— um trecho de TEXTO que aparece na conversa (invariante 1 do
`CLAUDE.md`). Uma foto sem legenda não produz texto nenhum: `_texto_da_
mensagem` (`transport/evolution.py`) devolve `bloco.get("caption") or ""`
para `imageMessage` — uma foto sem legenda vira uma mensagem `in` com
`texto=""`, e uma mensagem sem texto não pode, estruturalmente, virar
evidência de nada. O cliente que manda só a foto, sem escrever nada junto
— o caso mais comum no funil B2C, onde a foto costuma ser a primeira
coisa que o cliente manda — nunca alcança `S2`.

Isso não é um bug de prompt (não é o LLM "não vendo" a foto — o LLM nunca
recebe sinal de que uma foto foi anexada; ele só lê texto). É uma lacuna
estrutural: nenhuma quantidade de ajuste em `extraction/prompt.py`
resolve, porque não há texto para o modelo ler. O change irmão
`mensagem-sem-texto-preservada` já resolveu o problema adjacente (mídia
sem legenda sendo **descartada inteira**, corrompendo `bola_com`/relógio
de temperatura) para áudio, figurinha, contato e localização — mas
deliberadamente deixou `imageMessage`/`videoMessage`/`documentMessage` de
fora porque `_texto_da_mensagem` já devolve algo (`""`) para eles, então
o evento não é descartado. O problema aqui é outro: a mensagem existe,
mas carrega zero evidência para o fato que mais importa medir.

## What Changes

- **`mensagens` ganha uma coluna `midia_tipo`** (nullable, mesmo domínio
  de `_tipo_de_midia` em `transport/evolution.py`: `image`/`video`/
  `audio`/`document`/`sticker`/`contact`/`location`/`live_location`,
  `NULL` quando não há mídia). Captura o tipo estrutural da mensagem
  **independente** de haver legenda — hoje esse dado já é calculado por
  `_tipo_de_midia` e descartado logo em seguida.
- **Fato determinístico, fora do LLM.** Uma mensagem `in`, numa conversa
  B2C, com `midia_tipo='image'`, faz `foto_pet_recebida=true` ser gravado
  em `fatos` diretamente — sem passar por `extraction/contract.py`, sem
  chamada ao LLM. A evidência gravada é um marcador fixo (não o conteúdo
  da imagem, que este sistema nunca vê): reaproveita o vocabulário de
  marcador que `transport/evolution.py` (`_MARCADORES`) e
  `whatsapp_export.py` (`_PLACEHOLDERS_MIDIA`) já usam para mídia sem
  legenda — `[imagem]`. Este é o único ponto do sistema onde um fato do
  §2 nasce fora de `extraction/`, e é uma DIVERGÊNCIA proposital do
  invariante 1 do `CLAUDE.md` ("todo `true` exige evidência literal via
  `extraction/contract.py`") — ver design.md para o porquê de ser seguro:
  a extração exige evidência literal porque o LLM pode alucinar; aqui não
  há inferência nenhuma, "a mensagem contém uma imagem" é um fato do
  próprio payload da Evolution API, não uma interpretação.
- **`_texto_da_mensagem` para `imageMessage`/`videoMessage`/
  `documentMessage` sem legenda** deixa de devolver `""` e passa a
  devolver o marcador de tipo (`[imagem]`/`[vídeo]`/`[documento]`) — hoje
  uma foto sem legenda é invisível até no painel (linha de mensagem em
  branco). Isso NÃO é o que dispara `foto_pet_recebida` (o disparo é
  `midia_tipo`, direto do payload estruturado, não do texto) — é
  consistência com o padrão já estabelecido por `mensagem-sem-texto-
  preservada` e visibilidade no painel, nada mais.
- **Escopo do gatilho determinístico: só `funil=B2C`, só `midia_tipo=
  'image'`, só `direcao='in'`.** B2B (`P0-P6`) não tem fato análogo — uma
  foto vinda de petshop não significa nada no funil de consignação.
  Vídeo/documento continuam fora do gatilho determinístico (ver "Fora de
  escopo" — ambíguo demais para ser automático sem revisão).
- **Nenhum binário é baixado nem armazenado.** O sistema nunca busca a
  imagem em si na Evolution API — só sabe, pelo tipo do payload, que uma
  imagem foi anexada. Isso elimina a necessidade de critério de retenção
  separado para mídia que o roadmap (`openspec/roadmap.md`, onda 4)
  antecipava: `midia_tipo` é uma string curta de enum fechado, sem
  conteúdo pessoal, na MESMA linha e ciclo de vida de `mensagens.texto` —
  já coberta por `Database.purgar_mensagens_antigas` (§12, 12 meses), sem
  tabela nem coluna nova de retenção.

## Impact

- Specs afetadas: `midia-foto-pet` (nova)
- Schema: `mensagens.midia_tipo` (migration idempotente, mesmo padrão de
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` que o resto do `SCHEMA` em
  `db.py` já usa para colunas adicionadas depois da tabela original)
- Código: `camucrm/transport/evolution.py` (`_texto_da_mensagem`, e
  `_tipo_de_midia` passa a ser propagado, não só usado para decidir
  marcador), `camucrm/transport/base.py` (`EventoRecebido.midia_tipo`),
  `camucrm/db.py` (persistir a coluna, `gravar_fato_deterministico` ou
  equivalente), `camucrm/ingest.py` (chamar o gatilho determinístico após
  gravar a mensagem), um módulo pequeno novo e puro (proposto:
  `camucrm/extraction/deterministico.py` — ver design.md para o porquê de
  ficar dentro do pacote `extraction/` sem violar a regra "LLM só em três
  lugares")
- Testes: `tests/test_transport.py` (marcador de imagem sem legenda,
  `midia_tipo` propagado), `tests/test_ingest.py` ou equivalente (fato
  gravado sem chamar LLM, só para B2C+image+in), `tests/test_e2e.py`
  (estende o E2E único do repo — não duplica, por `CLAUDE.md`)
- Bloqueado por: nenhum tecnicamente. **Recomendado, não obrigatório:**
  esperar a onda 0.1 do roadmap (eval de 30 conversas rotuladas) antes de
  habilitar em produção — este change muda exatamente o critério que o
  eval existe para medir antes/depois (§7: "Falso positivo de avanço de
  estágio: 0", a única meta que reprova sozinha). Ver design.md, seção
  "Risco", para o argumento de por que o risco é baixo mesmo sem eval.

## Fora de escopo

- **Baixar/armazenar a imagem em si.** Nenhuma necessidade identificada
  no CRM (que rastreia funil, não faz a personalização) — evita LGPD de
  mídia binária inteiramente, decisão central deste design.
- **Vídeo/documento como gatilho determinístico de `foto_pet_recebida`.**
  Um vídeo do pet também deveria contar, mas é ambíguo o bastante (vídeo
  de outra coisa, documento de pagamento/comprovante) para não entrar
  sem revisão — fica de fora até haver pedido explícito ou sinal de
  volume real perdido.
- **Classificação por conteúdo (visão computacional/OCR) para confirmar
  que a imagem é de fato um pet.** Exigiria baixar a mídia (reabre LGPD)
  e uma chamada multimodal ao LLM (mudaria a natureza de `extraction/` —
  hoje só lê texto). Não perseguido: o design.md argumenta que a taxa de
  falso positivo de "qualquer imagem no DM = foto do pet" já é baixa o
  bastante sem isso, dado que o canal B2C é dedicado a esse produto.
- **Aplicar o mesmo padrão a `importacao-conversas-whatsapp`/
  `whatsapp_export.py`.** O parser de exportação `.txt` já produz o
  marcador `[imagem]`, mas não carrega um `midia_tipo` estruturado (o
  `.txt` do WhatsApp não distingue por payload, só por linha de aviso em
  texto) — ensinar o import a também gravar `foto_pet_recebida`
  deterministicamente a partir do marcador de texto é extensão natural,
  mas outro caminho de código (`backfill.importar_conversas`), tratado
  como item futuro, não deste change.
