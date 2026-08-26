"""Sinais de conversa: tudo que as regras precisam e o LLM não fornece.

§1 divide o trabalho: o LLM extrai fatos da linguagem; as regras decidem a
partir de fatos **e de tempo**. Tempo, reciprocidade e contagem de mensagens
não são linguagem — saem daqui, do histórico de mensagens, sem custo de LLM e
com o mesmo resultado a cada replay.

Módulo puro: recebe registros de mensagem já carregados, não toca o banco.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from ..taxonomia import BOLA_CAMU, BOLA_CLIENTE, B2C

ENTRADA = "in"    # cliente -> Camu
SAIDA = "out"     # Camu -> cliente
DIRECOES = (ENTRADA, SAIDA)


@dataclass(frozen=True)
class Mensagem:
    """Uma linha de `mensagens`, no mínimo que as regras precisam."""

    direcao: str
    enviada_em: datetime
    texto: str = ""

    @property
    def is_inbound(self) -> bool:
        return self.direcao == ENTRADA


@dataclass(frozen=True)
class SinaisConversa:
    """Estado temporal e manual de uma conversa, no instante `agora`.

    Separado dos fatos de propósito: fatos vêm do LLM e são caros; sinais são
    derivados de timestamps e de marcos que um humano registrou, e podem ser
    recalculados de graça a cada consulta da fila.
    """

    funil: str = B2C
    agora: datetime = datetime.now(timezone.utc)

    # Reciprocidade e ritmo (§5)
    tem_inbound: bool = False
    primeiro_inbound: datetime | None = None
    ultimo_inbound: datetime | None = None
    primeiro_outbound: datetime | None = None
    ultimo_outbound: datetime | None = None
    total_outbound: int = 0

    # Avanço observado (§3)
    inbound_apos_preco: bool = False
    proposta_apresentada: bool = False
    # Momento da mensagem que entregou a proposta (P3). Guardado, e não só o
    # booleano, porque é ele que carimba o evento de estágio — sem timestamp
    # real, a métrica de tempo da §8 não teria o que medir ao vivo.
    proposta_em: datetime | None = None
    avancou_estagio_hoje: bool = False
    estagio_maximo_alcancado: str | None = None

    # Follow-up (§6)
    followups_enviados: int = 0
    followups_sem_retorno: int = 0

    # Marcos que só um humano marca (§3: "manual")
    ganho: bool = False
    consignacao_assinada: bool = False
    primeira_reposicao: bool = False

    @property
    def bola_com(self) -> str:
        """Quem deve a próxima mensagem — o sinal de maior peso da §5.

        `camu` quer dizer que o cliente falou por último e a resposta é dívida
        nossa. Conversa sem nenhum inbound tem a bola com o cliente: fomos nós
        que abrimos e estamos esperando.
        """
        if self.ultimo_inbound is None:
            return BOLA_CLIENTE
        if self.ultimo_outbound is None:
            return BOLA_CAMU
        return BOLA_CAMU if self.ultimo_inbound > self.ultimo_outbound else BOLA_CLIENTE

    @property
    def horas_desde_inbound(self) -> float | None:
        """Horas desde a última mensagem do cliente; `None` se ele nunca falou."""
        if self.ultimo_inbound is None:
            return None
        return _horas_entre(self.ultimo_inbound, self.agora)

    @property
    def dias_desde_inbound(self) -> float | None:
        horas = self.horas_desde_inbound
        return None if horas is None else horas / 24.0

    @property
    def dias_sem_resposta(self) -> float | None:
        """Dias desde que ficamos esperando o cliente.

        Conta a partir da última mensagem *dele*; se ele nunca falou, a partir
        da primeira que mandamos. `None` quando não houve mensagem nenhuma —
        aí não há silêncio a medir, só uma conversa que não começou.
        """
        referencia = self.ultimo_inbound or self.ultimo_outbound
        if referencia is None:
            return None
        return _horas_entre(referencia, self.agora) / 24.0


def _horas_entre(inicio: datetime, fim: datetime) -> float:
    """Diferença em horas, tolerando datetime ingênuo (assume UTC).

    Timestamp sem timezone é um erro de origem, não motivo para derrubar a
    fila do dia: normalizamos para UTC e seguimos.
    """
    return max(0.0, (_aware(fim) - _aware(inicio)).total_seconds() / 3600.0)


def _aware(momento: datetime) -> datetime:
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def construir_sinais(
    mensagens: Sequence[Mensagem] | Iterable[Mensagem],
    *,
    funil: str = B2C,
    agora: datetime | None = None,
    preco_apresentado_em: datetime | None = None,
    autorizou_em: datetime | None = None,
    followups_enviados: int = 0,
    ultimo_followup_em: datetime | None = None,
    avancou_estagio_em: datetime | None = None,
    estagio_maximo_alcancado: str | None = None,
    ganho: bool = False,
    consignacao_assinada: bool = False,
    primeira_reposicao: bool = False,
) -> SinaisConversa:
    """Deriva os sinais de uma conversa a partir das mensagens dela.

    `preco_apresentado_em` / `autorizou_em` vêm de `fatos.extraido_em` — o
    momento em que o fato foi *registrado*, não necessariamente em que foi
    dito. Na operação ao vivo a diferença é de minutos (a extração roda sobre
    o delta logo depois) e não muda nenhuma decisão. No backfill a diferença é
    arbitrária, e é exatamente por isso que §8 manda excluir eventos de
    backfill de qualquer métrica de tempo.
    """
    agora = _aware(agora or datetime.now(timezone.utc))
    ordenadas = sorted(mensagens, key=lambda m: _aware(m.enviada_em))

    inbounds = [m for m in ordenadas if m.is_inbound]
    outbounds = [m for m in ordenadas if not m.is_inbound]
    ultimo_inbound = _aware(inbounds[-1].enviada_em) if inbounds else None
    ultimo_outbound = _aware(outbounds[-1].enviada_em) if outbounds else None

    inbound_apos_preco = bool(
        preco_apresentado_em
        and ultimo_inbound
        and ultimo_inbound > _aware(preco_apresentado_em)
    )
    # P3 (§3): "Proposta apresentada — Msg 2 entregue", isto é, uma mensagem
    # nossa depois de o lojista ter autorizado o envio do material.
    proposta_em = None
    if autorizou_em:
        posteriores = [
            _aware(m.enviada_em)
            for m in outbounds
            if _aware(m.enviada_em) > _aware(autorizou_em)
        ]
        proposta_em = min(posteriores) if posteriores else None
    proposta_apresentada = proposta_em is not None

    # "Sem retorno" é literal: nenhum inbound depois do follow-up que mandamos.
    followups_sem_retorno = followups_enviados
    if ultimo_followup_em and ultimo_inbound and ultimo_inbound > _aware(ultimo_followup_em):
        followups_sem_retorno = 0

    avancou_hoje = bool(
        avancou_estagio_em and (agora - _aware(avancou_estagio_em)) < timedelta(hours=24)
    )

    return SinaisConversa(
        funil=funil,
        agora=agora,
        tem_inbound=bool(inbounds),
        primeiro_inbound=_aware(inbounds[0].enviada_em) if inbounds else None,
        ultimo_inbound=ultimo_inbound,
        primeiro_outbound=_aware(outbounds[0].enviada_em) if outbounds else None,
        ultimo_outbound=ultimo_outbound,
        total_outbound=len(outbounds),
        inbound_apos_preco=inbound_apos_preco,
        proposta_apresentada=proposta_apresentada,
        proposta_em=proposta_em,
        avancou_estagio_hoje=avancou_hoje,
        estagio_maximo_alcancado=estagio_maximo_alcancado,
        followups_enviados=followups_enviados,
        followups_sem_retorno=followups_sem_retorno,
        ganho=ganho,
        consignacao_assinada=consignacao_assinada,
        primeira_reposicao=primeira_reposicao,
    )
