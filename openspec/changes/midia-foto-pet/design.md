# Design — foto sem legenda avança S2

## Decisão 1: fato determinístico, fora de `extraction/contract.py`

**Alternativas consideradas:**

**(a) Deixar o marcador `[imagem]` entrar no texto e ensinar o LLM a usar
isso como evidência de `foto_pet_recebida`.** Rejeitada. O invariante 1 do
`CLAUDE.md` ("todo `true` exige evidência literal") existe para impedir
alucinação — o modelo afirmando algo que a conversa não sustenta.
`mensagem-sem-texto-preservada` já tomou a decisão inversa e a registrou
explicitamente: nenhum marcador conta como evidência de fato nenhum,
justamente para não abrir um precedente onde "o marcador X justifica o
fato Y" vira um padrão que o modelo generaliza para combinações erradas
(ex. um dia alucinar que `[áudio recebido]` é evidência de
`preco_apresentado`, porque "deve ser o áudio explicando o preço"). Fazer
uma exceção só para este par (marcador `[imagem]` → `foto_pet_recebida`)
technically funciona, mas mistura duas fontes de verdade dentro do mesmo
mecanismo (`_fold`): "evidência real dita pelo cliente" e "evidência
sintética que o sistema mesmo inseriu" passam a precisar de tratamento
diferente dentro da MESMA função de conferência, aumentando a chance de a
distinção vazar para outro campo por engano numa mudança futura.

**(b) Fato determinístico, gravado direto em `fatos`, sem passar pelo
LLM.** Escolhida. `foto_pet_recebida` para uma mensagem com
`midia_tipo='image'` não é uma INFERÊNCIA — é um fato do próprio payload
estruturado da Evolution API (`message.imageMessage` existe ou não existe,
sem ambiguidade). Não há "literalidade" a conferir porque não há
alegação: o sistema não está interpretando texto, está lendo um campo
estruturado. Colocar isso fora de `extraction/contract.py` mantém a regra
"todo `true` (do LLM) exige evidência literal" com zero exceção — ela
continua falando exclusivamente de fatos que o LLM afirma. O preço: este é
o único ponto do sistema em que um fato do §2 nasce sem passar pelo
processo de extração — uma divergência real, registrada aqui e no
`proposal.md`, não escondida.

**Onde o código mora.** Um módulo pequeno e puro,
`camucrm/extraction/deterministico.py` — dentro do PACOTE `extraction/`
(que já hospeda `contract.py`, puro, sem LLM), mas fora do trio de lugares
onde `CLAUDE.md` autoriza chamada ao LLM (`extraction/`
propriamente dito quando faz a chamada, `drafts.py`, `summaries.py`).
Ficar dentro do pacote é deliberado: fisicamente perto do resto do
código que produz `fatos`, e o nome do arquivo (`deterministico.py`) deixa
claro, no primeiro grep, que aquele arquivo especificamente NÃO chama LLM
— mais fácil de auditar do que espalhar essa exceção para `pipeline.py`
ou `ingest.py`, que hoje não sabem nada sobre `§2`. A função exportada:

```python
def fato_de_midia(midia_tipo: str | None, direcao: str, funil: str) -> str | None:
    """Devolve a chave de `fatos` a gravar como `true`, ou `None`."""
    if funil == B2C and direcao == "in" and midia_tipo == "image":
        return "foto_pet_recebida"
    return None
```

Zero I/O, testável sem banco — mesmo padrão de `rules/`. `ingest.py`
chama essa função logo depois de gravar a mensagem (`Database.
gravar_mensagem` ou equivalente) e, se ela devolver uma chave, grava o
fato via um método novo de `db.py` (`gravar_fato_deterministico` — reusa a
MESMA tabela `fatos`, o mesmo índice de dedupe
`fatos_dedupe_idx (conversa_id, chave, valor, md5(evidencia))`, mesma
`mensagem_em`), com `evidencia='[imagem]'` fixo. Isso significa que
`rules/estagio.py::derivar` (que só lê `fatos.get("foto_pet_recebida")`)
não muda NADA — o fato está lá, venha de onde vier, e a regra que "nunca
regride" e o resto de §3 continuam funcionando sem saber a origem.

## Decisão 2: nenhum binário de mídia é baixado ou guardado

O produto da Camu é personalização a partir da foto, mas ESTE sistema é o
CRM de conversas — rastreia estágio/temperatura/fila, não faz a
personalização em si (isso já acontece em outro lugar, fora do escopo
deste projeto). O CRM não precisa NUNCA olhar o conteúdo da imagem — só
precisar saber que ela existe. Buscar o binário na Evolution API (que
oferece isso via `mediaUrl`/download por `mediaKey`) para guardar aqui
seria capability nova sem necessidade identificada, e reabriria
exatamente o problema de retenção que o roadmap (onda 4, texto original)
citava como razão para este ser "capability separada": imagem de pet
(e possivelmente de pessoa, se aparecer no enquadramento) é dado pessoal
sensível o bastante para exigir critério de retenção próprio, diferente
dos 12 meses de `mensagens.texto` (§12). Ao decidir NÃO guardar o
binário, essa exigência desaparece: `midia_tipo` é uma string de enum
fechado (`image`/`video`/.../`NULL`), sem conteúdo, do mesmo risco que
qualquer outra coluna de metadado de `mensagens` — coberta pela retenção
que já existe.

## Decisão 3: escopo do gatilho — por que "qualquer imagem = foto do pet"
é uma aposta razoável sem visão computacional

O risco central: um cliente manda uma imagem que NÃO é do pet
(comprovante de pagamento, print de outra loja, meme, foto de perfil) e o
sistema declara `S2` incorretamente — um falso positivo de avanço de
estágio, exatamente a métrica que §7 do documento de definições diz que
deve ser ZERO ("a única que reprova sozinha").

Argumento para aceitar esse risco sem visão computacional:

1. **O canal é dedicado.** O funil B2C é DM direto com a Camu, cujo
   produto inteiro é personalização a partir da foto do pet. Uma imagem
   enviada nesse canal, por um cliente ainda não convertido, tem prior
   altíssimo de ser exatamente isso — diferente de, por exemplo, um canal
   de suporte genérico onde imagem pode ser qualquer coisa.
2. **A consequência de um falso positivo aqui é branda, não a mesma
   classe da que §7 tenta prevenir originalmente.** O invariante 5 do
   `CLAUDE.md` ("envio exige `aprovado_por`") garante que nenhum estágio
   errado dispara ação automática — o pior caso é a conversa aparecer em
   `S2` no kanban/fila quando devia estar em `S1`, algo que um operador
   corrige com um clique (`camucrm/rules/estagio.py::reabrir`/correção
   manual já suportada pelo painel) ao abrir a conversa e ver que a foto
   não é de um pet. Comparar com o caso que §7 realmente teme: o LLM
   INVENTANDO que o cliente disse algo que não disse, silenciosamente,
   sem sinal para o operador desconfiar.
3. **A alternativa (visão computacional) muda a natureza do sistema.**
   Precisaria baixar o binário (reabre a Decisão 2), fazer uma chamada
   multimodal por imagem recebida (custo, latência, mais um jeito de
   `extraction/` falhar), e ainda teria sua própria taxa de erro — não é
   obviamente melhor que o determinístico "imagem existe = provavelmente
   é do pet" para o caso de uso real.

**Por isso a recomendação no `proposal.md` de esperar o eval (onda 0.1)
antes de habilitar em produção**, mesmo o argumento acima sendo forte: é
exatamente o tipo de mudança de critério que §7 pede para ser medida
antes/depois, e a decisão final de "vale o risco" cabe ao Marcos, não a
uma auto-avaliação de agente. Este design.md serve como a análise para
essa decisão, não como substituto dela.

## Decisão 4: coluna nova em vez de reaproveitar `texto`

`mensagens.midia_tipo` é uma coluna nova, não uma inferência sobre
`texto` (ex. checar se `texto == "[imagem]"`). Motivo: acoplar o gatilho
determinístico ao CONTEÚDO de `texto` faria qualquer mudança futura no
vocabulário de marcadores (`_MARCADORES`/`_PLACEHOLDERS_MIDIA`) capaz de
quebrar silenciosamente a detecção de `foto_pet_recebida` — dois
mecanismos com propósitos diferentes (marcador = visibilidade humana no
painel; `midia_tipo` = sinal estruturado para regra) compartilhando um
único campo de texto seria o tipo de acoplamento acidental que este
projeto evita em outro lugar (`fatos.mensagem_em` foi uma adição
deliberada pelo mesmo motivo — ver `openspec/project.md`, "Decisões que
divergem"). `midia_tipo` sobrevive a qualquer redesign futuro do texto do
marcador.

## Migração de schema

`ALTER TABLE mensagens ADD COLUMN IF NOT EXISTS midia_tipo VARCHAR(16)` —
mesmo padrão idempotente que `db.py::SCHEMA` já usa para toda coluna
adicionada depois da tabela original (grep por `ADD COLUMN IF NOT EXISTS`
em `db.py` para o precedente exato). Sem `NOT NULL`/`DEFAULT`: mensagens
existentes ficam `NULL` (sem mídia conhecida), comportamento correto —
não têm como saber retroativamente.
