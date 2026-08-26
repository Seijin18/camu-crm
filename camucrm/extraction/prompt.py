"""Prompt de extração: as perguntas fechadas que o LLM tem permissão de responder.

§2. O modelo recebe um bloco de mensagens novas mais um resumo rolante, e
devolve o JSON do contrato. Ele não vê estágio, não vê temperatura e não vê
prioridade — se visse, começaria a raciocinar sobre elas, e §1 tira essa
decisão dele de propósito.

Toda mudança neste arquivo exige rodar o eval (§7). É barato e é o que separa
"o prompt melhorou" de "o prompt mudou".
"""

from __future__ import annotations

import json
from typing import Iterable, Mapping, Sequence

from ..taxonomia import OBJECAO_LABELS, OBJECOES
from .contract import FATOS_BOOLEANOS

# Versão do prompt. Gravada junto do eval: comparar duas rodadas sem saber
# qual prompt gerou cada uma não mede nada.
PROMPT_VERSAO = "1"

_PERGUNTAS = {
    "foto_pet_recebida": "O cliente enviou uma foto do próprio pet nesta conversa?",
    "preco_apresentado": "A Camu informou um valor de peça E o frete (ou que o frete é grátis)?",
    "previa_enviada": "A Camu mostrou ao cliente o resultado/prévia da arte do pet?",
    "intencao_compra_explicita": "O cliente disse explicitamente que quer comprar/fechar?",
    "recusa_explicita": "O cliente disse explicitamente que NÃO vai comprar / não quer?",
    "autorizou_envio_material": "O lojista respondeu autorizando o envio de foto/material?",
    "visita_aceita": "Ficou marcada uma data de visita presencial?",
}

_REGRAS = """\
REGRAS ABSOLUTAS

1. Responda SOMENTE com um objeto JSON no formato exato indicado. Sem texto
   antes ou depois, sem comentários.

2. Todo campo marcado `true` EXIGE uma evidência em `evidencias`: um trecho
   COPIADO LITERALMENTE da conversa, caractere por caractere. Não parafraseie,
   não resuma, não junte pedaços de mensagens diferentes.

3. Na dúvida, responda `false`. Errar para menos custa uma revisão; errar para
   mais faz um cliente interessado ser tratado como já atendido e ser
   abandonado. `false` sem evidência é a resposta correta e esperada.

4. Você responde apenas o que ACONTECEU no texto. Você não avalia se o cliente
   parece interessado, não estima probabilidade, não classifica estágio nem
   temperatura, e não sugere o que fazer. Essas decisões não são suas.

5. `objecao` só pode ser um destes valores, ou `null` se o cliente não
   levantou objeção alguma neste bloco:
{objecoes}
   Se o cliente objetou algo que não cabe em nenhum, use "outro" e ponha o
   trecho literal em `evidencias.objecao`.

6. Preço e frete são objeções DIFERENTES. "Ficou caro" é `preco`; "o frete
   ficou caro" ou "demora pra chegar" é `frete`. Nunca junte os dois.
"""


def _linhas_objecoes() -> str:
    return "\n".join(
        f"   - {codigo}: {OBJECAO_LABELS[codigo]}" for codigo in OBJECOES
    )


def _formato_json() -> str:
    exemplo: dict[str, object] = {campo: False for campo in FATOS_BOOLEANOS}
    exemplo["objecao"] = None
    exemplo["evidencias"] = {campo: None for campo in FATOS_BOOLEANOS} | {"objecao": None}
    return json.dumps(exemplo, indent=2, ensure_ascii=False)


def system_prompt() -> str:
    """Instrução fixa do extrator."""
    perguntas = "\n".join(f"   - {campo}: {p}" for campo, p in _PERGUNTAS.items())
    return (
        "Você extrai fatos de conversas de WhatsApp de uma marca que faz peças "
        "personalizadas com a foto do pet do cliente (Camu). Você atende dois "
        "públicos: consumidor final (B2C, DM) e petshops (B2B, consignação).\n\n"
        "Responda estas perguntas, e apenas estas:\n"
        f"{perguntas}\n\n"
        + _REGRAS.format(objecoes=_linhas_objecoes())
        + "\n\nFORMATO EXATO DA RESPOSTA:\n"
        + _formato_json()
        + "\n\nEm `evidencias`, inclua apenas as chaves cujos campos você marcou "
        "`true` (e `objecao`, se houver objeção). Omita o resto."
    )


def resumo_rolante(fatos_conhecidos: Mapping[str, bool], funil: str) -> str:
    """Resumo barato do que já se sabe da conversa (§2, custo).

    Não é uma chamada de LLM: o resumo é literalmente a lista de fatos que já
    foram estabelecidos. Reenviar o histórico completo a cada bloco custaria
    proporcional ao tamanho da conversa, e o único estado que o extrator
    precisa carregar entre blocos é justamente esse conjunto de fatos.
    """
    afirmados = [campo for campo in FATOS_BOOLEANOS if fatos_conhecidos.get(campo)]
    linhas = [f"Funil: {funil.upper()}"]
    if afirmados:
        linhas.append("Já estabelecido em blocos anteriores: " + ", ".join(afirmados))
        linhas.append(
            "Não repita esses campos como `true` sem evidência NOVA neste bloco — "
            "o sistema já os tem registrados."
        )
    else:
        linhas.append("Nada estabelecido ainda nesta conversa.")
    return "\n".join(linhas)


def formatar_mensagens(mensagens: Sequence[tuple[str, str]] | Iterable[tuple[str, str]]) -> str:
    """Transcreve `(direcao, texto)` no formato que o modelo lê.

    Rótulos explícitos em vez de "eu/você": o modelo precisa saber quem falou
    para responder `foto_pet_recebida` (cliente) e `previa_enviada` (Camu),
    que são simétricos e trocá-los inverteria o funil.
    """
    linhas = []
    for direcao, texto in mensagens:
        quem = "CLIENTE" if direcao == "in" else "CAMU"
        linhas.append(f"{quem}: {(texto or '').strip()}")
    return "\n".join(linhas)


def user_prompt(
    mensagens_novas: Sequence[tuple[str, str]],
    *,
    fatos_conhecidos: Mapping[str, bool] | None = None,
    funil: str = "b2c",
) -> str:
    return (
        f"{resumo_rolante(fatos_conhecidos or {}, funil)}\n\n"
        "MENSAGENS NOVAS (é sobre estas que você responde):\n"
        "---\n"
        f"{formatar_mensagens(mensagens_novas)}\n"
        "---"
    )
