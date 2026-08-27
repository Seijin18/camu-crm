"""Ingestão de um evento de mensagem: o caminho único de entrada.

Compartilhado pelo webhook (`camucrm/webhook.py`) e pela CLI
(`camucrm ingerir`). Existe como módulo próprio para que os dois nunca
divirjam: duas cópias da mesma lógica de entrada acabam testando versões
diferentes do mesmo caminho, e a divergência só aparece em produção.

O evento chega já normalizado pelo transporte (§11) — este módulo não sabe o
que é Evolution API, nem WhatsApp, nem o formato de nenhum webhook.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from . import acoes
from .db import Database
from .pipeline import EstadoConversa, recalcular
from .rules.sinais import SAIDA
from .taxonomia import B2B, B2C
from .transport.base import EventoRecebido

logger = logging.getLogger("camucrm.ingest")


@dataclass(frozen=True)
class ResultadoIngestao:
    """O que a ingestão de um evento produziu."""

    conversa_id: int | None
    contato: str | None
    duplicada: bool = False
    ignorada: bool = False
    estado: EstadoConversa | None = None

    def __str__(self) -> str:
        if self.ignorada:
            return "evento ignorado (não é mensagem de conversa)"
        if self.duplicada:
            return f"#{self.conversa_id} mensagem já conhecida; nada mudou"
        estado = self.estado
        return (
            f"#{self.conversa_id} {self.contato}: "
            f"{estado.estagio}, {estado.temperatura.upper()}"
            if estado
            else f"#{self.conversa_id} {self.contato}"
        )


def ingerir(
    db: Database,
    evento: EventoRecebido | None,
    *,
    origem: str = "whatsapp",
    tipo_padrao: str = B2C,
    agora: datetime | None = None,
) -> ResultadoIngestao:
    """Grava a mensagem e recalcula o estado da conversa.

    `evento=None` — o que o transporte devolve para tudo que não é mensagem
    (status de conexão, recibo de leitura, presença) — é o caminho normal e
    silencioso, não erro.

    O contato nasce `b2c` por padrão. Classificar petshop automaticamente
    (por nome, por horário, por qualquer heurística) seria inferência, e §1
    tira inferência de decisão de negócio do caminho automático: quem sabe que
    aquele número é uma loja marca com `camucrm marcar`. Um lead classificado
    errado entra no funil errado e sai da fila pela regra errada, e ninguém
    descobre por quê.
    """
    if evento is None:
        return ResultadoIngestao(None, None, ignorada=True)

    tipo = tipo_padrao if tipo_padrao in (B2B, B2C) else B2C
    contato = db.upsert_contato(
        evento.telefone, nome=evento.nome, tipo=tipo, origem=origem
    )
    conversa = db.get_or_create_conversa(contato.id)

    inserida = db.registrar_mensagem(
        conversa.id,
        evento.direcao,
        evento.texto,
        evento.enviada_em,
        externa_id=evento.externa_id,
    )
    if inserida is None:
        # Webhook reentregue. Não é erro, e não pode mover o relógio da
        # conversa — senão a temperatura oscilaria sem ninguém ter falado.
        logger.debug(
            "Mensagem duplicada ignorada (externa_id=%s)", evento.externa_id
        )
        return ResultadoIngestao(conversa.id, contato.label, duplicada=True)

    if evento.direcao == SAIDA:
        # Caminho 2 de vínculo rascunho -> mensagem (design.md, change
        # `rascunho-registrado`): só para mensagem NOVA (não duplicata) e só
        # `out` — o eco da Evolution é a única fonte que confirma o que foi
        # de fato enviado.
        acoes.reconciliar_rascunho(db, conversa.id, inserida, evento.texto)

    atualizada = db.get_conversa(conversa.id)
    assert atualizada is not None
    estado = recalcular(db, atualizada, agora=agora)

    logger.info(
        "Ingerido #%s %s (%s): %s / %s",
        conversa.id,
        contato.label,
        evento.direcao,
        estado.estagio,
        estado.temperatura,
    )
    return ResultadoIngestao(conversa.id, contato.label, estado=estado)
