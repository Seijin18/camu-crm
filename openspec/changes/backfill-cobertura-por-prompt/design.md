# Design — backfill-cobertura-por-prompt

## Por que um watermark por VERSÃO, não um watermark único por conversa

A primeira versão deste desenho usava um watermark único
(`conversas.ultima_mensagem_processada_id` + uma coluna
`extraida_com_prompt_versao`). Foi descartada por um cenário de "vintage
misto" que ela erra silenciosamente:

1. Conversa é extraída ao vivo sob o prompt v1 até a mensagem 150.
   `extraida_com_prompt_versao = "1"`.
2. `PROMPT_VERSAO` sobe para `"2"` (§7: eval rodado, prompt melhorado).
3. A mensagem 151 chega e é processada ao vivo, já sob v2.
   `extraida_com_prompt_versao` vira `"2"` — mas as mensagens 1–150 NUNCA
   foram lidas pelo prompt v2, só pelo v1.
4. Um backfill roda depois. Com um watermark único, a checagem "esta
   conversa já está coberta pela versão atual?" vê
   `extraida_com_prompt_versao == "2"` e responde **sim** — pulando a
   releitura de 1–150, que na verdade nunca passou pelo v2.

Isso é pior que não ter a otimização: um bug silencioso que esconde
exatamente o cenário que `forcar=True` existe para cobrir (reextrair
histórico com um prompt melhor).

**A correção é dar a cada versão de prompt seu próprio watermark,
independente.** `cobertura_extracao` é `(conversa_id, prompt_versao) →
ultima_mensagem_id`, uma linha por versão que já tocou aquela conversa. No
cenário acima:

- v1 fica com `ultima_mensagem_id = 150` para sempre (nunca mais avança,
  porque nada mais é extraído sob v1).
- v2 nasce sem linha (cobertura nula) na primeira vez que toca a conversa,
  e só então cresce — mensagem 151 grava `v2 → 151`.

A pergunta que o extrator faz não é mais "esta conversa está atualizada?",
é "**a versão de prompt atual** já leu até onde nesta conversa?" — e a
resposta vem só da linha daquela versão, nunca contaminada pelo que outra
versão fez antes ou depois.

## Onde isso entra em `processar_conversa`

```
cobertura = None
if forcar and somente_desatualizados:
    cobertura = db.cobertura_extracao(conversa_id, prompt_mod.PROMPT_VERSAO)

if forcar and cobertura is None:
    desde = None                                   # comportamento de hoje
    estagio_referencia = estagio_inicial(conversa.funil)
elif forcar:                                        # forcar + cobertura
    desde = cobertura                               # retoma de onde esta versão parou
    estagio_referencia = conversa.estagio           # cache já é a fonte certa
else:
    desde = conversa.ultima_mensagem_processada_id  # caminho ao vivo, inalterado
    estagio_referencia = conversa.estagio
```

O ramo `forcar + cobertura` colapsa para exatamente o mesmo comportamento
do caminho não-forçado — a diferença entre "backfill retomando" e
"extração ao vivo" deixa de existir na prática assim que a versão atual já
tocou a conversa uma vez. Isso é uma simplificação correta, não uma
coincidência: nos dois casos a pergunta é idêntica ("o que há de novo desde
o que esta versão de prompt já viu"), e a resposta correta é idêntica.

`estagio_referencia = conversa.estagio` é seguro porque
`conversa.estagio` é sempre reescrito por `recalcular` (`pipeline.py`)
depois de qualquer bloco processado, ao vivo ou backfill — a origem de
quem escreveu por último não importa para a validade do cache, só para o
carimbo de tempo dos eventos gerados a partir dele (`origem` continua
sendo passada pelo chamador, sem mudança).

## Por que gravar cobertura nos dois caminhos (ao vivo e backfill)

Se só o caminho de backfill gravasse `cobertura_extracao`, a primeira
passada de backfill depois de uma sequência de extrações ao vivo (o caso
mais comum: a maior parte do volume passa pelo webhook, não pelo backfill)
encontraria cobertura vazia e faria releitura total mesmo que a versão
atual já tivesse processado tudo ao vivo. O ganho do change dependeria de
"backfill já ter rodado uma vez sob esta versão" — praticamente nunca
verdade na operação real, onde backfill é exceção e webhook é regra.
Gravar em toda persistência bem-sucedida (live ou forçada) fecha essa
lacuna: o watermark por versão reflete tudo que já foi extraído sob ela,
não importa por qual porta de entrada.

## Degradação segura se `TAMANHO_MAXIMO_BLOCO` mudar

A cobertura é um watermark de mensagem (`ultima_mensagem_id`), não uma
lista de fronteiras de bloco — mudar `TAMANHO_MAXIMO_BLOCO` não invalida
cobertura já gravada nem quebra a leitura de onde parar. O próximo bloco
lido a partir do watermark simplesmente tem o novo tamanho.
