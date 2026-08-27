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

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        instancia: str = "",
        *,
        timeout: int = 10,
    ):
        """Credenciais são opcionais — só o envio precisa delas.

        A metade de recepção do contrato (`receber`) é parsing puro: não abre
        conexão, não autentica, não sabe qual instância está pareada. Exigir
        `api_key` para receber obrigaria o processo do webhook a carregar uma
        credencial de envio que ele nunca usa — e um receptor sem credencial
        **não consegue** enviar, nem por bug nem se for comprometido, que é a
        garantia que §10 pede em vez de disciplina.

        A validação mora em `enviar`, onde a credencial é de fato usada.
        """
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.instancia = instancia
        self.timeout = timeout

    # -- envio ------------------------------------------------------------

    def enviar(
        self, contato: Destinatario, texto: str, *, aprovado_por: str
    ) -> ResultadoEnvio:
        quem = validar_aprovacao(self.nome, aprovado_por)
        self._exigir_credenciais()
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

    def _exigir_credenciais(self) -> None:
        faltando = [
            nome
            for nome, valor in (
                ("EVOLUTION_API_BASE_URL", self.base_url),
                ("EVOLUTION_API_KEY", self.api_key),
                ("EVOLUTION_INSTANCE", self.instancia),
            )
            if not valor
        ]
        if faltando:
            raise TransporteError(
                self.nome,
                f"envio exige {', '.join(faltando)} — este processo foi criado "
                "apenas para recepção",
            )

    # -- recebimento ------------------------------------------------------

    def receber(self, evento: Mapping[str, Any]) -> EventoRecebido | None:
        """Normaliza um webhook `messages.upsert` da Evolution API.

        Mensagem de texto e mídia (com ou sem legenda) viram `EventoRecebido`
        — nunca guardamos o binário em si (retenção e LGPD, §12), só o fato
        de que algo chegou: mídia sem legenda grava um marcador textual fixo
        (`_texto_da_mensagem`/`_MARCADORES`), nunca `None`. A foto do pet
        continua sendo confirmada pela extração sobre a conversa (S2), não
        por este marcador. Envelope (`ephemeralMessage`/`viewOnceMessage*`/
        `deviceSentMessage`) é desembrulhado recursivamente pela mesma
        função. Ruído de protocolo (`reactionMessage`, recibo, presença, tipo
        não reconhecido) continua devolvendo `None` — evento descartado.
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
            # `pushName` no eco da nossa própria mensagem é o perfil da CAMU,
            # não o do cliente. Aproveitá-lo renomeia o contato para "Camu" na
            # primeira resposta que a gente manda — e a fila do dia passa a
            # listar o nosso próprio nome no lugar do de quem está esperando.
            nome=dados.get("pushName") if not from_me else None,
            externa_id=chave.get("id") if isinstance(chave, Mapping) else None,
            bruto=dict(evento),
        )


#: Marcador textual fixo para mídia sem legenda — decidido por
#: `_tipo_de_midia`. Nunca é evidência literal de fato nenhum (§2): a
#: conferência de literalidade em `extraction/contract.py::_fold` recusa
#: qualquer `true` cuja evidência seja só este texto, como qualquer trecho
#: que não corresponda ao que o cliente disse de verdade.
_MARCADORES = {
    "audio": "[áudio recebido]",
    "sticker": "[figurinha recebida]",
    "contact": "[contato recebido]",
    "location": "[localização recebida]",
    "live_location": "[localização recebida]",
}

#: Chaves de envelope: o conteúdo real está em `.message`, dentro do bloco.
_ENVELOPES = (
    "ephemeralMessage",
    "viewOnceMessage",
    "viewOnceMessageV2",
    "deviceSentMessage",
)


def _texto_da_mensagem(mensagem: Mapping[str, Any]) -> str | None:
    """Texto de um `message` da Evolution, nos formatos que ela usa.

    Três estágios de reconhecimento, nesta ordem:

    1. Texto puro (`conversation`/`extendedTextMessage`) ou legenda de mídia
       com legenda (`imageMessage`/`videoMessage`/`documentMessage`) — a
       legenda, mesmo vazia, é devolvida.
    2. Mídia sem legenda (`audioMessage`, `stickerMessage`, `contactMessage`,
       `locationMessage`, `liveLocationMessage`): `_tipo_de_midia` decide o
       tipo, e `_MARCADORES` devolve um marcador fixo em vez de `None` — o
       evento continua sendo gravado, não descartado.
    3. Envelope (`ephemeralMessage`, `viewOnceMessage`, `viewOnceMessageV2`,
       `deviceSentMessage`): o conteúdo real está em `.message`, dentro do
       envelope. Extrai esse `.message` interno e chama esta MESMA função
       recursivamente sobre ele — texto puro é preservado como texto normal,
       mídia sem legenda cai no marcador do item 2, e conteúdo não
       reconhecido continua devolvendo `None`, exatamente como devolveria
       sem o envelope.

    `reactionMessage` e qualquer chave não reconhecida (inclusive dentro de
    um envelope) devolvem `None` — comportamento inalterado.
    """
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
    # Mídia sem legenda: marcador fixo em vez de descartar o evento inteiro.
    marcador = _MARCADORES.get(_tipo_de_midia(mensagem) or "")
    if marcador is not None:
        return marcador
    # Envelope (efêmero/view-once/eco de outro dispositivo): desembrulhar
    # `.message` e reaplicar esta mesma função, recursivamente.
    for chave in _ENVELOPES:
        envelope = mensagem.get(chave)
        if isinstance(envelope, Mapping):
            return _texto_da_mensagem(envelope.get("message") or {})
    return None


def _tipo_de_midia(mensagem: Mapping[str, Any]) -> str | None:
    """Tipo de mídia anexada, quando houver.

    Decide o marcador textual que `_texto_da_mensagem` devolve para mídia
    sem legenda (via `_MARCADORES`) — deixou de ser gancho morto.
    """
    if not isinstance(mensagem, Mapping):
        return None
    for chave, tipo in (
        ("imageMessage", "image"),
        ("audioMessage", "audio"),
        ("videoMessage", "video"),
        ("documentMessage", "document"),
        ("stickerMessage", "sticker"),
        ("contactMessage", "contact"),
        ("locationMessage", "location"),
        ("liveLocationMessage", "live_location"),
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
