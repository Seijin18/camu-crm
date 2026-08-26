"""Transporte de mesa: imprime em vez de enviar.

Padrão do sistema. §10 é explícito — "Nunca enviar automaticamente" — e um
transporte que não fala com a rede é a forma mais barata de garantir que
nenhum experimento, teste ou script mal calibrado toque no chip.

Também é o adaptador usado nos testes: satisfaz o mesmo protocolo, registra o
que "enviou", e nunca abre socket.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from .base import (
    Destinatario,
    EventoRecebido,
    ResultadoEnvio,
    validar_aprovacao,
)

logger = logging.getLogger("camucrm.transporte.console")


class ConsoleTransporte:
    nome = "console"

    def __init__(self, *, silencioso: bool = False):
        self.silencioso = silencioso
        self.enviados: list[tuple[Destinatario, str, str]] = []

    def enviar(
        self, contato: Destinatario, texto: str, *, aprovado_por: str
    ) -> ResultadoEnvio:
        quem = validar_aprovacao(self.nome, aprovado_por)
        self.enviados.append((contato, texto, quem))
        if not self.silencioso:
            logger.info("[dry-run] para %s (aprovado por %s):\n%s", contato, quem, texto)
        return ResultadoEnvio(
            entregue=False,
            externa_id=None,
            detalhe=f"dry-run: nada foi enviado (aprovado por {quem})",
        )

    def receber(self, evento: Mapping[str, Any]) -> EventoRecebido | None:
        """Aceita o formato simples usado por scripts e fixtures."""
        telefone = evento.get("telefone") or evento.get("from")
        texto = evento.get("texto") or evento.get("text")
        if not telefone or texto is None:
            return None
        enviada_em = evento.get("enviada_em") or datetime.now(timezone.utc)
        return EventoRecebido(
            telefone=str(telefone),
            texto=str(texto),
            enviada_em=enviada_em,
            direcao=evento.get("direcao", "in"),
            nome=evento.get("nome"),
            externa_id=evento.get("externa_id"),
            bruto=dict(evento),
        )
