"""Transporte isolado: a única fronteira de leitura e envio (§11).

"Toda leitura e envio passa por uma interface única: `enviar(contato, texto)` /
`receber(evento)`."

A Evolution API é frágil por natureza — viola o ToS do WhatsApp e o chip pode
cair a qualquer momento, independentemente do volume. Migrar para a Cloud API
oficial, ou trocar de chip, deve significar substituir um adaptador. Nenhum
módulo de domínio (regras, extração, fila, rascunhos) pode importar um cliente
concreto; todos falam com `Transporte`.

Uma decisão de tipo, não de disciplina: `enviar` exige `aprovado_por`, o nome
de quem autorizou. §1 diz que quem envia é humano, sempre, e §10 que disparo
automático acelera banimento. Um parâmetro obrigatório torna impossível um
laço de código enviar sozinho sem antes inventar um nome — o que é uma
fraude visível na auditoria, não um descuido invisível.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, runtime_checkable

from ..rules.sinais import ENTRADA, SAIDA


@dataclass(frozen=True)
class Destinatario:
    """Para quem enviar. `telefone` em claro só existe aqui e no adaptador (§12)."""

    telefone: str
    nome: str | None = None

    def __str__(self) -> str:
        return self.nome or self.telefone


@dataclass(frozen=True)
class EventoRecebido:
    """Uma mensagem que chegou, já normalizada — o formato que o CRM entende.

    `direcao` existe porque a Evolution API entrega também os ecos das nossas
    próprias mensagens (`fromMe`), e eles importam: são o `ultimo_outbound`
    que decide a bola e, com ela, a temperatura.
    """

    telefone: str
    texto: str
    enviada_em: datetime
    direcao: str = ENTRADA
    nome: str | None = None
    externa_id: str | None = None
    bruto: Mapping[str, Any] | None = None

    @property
    def is_inbound(self) -> bool:
        return self.direcao == ENTRADA


@dataclass(frozen=True)
class ResultadoEnvio:
    entregue: bool
    externa_id: str | None = None
    detalhe: str | None = None


class TransporteError(RuntimeError):
    """Falha de entrega. `retentavel` distingue queda de rede de recusa."""

    def __init__(self, transporte: str, mensagem: str, *, retentavel: bool = False):
        super().__init__(f"[{transporte}] {mensagem}")
        self.transporte = transporte
        self.retentavel = retentavel


class EnvioNaoAutorizadoError(TransporteError):
    """Envio sem um humano nomeado. Ver o docstring do módulo."""

    def __init__(self, transporte: str):
        super().__init__(
            transporte,
            "envio exige `aprovado_por` com o nome de quem autorizou — "
            "disparo automático não é suportado por decisão de projeto (§10)",
        )


@runtime_checkable
class Transporte(Protocol):
    """Contrato que todo adaptador de canal satisfaz."""

    nome: str

    def enviar(
        self, contato: Destinatario, texto: str, *, aprovado_por: str
    ) -> ResultadoEnvio:
        """Entrega `texto` a `contato`. `aprovado_por` é obrigatório."""
        ...

    def receber(self, evento: Mapping[str, Any]) -> EventoRecebido | None:
        """Normaliza um payload de webhook, ou `None` se não for mensagem.

        Devolver `None` é o caminho normal para os muitos eventos que não são
        mensagem (status de conexão, recibo de leitura, presença). Não é erro.
        """
        ...


def validar_aprovacao(transporte: str, aprovado_por: str | None) -> str:
    """Guarda compartilhada por todos os adaptadores."""
    nome = (aprovado_por or "").strip()
    if not nome:
        raise EnvioNaoAutorizadoError(transporte)
    return nome


__all__ = [
    "ENTRADA",
    "SAIDA",
    "Destinatario",
    "EnvioNaoAutorizadoError",
    "EventoRecebido",
    "ResultadoEnvio",
    "Transporte",
    "TransporteError",
    "validar_aprovacao",
]
