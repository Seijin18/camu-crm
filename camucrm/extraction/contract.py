"""Contrato de extração: o único formato que o LLM tem permissão de devolver.

§2 do documento de definições. O LLM responde perguntas factuais fechadas —
nunca estágio, temperatura, prioridade ou envio. Este módulo é puro (sem I/O,
sem rede) porque é ele que precisa ser testável linha a linha: é a fronteira
onde otimismo do modelo vira dado errado no histórico.

A regra estruturante: **todo `true` exige evidência literal**. Sem trecho, ou
com trecho que não aparece na conversa, o campo volta a `false` e a demoção é
registrada. Errar para menos custa tempo; errar para mais abandona um lead
quente achando que já foi tratado (§7).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..taxonomia import OBJECAO_OUTRO, OBJECOES

# Ordem fixa dos campos do contrato (§2). É também a ordem em que aparecem no
# prompt e no JSON de exemplo — mudar aqui muda o contrato, não a formatação.
FATOS_BOOLEANOS = (
    "foto_pet_recebida",
    "preco_apresentado",
    "previa_enviada",
    "intencao_compra_explicita",
    "recusa_explicita",
    "autorizou_envio_material",
    "visita_aceita",
)

CAMPO_OBJECAO = "objecao"
CAMPO_EVIDENCIAS = "evidencias"

# Direção exigida por campo (§2, mapeamento do change
# `literalidade-e-idempotencia-da-extracao`). Fatos que dependem de fala do
# CLIENTE não podem ser "confirmados" por uma pergunta ou script da própria
# Camu, e vice-versa — sem isto, a conferência de literalidade provava só que
# o trecho existe em ALGUM lugar da conversa, não que quem precisava dizê-lo
# disse. `objecao` fica de fora deste mapa de propósito: o contrato normativo
# (spec deste change) só exige direção para os 7 campos booleanos; a
# categoria de objeção continua conferida contra o corpus geral (os dois
# lados), como já era.
DIRECAO_CLIENTE = "in"
DIRECAO_CAMU = "out"

DIRECAO_POR_CAMPO: Mapping[str, str] = {
    "foto_pet_recebida": DIRECAO_CLIENTE,
    "preco_apresentado": DIRECAO_CAMU,
    "previa_enviada": DIRECAO_CAMU,
    "intencao_compra_explicita": DIRECAO_CLIENTE,
    "recusa_explicita": DIRECAO_CLIENTE,
    "autorizou_envio_material": DIRECAO_CLIENTE,
    "visita_aceita": DIRECAO_CLIENTE,
}

# Evidência curta demais casa com qualquer coisa e não prova nada ("ok", "sim"
# aparecem em toda conversa). Três caracteres é o piso: aceita "sim" — que é
# uma confirmação real e frequente em DM — e rejeita ruído de um ou dois
# caracteres. Medido após a normalização de `_fold`.
MIN_EVIDENCIA = 3

# Motivos de demoção. Fechados de propósito: o padrão das demoções é sinal de
# o que o prompt não está vendo (§7, loop de correção), e categoria livre não
# agrega.
SEM_EVIDENCIA = "sem_evidencia"
EVIDENCIA_CURTA = "evidencia_curta"
EVIDENCIA_NAO_LITERAL = "evidencia_nao_literal"
VALOR_INVALIDO = "valor_invalido"
OBJECAO_FORA_DA_LISTA = "objecao_fora_da_lista"
OBJECAO_OUTRO_SEM_TRECHO = "objecao_outro_sem_trecho"


class ContratoInvalidoError(ValueError):
    """A resposta do LLM não é sequer um objeto JSON interpretável.

    Distinta de uma demoção: demoção é um campo que o contrato rebaixa e
    segue em frente; isto é resposta que não dá para ler.
    """


@dataclass(frozen=True)
class Democao:
    """Um campo que o LLM afirmou e o contrato rebaixou, com o porquê."""

    campo: str
    motivo: str
    evidencia: str | None = None

    def __str__(self) -> str:
        trecho = f" ({self.evidencia!r})" if self.evidencia else ""
        return f"{self.campo}: {self.motivo}{trecho}"


@dataclass(frozen=True)
class Extracao:
    """Saída validada de uma rodada de extração.

    `fatos` já reflete as demoções — quem consome não precisa saber que houve
    rebaixamento. `democoes` existe para auditoria e para o eval (§7).
    """

    fatos: Mapping[str, bool]
    objecao: str | None = None
    evidencias: Mapping[str, str] = field(default_factory=dict)
    democoes: tuple[Democao, ...] = ()

    def __getitem__(self, campo: str) -> bool:
        return bool(self.fatos.get(campo, False))

    @property
    def afirmados(self) -> tuple[str, ...]:
        """Campos que sobreviveram à validação, em ordem de contrato."""
        return tuple(c for c in FATOS_BOOLEANOS if self.fatos.get(c))

    def to_dict(self) -> dict[str, Any]:
        """Serialização no formato exato do contrato (§2)."""
        payload: dict[str, Any] = {c: bool(self.fatos.get(c, False)) for c in FATOS_BOOLEANOS}
        payload[CAMPO_OBJECAO] = self.objecao
        payload[CAMPO_EVIDENCIAS] = dict(self.evidencias)
        return payload


def extracao_vazia() -> Extracao:
    """Extração neutra: nenhum fato afirmado.

    É o resultado de uma conversa sem nada de novo — e também o fallback
    seguro quando o LLM falha, porque `false` em tudo nunca avança estágio.
    """
    return Extracao(fatos={c: False for c in FATOS_BOOLEANOS})


# --------------------------------------------------------------------------
# Normalização para conferência de literalidade
# --------------------------------------------------------------------------


def _fold(texto: str) -> str:
    """Normaliza para comparar trecho contra conversa.

    Dobra acento e caixa e colapsa espaço em branco — pontuação é preservada
    de propósito: "quanto custa" e "quanto custa?" devem casar (o espaço é o
    que varia entre transcrições), mas afrouxar mais que isso transformaria a
    conferência de literalidade em conferência de palavras soltas, que é
    exatamente o que §2 quer impedir.

    `\\s` inclui `\\n` — por isso `build_corpus` NUNCA deve juntar mensagens
    com um separador de espaço em branco puro: `_fold` o colapsaria e a
    fronteira entre duas mensagens desapareceria (ver `SEPARADOR_MENSAGEM`).
    """
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


# Separador de fronteira entre mensagens (§2, change
# `literalidade-e-idempotencia-da-extracao`). Precisa estar FORA da classe
# `\s` — `"\n"` puro é colapsado por `_fold` (`re.sub(r"\s+", " ", ...)`), e
# nesse caso o fim de uma mensagem se cola ao começo da próxima, formando um
# trecho que ninguém disse mas que casa como se fosse contíguo. `"\x00"`
# nunca aparece em texto real de WhatsApp e sobrevive à normalização intacto,
# então a fronteira continua detectável (e continua impossível de casar como
# substring válido) depois do fold.
SEPARADOR_MENSAGEM = "\x00"


@dataclass(frozen=True)
class Corpus:
    """Corpus de literalidade, separado por quem falou (§2, direção da
    evidência).

    `cliente` só tem mensagens `in`, `camu` só tem mensagens `out` — um
    trecho que só existe do lado errado não deve casar contra o corpus do
    campo que exige o lado certo (ver `DIRECAO_POR_CAMPO`). `geral` funde os
    dois lados, na mesma fronteira preservada, e serve para o que não tem
    direção exigida (a objeção, e o fallback de compatibilidade).
    """

    cliente: str
    camu: str
    geral: str

    def para_campo(self, campo: str) -> str:
        direcao = DIRECAO_POR_CAMPO.get(campo)
        if direcao == DIRECAO_CLIENTE:
            return self.cliente
        if direcao == DIRECAO_CAMU:
            return self.camu
        return self.geral


def _juntar(textos: list[str]) -> str:
    return _fold(SEPARADOR_MENSAGEM.join(t for t in textos if t))


def build_corpus(mensagens: Iterable[tuple[str, str]]) -> Corpus:
    """Junta as mensagens da conversa em corpus separados por direção.

    `mensagens` é uma sequência de `(direcao, texto)` — mesmo formato que o
    prompt recebe. O separador entre mensagens (`SEPARADOR_MENSAGEM`) importa:
    sem ele, o fim de uma mensagem e o começo da seguinte formariam trechos
    que nunca foram ditos, e uma evidência inventada poderia casar por
    acidente; com um separador que `_fold` colapsa em espaço comum (como
    `"\\n"` puro), a proteção desaparece silenciosamente.
    """
    cliente, camu, geral = [], [], []
    for direcao, texto in mensagens:
        if not texto:
            continue
        geral.append(texto)
        if direcao == DIRECAO_CLIENTE:
            cliente.append(texto)
        elif direcao == DIRECAO_CAMU:
            camu.append(texto)
    return Corpus(cliente=_juntar(cliente), camu=_juntar(camu), geral=_juntar(geral))


def _coerce_bool(valor: Any) -> bool | None:
    """`True`/`False` a partir do que o LLM devolveu, ou `None` se ilegível."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        normalizado = valor.strip().lower()
        if normalizado in {"true", "sim", "yes"}:
            return True
        if normalizado in {"false", "nao", "não", "no", ""}:
            return False
    if valor is None:
        return False
    return None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_resposta_llm(bruto: str | Mapping[str, Any]) -> dict[str, Any]:
    """Extrai o objeto JSON da resposta do modelo.

    Tolera cerca de markdown (```json ... ```) e texto em volta, porque isso é
    formatação e não conteúdo. Não tolera ausência de objeto JSON — aí a
    resposta não é interpretável e vira `ContratoInvalidoError`.
    """
    if isinstance(bruto, Mapping):
        return dict(bruto)
    if not isinstance(bruto, str) or not bruto.strip():
        raise ContratoInvalidoError("resposta vazia do modelo")

    texto = bruto.strip()
    cerca = re.search(r"```(?:json)?\s*(.*?)```", texto, re.DOTALL)
    if cerca:
        texto = cerca.group(1).strip()

    try:
        carregado = json.loads(texto)
    except json.JSONDecodeError:
        inicio, fim = texto.find("{"), texto.rfind("}")
        if inicio == -1 or fim <= inicio:
            raise ContratoInvalidoError(
                f"nenhum objeto JSON na resposta: {bruto[:200]!r}"
            ) from None
        try:
            carregado = json.loads(texto[inicio : fim + 1])
        except json.JSONDecodeError as exc:
            raise ContratoInvalidoError(
                f"JSON inválido na resposta: {exc}"
            ) from exc

    if not isinstance(carregado, dict):
        raise ContratoInvalidoError(
            f"esperado objeto JSON, veio {type(carregado).__name__}"
        )
    return carregado


def validar(
    bruto: str | Mapping[str, Any],
    *,
    corpus: Corpus | None = None,
) -> Extracao:
    """Aplica o contrato à saída do LLM, rebaixando o que não se sustenta.

    `corpus` vem de `build_corpus`, já separado por direção. Quando ausente,
    a conferência de literalidade é pulada e só a exigência de evidência
    não-vazia vale — modo usado no eval, onde o rótulo humano não traz
    trecho. Em produção sempre passe o corpus: é ele que transforma "exige
    evidência" em "exige evidência verdadeira", e agora também "evidência
    dita por quem tinha que dizer" (§2, direção da evidência) — um trecho que
    só existe do lado errado da conversa (a Camu "confirmando" um fato que só
    o cliente pode afirmar, ou vice-versa) não sustenta o campo.
    """
    payload = parse_resposta_llm(bruto)
    evidencias_brutas = payload.get(CAMPO_EVIDENCIAS) or {}
    if not isinstance(evidencias_brutas, Mapping):
        evidencias_brutas = {}

    fatos: dict[str, bool] = {}
    evidencias: dict[str, str] = {}
    democoes: list[Democao] = []

    for campo in FATOS_BOOLEANOS:
        afirmado = _coerce_bool(payload.get(campo, False))
        if afirmado is None:
            democoes.append(Democao(campo, VALOR_INVALIDO, repr(payload.get(campo))))
            fatos[campo] = False
            continue
        if not afirmado:
            fatos[campo] = False
            continue

        evidencia = evidencias_brutas.get(campo)
        evidencia = evidencia.strip() if isinstance(evidencia, str) else ""
        corpus_campo = corpus.para_campo(campo) if corpus is not None else None
        motivo = _motivo_recusa(evidencia, corpus_campo)
        if motivo:
            democoes.append(Democao(campo, motivo, evidencia or None))
            fatos[campo] = False
            continue

        fatos[campo] = True
        evidencias[campo] = evidencia

    corpus_objecao = corpus.geral if corpus is not None else None
    objecao, evidencia_objecao, democao_objecao = _validar_objecao(
        payload.get(CAMPO_OBJECAO), evidencias_brutas.get(CAMPO_OBJECAO), corpus_objecao
    )
    if democao_objecao:
        democoes.append(democao_objecao)
    if objecao and evidencia_objecao:
        evidencias[CAMPO_OBJECAO] = evidencia_objecao

    return Extracao(
        fatos=fatos,
        objecao=objecao,
        evidencias=evidencias,
        democoes=tuple(democoes),
    )


def _motivo_recusa(evidencia: str, corpus: str | None) -> str | None:
    """Por que esta evidência não sustenta um `true` — ou `None` se sustenta."""
    if not evidencia:
        return SEM_EVIDENCIA
    dobrada = _fold(evidencia)
    if len(dobrada) < MIN_EVIDENCIA:
        return EVIDENCIA_CURTA
    if corpus is not None and dobrada not in corpus:
        return EVIDENCIA_NAO_LITERAL
    return None


def _validar_objecao(
    categoria: Any, trecho: Any, corpus: str | None
) -> tuple[str | None, str | None, Democao | None]:
    """Objeção só da lista fechada; `outro` exige trecho literal (§4).

    Categoria desconhecida não é descartada em silêncio: vira `outro` com o
    valor original como trecho, porque §4 usa a proporção de `outro` para
    decidir se falta uma categoria. Engolir o desconhecido esconderia
    exatamente o sinal que justifica a revisão mensal.
    """
    if categoria is None:
        return None, None, None
    if not isinstance(categoria, str) or not categoria.strip():
        return None, None, None

    normalizada = categoria.strip().lower()
    trecho_limpo = trecho.strip() if isinstance(trecho, str) else ""

    if normalizada not in OBJECOES:
        trecho_final = trecho_limpo or categoria.strip()
        return (
            OBJECAO_OUTRO,
            trecho_final,
            Democao(CAMPO_OBJECAO, OBJECAO_FORA_DA_LISTA, categoria.strip()),
        )

    if normalizada == OBJECAO_OUTRO:
        motivo = _motivo_recusa(trecho_limpo, corpus)
        if motivo:
            return (
                None,
                None,
                Democao(
                    CAMPO_OBJECAO,
                    OBJECAO_OUTRO_SEM_TRECHO if motivo == SEM_EVIDENCIA else motivo,
                    trecho_limpo or None,
                ),
            )

    return normalizada, trecho_limpo or None, None


def merge(anterior: Extracao, nova: Extracao) -> Extracao:
    """Combina extrações de blocos sucessivos da mesma conversa.

    Fato é monotônico: a foto do pet que chegou ontem não deixa de ter
    chegado porque o bloco de hoje não fala dela. §2 exige que reprocessar
    não regrida estágio, e estágio deriva de fato — então a monotonicidade
    precisa morar aqui, no fato, e não numa trava no estágio.

    A objeção é o oposto: é estado corrente, e a mais recente ganha — cliente
    que reclamava de preço e agora reclama de frete tem objeção `frete`. O
    histórico completo fica em `objecoes`, uma linha por ocorrência.
    """
    fatos = {
        campo: bool(anterior.fatos.get(campo)) or bool(nova.fatos.get(campo))
        for campo in FATOS_BOOLEANOS
    }
    evidencias = {**anterior.evidencias, **nova.evidencias}
    return Extracao(
        fatos=fatos,
        objecao=nova.objecao if nova.objecao is not None else anterior.objecao,
        evidencias=evidencias,
        democoes=anterior.democoes + nova.democoes,
    )


def momento_da_evidencia(
    evidencia: str | None, mensagens: Iterable[tuple[str, Any]]
) -> Any | None:
    """Quando foi dita a mensagem que sustenta esta evidência.

    `mensagens` é uma sequência de `(texto, momento)`. Devolve o momento da
    **última** mensagem que contém o trecho, ou `None` se não achar.

    Por que isto existe: o momento em que um fato foi *extraído* é sempre
    posterior a todas as mensagens do bloco que o produziu. Usá-lo como "quando
    o preço foi apresentado" tornaria S5 ("respondeu ao preço") e P3 ("msg 2
    após autorização") inalcançáveis numa única passada de extração — a
    resposta do cliente estaria sempre *antes* do carimbo do fato. O momento da
    mensagem que carrega a evidência é o dado real, e é o mesmo no replay e no
    backfill.

    A última, e não a primeira: um preço citado de novo mais adiante é o preço
    que vale, e é a resposta a *ele* que caracteriza negociação.
    """
    if not evidencia:
        return None
    dobrada = _fold(evidencia)
    if len(dobrada) < MIN_EVIDENCIA:
        return None
    encontrado = None
    for texto, momento in mensagens:
        if dobrada in _fold(texto or ""):
            encontrado = momento
    return encontrado
