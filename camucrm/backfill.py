"""Backfill do histórico existente (§8).

"Rodar a extração sobre as conversas que já existem. É o que dá base de
comparação desde o dia um." E, por §13, é a primeira coisa a rodar — conversa
que passou sem ser instrumentada não volta.

O cuidado que §8 manda estar no código está aqui: o backfill recupera o estado
*final*, não *quando* cada transição ocorreu. Todo evento gerado por ele leva
`origem='backfill'`, e `metrics.py` exclui esses eventos de qualquer métrica de
tempo — senão a média de duração por estágio fica contaminada por timestamps
inventados. Métricas de conversão (quantos chegaram a cada estágio) podem usar
backfill à vontade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .db import Database
from .extraction.extractor import Extrator, ResultadoExtracao
from .rules.estagio import ORIGEM_BACKFILL
from .rules.sinais import ENTRADA, SAIDA
from .taxonomia import B2B, B2C

logger = logging.getLogger("camucrm.backfill")


@dataclass
class ResumoBackfill:
    conversas: int = 0
    mensagens: int = 0
    extraidas: int = 0
    falhas: int = 0
    democoes: int = 0

    def __str__(self) -> str:
        return (
            f"{self.conversas} conversa(s), {self.mensagens} mensagem(ns), "
            f"{self.extraidas} extraída(s), {self.falhas} falha(s), "
            f"{self.democoes} campo(s) rebaixado(s)"
        )


def importar_conversas(
    db: Database, registros: Iterable[Mapping[str, Any]]
) -> ResumoBackfill:
    """Carrega conversas históricas no banco, sem extrair nada ainda.

    Formato de cada registro::

        {
          "telefone": "5511999998888",
          "nome": "Ana",
          "tipo": "b2c",                 # ou "b2b"
          "origem": "instagram",         # opcional
          "mensagens": [
            {"direcao": "in", "texto": "oi", "enviada_em": "2026-07-01T10:00:00Z"},
            ...
          ]
        }

    A importação é idempotente por `externa_id` quando ele existe. Sem
    `externa_id`, reimportar o mesmo dump duplica mensagens — por isso o dump
    de origem deve trazer os ids do WhatsApp sempre que os tiver.
    """
    resumo = ResumoBackfill()
    for registro in registros:
        telefone = str(registro.get("telefone") or "").strip()
        if not telefone:
            logger.warning("Registro sem telefone, ignorado: %r", registro)
            continue
        tipo = str(registro.get("tipo") or B2C).lower()
        if tipo not in (B2B, B2C):
            tipo = B2C
        contato = db.upsert_contato(
            telefone,
            nome=registro.get("nome"),
            tipo=tipo,
            origem=registro.get("origem") or "backfill",
        )
        conversa = db.get_or_create_conversa(contato.id, funil=tipo)
        resumo.conversas += 1

        for mensagem in registro.get("mensagens") or []:
            direcao = str(mensagem.get("direcao") or ENTRADA).lower()
            if direcao not in (ENTRADA, SAIDA):
                continue
            inserida = db.registrar_mensagem(
                conversa.id,
                direcao,
                str(mensagem.get("texto") or ""),
                _momento(mensagem.get("enviada_em")),
                externa_id=mensagem.get("externa_id"),
            )
            if inserida is not None:
                resumo.mensagens += 1
    return resumo


def extrair_historico(
    db: Database,
    extrator: Extrator,
    *,
    limite: int = 1000,
    agora: datetime | None = None,
) -> tuple[ResumoBackfill, list[ResultadoExtracao]]:
    """Roda a extração sobre tudo que já está no banco, marcando a origem.

    `forcar=True` porque o objetivo do backfill é justamente reler conversas
    inteiras, inclusive as que já têm `ultima_mensagem_processada_id` de uma
    importação anterior.
    """
    agora = agora or datetime.now(timezone.utc)
    resumo = ResumoBackfill()
    resultados: list[ResultadoExtracao] = []

    for conversa in db.listar_conversas_abertas(limite=limite):
        resumo.conversas += 1
        try:
            resultado = extrator.processar_conversa(
                conversa.id, agora=agora, origem=ORIGEM_BACKFILL, forcar=True
            )
        except Exception as exc:  # noqa: BLE001 - uma conversa não derruba o lote
            logger.exception("Backfill falhou na conversa %s: %s", conversa.id, exc)
            resumo.falhas += 1
            continue
        resultados.append(resultado)
        resumo.mensagens += resultado.mensagens_processadas
        resumo.democoes += len(resultado.democoes)
        if resultado.erro:
            resumo.falhas += 1
        else:
            resumo.extraidas += 1

    logger.info("Backfill concluído: %s", resumo)
    return resumo, resultados


def _momento(bruto: Any) -> datetime:
    """Converte o timestamp do dump, tolerando ISO com Z e epoch."""
    if isinstance(bruto, datetime):
        return bruto if bruto.tzinfo else bruto.replace(tzinfo=timezone.utc)
    if isinstance(bruto, (int, float)):
        return datetime.fromtimestamp(bruto, tz=timezone.utc)
    if isinstance(bruto, str) and bruto.strip():
        texto = bruto.strip().replace("Z", "+00:00")
        try:
            momento = datetime.fromisoformat(texto)
        except ValueError:
            logger.warning("Timestamp ilegível no dump: %r", bruto)
            return datetime.now(timezone.utc)
        return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)
