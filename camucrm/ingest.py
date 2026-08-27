"""Ingestão de um evento de mensagem: o caminho único de entrada.

Compartilhado pelo webhook (`camucrm/webhook.py`) e pela CLI
(`camucrm ingerir`). Existe como módulo próprio para que os dois nunca
divirjam: duas cópias da mesma lógica de entrada acabam testando versões
diferentes do mesmo caminho, e a divergência só aparece em produção.

O evento chega já normalizado pelo transporte (§11) — este módulo não sabe o
que é Evolution API, nem WhatsApp, nem o formato de nenhum webhook.
"""

from __future__ import annotations

import hashlib
import json
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


def _externa_id_efetivo(evento: EventoRecebido) -> str | None:
    """`evento.externa_id`, ou um hash estável do payload cru quando ausente.

    Change `ingestao-a-prova-de-falha`, design.md: "computar um hash estável
    do payload cru (...) como um `externa_id` sintético quando o campo real
    está ausente — mesma ideia que `backfill-seguro-para-reexecucao` usa
    para mensagens sem id de origem. Isso estende a proteção de dedupe sem
    exigir dois índices/dois caminhos de código" — `mensagens_externa_id_idx`
    já é único sobre `externa_id IS NOT NULL`; preencher esse campo com um
    hash sintético (em vez de deixá-lo `NULL`) basta para proteger reentrega
    de um evento sem `key.id`.

    Sem `evento.bruto` (evento já normalizado, construído direto em teste ou
    por um caminho que não guarda o payload cru) não há o que hashear —
    devolve `None`, mesmo comportamento de antes deste change.
    """
    if evento.externa_id is not None:
        return evento.externa_id
    if evento.bruto is None:
        return None
    canonico = json.dumps(evento.bruto, sort_keys=True, default=str)
    return f"hash:{hashlib.md5(canonico.encode('utf-8')).hexdigest()}"


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
    externa_id = _externa_id_efetivo(evento)

    # Change `ingestao-a-prova-de-falha`, spec.md "Cadeia de ingestão é
    # transacional": os três rodam na MESMA transação Postgres — uma falha
    # em qualquer ponto do meio não deixa contato/conversa gravados sem a
    # mensagem correspondente (`Database.transacao`/`_conn_ou`).
    with db.transacao() as conn:
        contato = db.upsert_contato(
            evento.telefone, nome=evento.nome, tipo=tipo, origem=origem, conn=conn
        )
        conversa = db.get_or_create_conversa(contato.id, conn=conn)

        inserida = db.registrar_mensagem(
            conversa.id,
            evento.direcao,
            evento.texto,
            evento.enviada_em,
            externa_id=externa_id,
            conn=conn,
        )
    if inserida is None:
        # Webhook reentregue. Não é erro, e não pode mover o relógio da
        # conversa — senão a temperatura oscilaria sem ninguém ter falado.
        logger.debug("Mensagem duplicada ignorada (externa_id=%s)", externa_id)
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
