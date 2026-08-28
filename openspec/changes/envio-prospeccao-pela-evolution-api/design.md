# Design — envio de prospecção pela Evolution API

## Decisão: reverter a decisão 2 de `prospeccao-b2b-shortlist`, com a garantia reformulada em vez de removida

A decisão original comparava duas opções e escolheu o link `wa.me` porque
"reverteria a garantia testada". O pedido do usuário aceita esse custo
explicitamente. A resposta de design não é "apagar a garantia" — é
reformulá-la para o que continua sendo verdade:

- **Antes:** nenhum módulo de `camucrm/painel/` importa `camucrm.transport`.
- **Agora:** nenhum módulo de `camucrm/painel/` importa `camucrm.transport`,
  **exceto `envio.py`**, que faz isso de propósito, documentado, e cuja
  única função pública exige `aprovado_por` não-vazio antes de tocar a rede.

O teste (`tests/test_painel_api.py`) muda de forma, não de espírito: ainda é
uma checagem por AST, ainda roda sobre todos os arquivos de
`camucrm/painel/`, só que agora com uma exceção nomeada e um teste
complementar que prova que a exceção existe pelo motivo certo (exige
aprovação humana).

## Por que módulo separado, e não a rota direto em `api.py`

`api.py` hoje é 100% leitura + as ações já existentes de `acoes-no-painel`
(marco, funil, correção — todas escrevem no banco, nenhuma toca rede
externa). Colocar a chamada a `transport.enviar` ali misturaria, no mesmo
arquivo, "grava uma decisão no banco" com "faz uma chamada HTTP para fora do
processo, com credencial". São coisas de risco muito diferente — a segunda
pode falhar de rede, vazar em erro de log, custar limite de taxa da
Evolution.

`envio.py` isola isso: uma função, `enviar_prospeccao`, que a rota em
`api.py` chama sem `api.py` precisar importar `transport` diretamente. A
rota fica assim:

```python
@router.post("/prospeccao/{prospeccao_id}/enviar")
def enviar_prospeccao_rota(prospeccao_id: int, corpo: EnviarProspeccaoBody, db=Depends(_db)):
    return envio.enviar_prospeccao(
        db, prospeccao_id, telefone=corpo.telefone, mensagem=corpo.mensagem, por=corpo.por
    )
```

`api.py` continua sem `import camucrm.transport` — só `envio.py` tem essa
linha, e é o único arquivo que o teste AST precisa permitir.

## `enviar_prospeccao`: o que ela faz e o que ela recusa

```python
def enviar_prospeccao(db, prospeccao_id, *, telefone, mensagem, por):
    quem = (por or "").strip()
    if not quem:
        raise ValorAusenteError("por")          # -> 422, nunca chega na rede
    if not (telefone or "").strip():
        raise ValorAusenteError("telefone")
    if not (mensagem or "").strip():
        raise ValorAusenteError("mensagem")

    transporte = transport.criar_transporte("evolution")
    try:
        resultado = transporte.enviar(
            transport.Destinatario(telefone), mensagem, aprovado_por=quem
        )
    except transport.TransporteError as exc:
        db.registrar_envio_prospeccao(prospeccao_id, por=quem, sucesso=False, erro=str(exc))
        raise                                     # -> 502, api.py traduz

    db.registrar_envio_prospeccao(prospeccao_id, por=quem, sucesso=True)
    return {"ok": True, "externa_id": resultado.externa_id}
```

`telefone`/`mensagem` vêm do corpo da requisição, **não** lidos de
`prospeccoes.telefone`/do template — são exatamente o que o operador viu (e
pode ter editado) no popup. Isso é o requirement central do pedido: revisar
e editar antes de enviar. Ler direto do banco ignoraria qualquer edição que
o operador tenha feito na tela.

`TransporteError` propaga (não é engolido) porque a rota precisa devolver
502 com o detalhe — o operador precisa saber que falhou e por quê, para
decidir se tenta de novo ou usa o link `wa.me` como alternativa.

## Schema: `enviado_em`/`enviado_por`/`enviado_erro`, distintos de `aberto_em`/`aberto_por`

```sql
ALTER TABLE prospeccoes ADD COLUMN IF NOT EXISTS enviado_em    TIMESTAMP WITH TIME ZONE;
ALTER TABLE prospeccoes ADD COLUMN IF NOT EXISTS enviado_por   VARCHAR(48);
ALTER TABLE prospeccoes ADD COLUMN IF NOT EXISTS enviado_erro  TEXT;
```

`aberto_em`/`aberto_por` (schema original) significam "o operador clicou no
link `wa.me`" — intenção registrada, o `design.md` anterior é explícito que
isso **não** é confirmação de envio, porque o sistema não tem como saber se
a mensagem foi enviada de fato dentro do WhatsApp.

`enviado_em`/`enviado_por` são diferentes em espécie: só são gravados depois
que a Evolution API respondeu com sucesso — é confirmação, não intenção. Por
isso colunas novas, não reuso das antigas: misturar as duas semânticas na
mesma coluna faria uma UI não conseguir distinguir "abriu o WhatsApp" de
"a API confirmou o envio", que é justamente a pergunta que motivou o pedido.

Uma tentativa que falha grava `enviado_erro` e `enviado_por`, mas **não**
sobrescreve `enviado_em` com `NULL` — se uma tentativa anterior teve
sucesso, esse registro fica, e a tela mostra "enviado em X, mas a tentativa
mais recente (Y) falhou: <erro>" em vez de perder o histórico do que
funcionou.

## Frontend: popup, não navegação

`linhaProspeccao(p)` ganha um segundo botão, "Enviar pela Evolution API",
ao lado do link `wa.me` existente — os dois continuam coexistindo, o
operador escolhe. O popup:

1. Abre com telefone e mensagem pré-preenchidos. O telefone é extraído do
   `link_whatsapp` já presente no payload (`?phone=...`) — **nenhum campo
   `telefone` novo é exposto no JSON de `/api/prospeccao`**, preservando a
   cautela §12 registrada em `views.prospeccao_para_json` (telefone só sai
   embutido em link, nunca como campo dedicado).
2. Os dois campos são editáveis (`<input>`/`<textarea>`), e o campo
   "aprovado por" vem pré-preenchido de `obterOperador()` (mesmo padrão de
   toda ação existente no painel).
3. "Enviar" chama `chamarApiEscrever` contra a rota nova. Sucesso fecha o
   popup e atualiza a linha (mostra "enviado às HH:MM"). Falha mostra o
   erro **dentro do popup**, sem fechar — o operador não perde o texto que
   editou e pode tentar de novo ou copiar a mensagem e usar o link `wa.me`.
4. Modal simples (`position: fixed`, overlay, `Escape`/clique fora fecha),
   sem framework — mesmo padrão de todo o painel (`el()`, JS puro, CSS
   próprio, `textContent` para qualquer texto vindo de dados).

## Consequência aceita: `EVOLUTION_API_KEY` no processo do painel

O `design.md` de `prospeccao-b2b-shortlist` registrava essa ausência como
propositalmente vazia. Este change aceita o custo: quando o operador usa o
botão, o processo do painel lê `EVOLUTION_API_BASE_URL`/`_API_KEY`/
`_INSTANCE` do ambiente — mesmas variáveis já usadas por `camucrm enviar` e
pelo receptor. Nenhuma variável nova é inventada; `.env` já as tinha porque
outros comandos já usam.

Isso não reabre a superfície do **receptor** (`camucrm/webhook.py`) — aquele
continua com `criar_transporte(..., para_envio=False)`, sem credencial,
garantia intacta. É só o processo do **painel** que ganha a credencial, e só
para este caminho.
