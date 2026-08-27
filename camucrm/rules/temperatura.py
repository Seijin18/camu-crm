"""Temperatura: regra determinística sobre tempo e reciprocidade, não sentimento.

§5 do documento. Análise de sentimento responde a pergunta errada — o que
prevê fechamento é reciprocidade e ritmo: cliente educado e sumido é frio,
cliente seco que responde em 2 minutos é quente.

Cada classificação devolve o sinal que disparou. Quando o Marcos discordar,
ele vê exatamente qual condição bateu, em vez de reconstruir a regra.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..taxonomia import (
    BOLA_CAMU,
    BOLA_CLIENTE,
    CAUSADA_POR_CAMU,
    DIAS_ESFRIANDO,
    ENCERRADO,
    ESFRIANDO,
    FRIO,
    HORAS_MORNO,
    HORAS_QUENTE,
    MAX_FOLLOWUPS,
    MORNO,
    QUENTE,
)
from .sinais import SinaisConversa


@dataclass(frozen=True)
class Classificacao:
    """Temperatura + o sinal que a produziu (§5: "auditável")."""

    temperatura: str
    sinal: str

    def __str__(self) -> str:
        return f"{self.temperatura.upper()} ({self.sinal})"


def classificar(
    sinais: SinaisConversa, fatos: Mapping[str, bool] | None = None
) -> Classificacao:
    """Classifica a conversa na escala QUENTE..ENCERRADO.

    A ordem de avaliação é a própria política: ENCERRADO primeiro porque é
    terminal (uma conversa encerrada que recebe resposta deixa de ter 2
    follow-ups sem retorno, e volta sozinha para QUENTE — sem caso especial);
    depois QUENTE, que é dívida operacional e não pode ser mascarada por
    nenhuma outra condição.
    """
    fatos = fatos or {}

    # --- ENCERRADO -------------------------------------------------------
    if fatos.get("recusa_explicita"):
        return Classificacao(ENCERRADO, "recusa explícita")
    if sinais.followups_sem_retorno >= MAX_FOLLOWUPS:
        return Classificacao(
            ENCERRADO, f"{sinais.followups_sem_retorno} follow-ups sem retorno"
        )

    # --- QUENTE ----------------------------------------------------------
    if sinais.bola_com == BOLA_CAMU:
        return Classificacao(QUENTE, "bola com a Camu")
    horas = sinais.horas_desde_inbound
    if horas is not None and horas < HORAS_QUENTE:
        return Classificacao(QUENTE, f"cliente respondeu há {horas:.1f}h")
    # Change `estagio-reabertura-manual-e-relogio`: "avançou hoje" só esquenta
    # quando o gatilho foi do CLIENTE — avanço 100% causado pela Camu (prévia
    # enviada, preço apresentado, proposta B2B sem resposta) não é
    # reciprocidade (§5), e `avancou_causada_por` vem de
    # `rules.estagio.Derivacao.causada_por` via `Transicao`.
    if sinais.avancou_estagio_hoje and sinais.avancou_causada_por != CAUSADA_POR_CAMU:
        return Classificacao(QUENTE, "avançou de estágio nas últimas 24h")

    # --- MORNO / ESFRIANDO / FRIO ----------------------------------------
    # Daqui para baixo a bola está com o cliente e o que resta é medir o
    # silêncio. `dias_sem_resposta` conta do último inbound quando existe e da
    # nossa primeira mensagem quando o cliente nunca falou — é o que faz um
    # petshop em P1, abordado ontem, cair em MORNO em vez de escapar da
    # escala por não ter "última mensagem dele".
    dias = sinais.dias_sem_resposta
    if dias is None:
        return Classificacao(MORNO, "conversa sem mensagens ainda")

    horas_silencio = dias * 24
    if horas_silencio < HORAS_MORNO:
        return Classificacao(MORNO, f"silêncio de {horas_silencio:.0f}h, bola com o cliente")

    if dias <= DIAS_ESFRIANDO and sinais.followups_sem_retorno == 0:
        return Classificacao(ESFRIANDO, f"{dias:.1f} dias sem resposta, sem follow-up")

    if sinais.followups_sem_retorno >= 1:
        return Classificacao(
            FRIO, f"{sinais.followups_sem_retorno} follow-up sem retorno"
        )
    return Classificacao(FRIO, f"{dias:.1f} dias sem resposta")


def bola_label(bola: str) -> str:
    return {BOLA_CAMU: "com a Camu", BOLA_CLIENTE: "com o cliente"}.get(bola, bola)
