"""Adaptador Evolution API (Baileys) — WhatsApp não oficial.

O documento (§11) é direto sobre o que este arquivo é: a peça frágil. Viola o
ToS do WhatsApp e o chip cai a qualquer momento, independentemente do volume.
Substituí-lo pela Cloud API oficial deve custar este arquivo e mais nada — se
algum dia custar mais, a fronteira vazou e é isso que precisa ser corrigido,
não o adaptador.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

import requests

from .base import (
    ENTRADA,
    SAIDA,
    Destinatario,
    EventoRecebido,
    ResultadoEnvio,
    TransporteError,
    validar_aprovacao,
)

logger = logging.getLogger("camucrm.transporte.evolution")

# Problema de transporte vale retentar; erro HTTP significa que a API
# respondeu e recusou — repetir não muda nada.
_RETENTAVEIS = (requests.ConnectionError, requests.Timeout)


class EvolutionTransporte:
    nome = "evolution"

    def __init__(self, base_url: str, api_key: str, instancia: str, *, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.instancia = instancia
        self.timeout = timeout

    # -- envio ------------------------------------------------------------

    def enviar(
        self, contato: Destinatario, texto: str, *, aprovado_por: str
    ) -> ResultadoEnvio:
        quem = validar_aprovacao(self.nome, aprovado_por)
        url = f"{self.base_url}/message/sendText/{self.instancia}"
        try:
            resposta = requests.post(
                url,
                headers={"apikey": self.api_key, "Content-Type": "application/json"},
                json={"number": _so_digitos(contato.telefone), "text": texto},
                timeout=self.timeout,
            )
            if not resposta.ok:
                logger.error("Evolution API %s: %s", resposta.status_code, resposta.text[:500])
            resposta.raise_for_status()
            corpo = resposta.json() if resposta.content else {}
            logger.info("Enviado para %s (aprovado por %s)", contato, quem)
            return ResultadoEnvio(
                entregue=True, externa_id=_id_da_resposta(corpo), detalhe=None
            )
        except requests.RequestException as exc:
            raise TransporteError(
                self.nome, str(exc), retentavel=isinstance(exc, _RETENTAVEIS)
            ) from exc

    # -- recebimento ------------------------------------------------------

    def receber(self, evento: Mapping[str, Any]) -> EventoRecebido | None:
        """Normaliza um webhook `messages.upsert` da Evolution API.

        Só mensagem de texto vira `EventoRecebido`. Mídia é ignorada de
        propósito **na v1**: a foto do pet é o fato mais importante do funil
        (S2), mas quem afirma que ela chegou é a extração sobre a conversa, e
        armazenar binário traz retenção e LGPD (§12) junto. O gancho está em
        `_tipo_de_midia` para quando isso for tratado como capability própria.
        """
        if not isinstance(evento, Mapping):
            return None
        dados = evento.get("data") or evento
        if not isinstance(dados, Mapping):
            return None

        chave = dados.get("key") or {}
        remote_jid = chave.get("remoteJid") if isinstance(chave, Mapping) else None
        if not remote_jid or "@g.us" in str(remote_jid):
            # Grupo não é conversa de venda; ignorar aqui evita poluir o funil.
            return None

        texto = _texto_da_mensagem(dados.get("message") or {})
        if texto is None:
            return None

        from_me = bool(chave.get("fromMe")) if isinstance(chave, Mapping) else False
        return EventoRecebido(
            telefone=_so_digitos(str(remote_jid).split("@", 1)[0]),
            texto=texto,
            enviada_em=_timestamp(dados.get("messageTimestamp")),
            direcao=SAIDA if from_me else ENTRADA,
            nome=dados.get("pushName"),
            externa_id=chave.get("id") if isinstance(chave, Mapping) else None,
            bruto=dict(evento),
        )


def _texto_da_mensagem(mensagem: Mapping[str, Any]) -> str | None:
    """Texto de um `message` da Evolution, nos formatos que ela usa."""
    if not isinstance(mensagem, Mapping):
        return None
    if isinstance(mensagem.get("conversation"), str):
        return mensagem["conversation"]
    estendida = mensagem.get("extendedTextMessage")
    if isinstance(estendida, Mapping) and isinstance(estendida.get("text"), str):
        return estendida["text"]
    # Legenda de mídia é texto do cliente e conta como mensagem: "olha ele
    # aqui" junto da foto é frequentemente a única frase da conversa.
    for chave in ("imageMessage", "videoMessage", "documentMessage"):
        bloco = mensagem.get(chave)
        if isinstance(bloco, Mapping):
            return bloco.get("caption") or ""
    return None


def _tipo_de_midia(mensagem: Mapping[str, Any]) -> str | None:
    """Tipo de mídia anexada, quando houver. Gancho para uma capability futura."""
    if not isinstance(mensagem, Mapping):
        return None
    for chave, tipo in (
        ("imageMessage", "image"),
        ("audioMessage", "audio"),
        ("videoMessage", "video"),
        ("documentMessage", "document"),
        ("stickerMessage", "sticker"),
    ):
        if isinstance(mensagem.get(chave), Mapping):
            return tipo
    return None


def _timestamp(bruto: Any) -> datetime:
    """Epoch em segundos -> datetime UTC; qualquer outra coisa -> agora."""
    try:
        return datetime.fromtimestamp(int(bruto), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _id_da_resposta(corpo: Mapping[str, Any]) -> str | None:
    chave = corpo.get("key") if isinstance(corpo, Mapping) else None
    if isinstance(chave, Mapping):
        return chave.get("id")
    return None


def _so_digitos(telefone: str) -> str:
    return "".join(c for c in telefone if c.isdigit())
