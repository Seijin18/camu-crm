"""Rascunho de resposta: duas opções, para o humano escolher em vez de aprovar.

§10 do documento. A última restrição é a que mais importa: "rascunho único
vira aprovação automática. Duas opções obrigam a ler."

Divisão de trabalho igual à da extração — o LLM escreve, a regra verifica. As
restrições que dão para conferir deterministicamente (número de opções,
tamanho, preço em S1/S2, tabela completa) são conferidas aqui, e uma violação
volta ao modelo uma vez com o motivo. O que não dá para conferir sem julgamento
(tom infantilizado) vira aviso ao humano, não recusa automática: uma lista de
palavras proibidas recusaria "cachorrinho", que é português normal.

E o que este módulo nunca faz: enviar. §10 é categórico — disparo automático em
API não oficial acelera banimento, e produto personalizado exige contexto que o
modelo não tem.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Sequence

from .llm import LlmClient, LlmIndisponivelError
from .rules.sinais import SinaisConversa
from .taxonomia import (
    FRIO,
    MAX_FOLLOWUPS,
    estagio_label,
)

logger = logging.getLogger("camucrm.rascunhos")

MIN_LINHAS = 2
MAX_LINHAS = 4

# Estágios em que abrir com preço queima o lead (§10): o cliente ainda não viu
# o resultado, então o número chega sem nada que o justifique.
ESTAGIOS_SEM_PRECO = frozenset({"S1", "S2"})

_PRECO = re.compile(r"R\$\s*\d", re.IGNORECASE)

# Sinais de infantilização que o humano deve olhar antes de mandar. Aviso, não
# bloqueio — ver o docstring do módulo.
_AVISO_TOM = (
    "fotinha",
    "pedidinho",
    "produtinho",
    "amiguinho",
    "bebezinho",
    "peludinho",
    "confere aí",
)


@dataclass(frozen=True)
class Rascunho:
    """Resultado da geração: duas opções, ou a recusa de gerar.

    `encerrar=True` é uma resposta legítima e não um erro — §10 manda o
    gerador se recusar quando a conversa já esgotou o teto de toque.
    """

    opcoes: tuple[str, ...] = ()
    encerrar: bool = False
    motivo: str | None = None
    avisos: tuple[str, ...] = field(default_factory=tuple)

    def __str__(self) -> str:
        if self.encerrar:
            return f"ENCERRAR — {self.motivo}"
        partes = []
        for i, opcao in enumerate(self.opcoes, 1):
            partes.append(f"Opção {i}:\n{opcao}")
        if self.avisos:
            partes.append("Avisos: " + "; ".join(self.avisos))
        return "\n\n".join(partes)


class RascunhoInvalidoError(RuntimeError):
    """O modelo não produziu duas opções dentro das restrições."""


def deve_encerrar(temperatura: str, followups_enviados: int) -> str | None:
    """Motivo para não gerar rascunho, ou `None` para seguir.

    Duas condições, ambas de §6/§10: FRIO com um follow-up já gasto (o próximo
    toque seria o último e §6 diz que essa conversa não aparece na fila), e o
    teto de 2 já atingido.
    """
    if followups_enviados >= MAX_FOLLOWUPS:
        return f"teto de {MAX_FOLLOWUPS} follow-ups atingido"
    if temperatura == FRIO and followups_enviados >= 1:
        return "FRIO com follow-up já enviado — encerrar em vez de insistir"
    return None


def system_prompt(playbook: str | None = None) -> str:
    base = (
        "Você rascunha respostas de WhatsApp para a Camu, marca que faz peças "
        "personalizadas com a foto do pet do cliente.\n\n"
        "RESTRIÇÕES (todas obrigatórias):\n"
        f"- Cada opção tem de {MIN_LINHAS} a {MAX_LINHAS} linhas. Texto longo mata interesse.\n"
        "- Voz direta, neutra a consultiva. Nunca diminutivo, nunca infantilização.\n"
        "- Nunca a tabela de preço completa — no máximo um preço relevante por vez.\n"
        "- Em S1 e S2, nunca abra com preço: peça a foto do pet.\n"
        "- Escreva SEMPRE duas opções diferentes entre si, não duas versões da "
        "mesma frase. Quem lê precisa escolher, não aprovar.\n\n"
        'Responda apenas com JSON: {"opcoes": ["texto da opção 1", "texto da opção 2"]}'
    )
    if playbook:
        base += f"\n\nREFERÊNCIA DE TOM (playbook):\n{playbook.strip()}"
    return base


def user_prompt(
    historico: Sequence[tuple[str, str]],
    *,
    estagio: str,
    temperatura: str,
    funil: str,
    objecao: str | None = None,
) -> str:
    linhas = [
        f"Funil: {funil.upper()}",
        f"Estágio: {estagio} ({estagio_label(estagio)})",
        f"Temperatura: {temperatura.upper()}",
    ]
    if objecao:
        linhas.append(f"Objeção registrada: {objecao}")
    transcricao = "\n".join(
        f"{'CLIENTE' if d == 'in' else 'CAMU'}: {(t or '').strip()}"
        for d, t in historico
    )
    return "\n".join(linhas) + "\n\nCONVERSA:\n---\n" + transcricao + "\n---"


def validar_opcoes(opcoes: Sequence[str], *, estagio: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Confere as restrições verificáveis. Levanta em violação dura.

    Devolve `(opcoes_limpas, avisos)`. Avisos são de tom e não bloqueiam.
    """
    limpas = tuple(o.strip() for o in opcoes if isinstance(o, str) and o.strip())
    if len(limpas) != 2:
        raise RascunhoInvalidoError(
            f"esperadas exatamente 2 opções, vieram {len(limpas)}"
        )
    if limpas[0].casefold() == limpas[1].casefold():
        raise RascunhoInvalidoError("as duas opções são idênticas")

    avisos: list[str] = []
    for indice, opcao in enumerate(limpas, 1):
        linhas = [linha for linha in opcao.splitlines() if linha.strip()]
        if not (MIN_LINHAS <= len(linhas) <= MAX_LINHAS):
            raise RascunhoInvalidoError(
                f"opção {indice} tem {len(linhas)} linha(s); "
                f"o limite é {MIN_LINHAS} a {MAX_LINHAS}"
            )
        precos = _PRECO.findall(opcao)
        if len(precos) > 1:
            raise RascunhoInvalidoError(
                f"opção {indice} traz {len(precos)} preços — um preço relevante por vez"
            )
        if estagio in ESTAGIOS_SEM_PRECO and precos:
            raise RascunhoInvalidoError(
                f"opção {indice} abre com preço em {estagio}; peça a foto do pet"
            )
        baixa = opcao.casefold()
        for termo in _AVISO_TOM:
            if termo in baixa:
                avisos.append(f"opção {indice}: tom possivelmente infantilizado ({termo!r})")
    return limpas, tuple(avisos)


def gerar(
    llm: LlmClient,
    historico: Sequence[tuple[str, str]],
    *,
    estagio: str,
    temperatura: str,
    funil: str,
    sinais: SinaisConversa | None = None,
    followups_enviados: int | None = None,
    objecao: str | None = None,
    playbook: str | None = None,
) -> Rascunho:
    """Gera duas opções de resposta, ou recusa e devolve `encerrar`.

    Uma única retentativa em caso de violação, com o motivo devolvido ao
    modelo. Duas seria custo sem retorno: se a primeira correção não resolveu,
    o problema é o prompt, e §7 diz para tratar isso rodando o eval, não
    insistindo em produção.
    """
    enviados = (
        followups_enviados
        if followups_enviados is not None
        else (sinais.followups_enviados if sinais else 0)
    )
    motivo = deve_encerrar(temperatura, enviados)
    if motivo:
        return Rascunho(encerrar=True, motivo=motivo)

    system = system_prompt(playbook)
    pedido = user_prompt(
        historico, estagio=estagio, temperatura=temperatura, funil=funil, objecao=objecao
    )

    ultima_falha: str | None = None
    for tentativa in (1, 2):
        entrada = pedido if ultima_falha is None else (
            f"{pedido}\n\nA tentativa anterior foi recusada: {ultima_falha}. "
            "Corrija e responda de novo, no mesmo formato JSON."
        )
        try:
            bruto = llm.completar(system, entrada, json_estrito=True)
            opcoes = _extrair_opcoes(bruto)
            limpas, avisos = validar_opcoes(opcoes, estagio=estagio)
            return Rascunho(opcoes=limpas, avisos=avisos)
        except RascunhoInvalidoError as exc:
            ultima_falha = str(exc)
            logger.info("Rascunho recusado (tentativa %s): %s", tentativa, exc)
        except LlmIndisponivelError as exc:
            raise RascunhoInvalidoError(f"LLM indisponível: {exc}") from exc

    raise RascunhoInvalidoError(
        f"não foi possível gerar duas opções válidas: {ultima_falha}"
    )


def _extrair_opcoes(bruto: str) -> list[str]:
    texto = (bruto or "").strip()
    cerca = re.search(r"```(?:json)?\s*(.*?)```", texto, re.DOTALL)
    if cerca:
        texto = cerca.group(1).strip()
    try:
        carregado = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise RascunhoInvalidoError(f"resposta não é JSON: {exc}") from exc
    if isinstance(carregado, list):
        return [str(o) for o in carregado]
    if isinstance(carregado, dict):
        opcoes = carregado.get("opcoes") or carregado.get("options")
        if isinstance(opcoes, list):
            return [str(o) for o in opcoes]
    raise RascunhoInvalidoError("JSON sem a chave `opcoes` com duas strings")
