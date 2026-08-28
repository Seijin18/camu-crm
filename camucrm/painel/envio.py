"""Envio direto pela Evolution API, a partir do painel — só para prospecção.

## Por que este módulo existe, e por que é o único de `camucrm/painel/` que
importa `camucrm.transport`

`prospeccao-b2b-shortlist/design.md` (decisão 2) escolheu deliberadamente
**não** enviar pela Evolution API a partir do painel — só o link
`api.whatsapp.com/send`, com o clique do operador abrindo o WhatsApp e o
envio de fato acontecendo lá dentro. A razão registrada: "zero mudança na
superfície de segurança do painel, zero credencial nova". Todos os outros
módulos de `camucrm/painel/` continuam obedecendo exatamente essa regra —
`tests/test_painel_api.py::TesteSemRotaDeEnvio` prova isso por AST.

Pedido explícito do usuário (change `envio-prospeccao-pela-evolution-api`)
reabre essa decisão só para este módulo: quer um botão que envie de fato,
sem sair do painel. A garantia que importa — §1/§10 do documento principal,
"envio é sempre humano, nunca automático" — continua de pé. O que muda é
ONDE o clique de aprovação acontece (no painel, em vez do WhatsApp Web/app),
não SE um humano aprova: `enviar_prospeccao` recusa (levanta
`CampoObrigatorioError`) qualquer chamada sem `por` preenchido, ANTES de
tocar rede — o mesmo `aprovado_por` obrigatório que `transport/base.py` já
exige de `camucrm enviar`.

Consequência aceita e documentada: o processo do painel passa a carregar
`EVOLUTION_API_KEY` quando este caminho é usado (antes, "ausente de
propósito"). Isolado aqui: nenhum outro arquivo do painel precisa saber que
`camucrm.transport` existe — `camucrm/painel/api.py` chama
`enviar_prospeccao`, não `transport.criar_transporte` diretamente.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..db import Database
from ..transport import Destinatario, TransporteError, criar_transporte

logger = logging.getLogger("camucrm.painel.envio")


class CampoObrigatorioError(ValueError):
    """`telefone`/`mensagem`/`por` vazio — recusado antes de tocar rede."""

    def __init__(self, campo: str):
        super().__init__(f"{campo} é obrigatório para enviar")
        self.campo = campo


@dataclass(frozen=True)
class ResultadoEnvioProspeccao:
    ok: bool
    externa_id: str | None = None
    erro: str | None = None


def enviar_prospeccao(
    db: Database,
    prospeccao_id: int,
    *,
    telefone: str,
    mensagem: str,
    por: str,
) -> ResultadoEnvioProspeccao:
    """Envia `mensagem` para `telefone` pela Evolution API e registra o
    resultado na linha de `prospeccoes`.

    `telefone`/`mensagem` são os que o OPERADOR reviu no popup — não são
    relidos de `prospeccoes.telefone` nem recalculados do template aqui.
    Ler direto do banco ignoraria qualquer edição feita na tela, e o próprio
    ponto do popup é permitir essa edição antes de enviar.

    `TransporteError` propaga (não é engolida): quem chama
    (`camucrm/painel/api.py`) precisa devolver 502 com o detalhe — o
    operador precisa saber que falhou e por quê, para decidir se tenta de
    novo ou usa o link `wa.me` como alternativa. O resultado da falha é
    gravado antes de propagar, então o histórico não se perde mesmo que a
    rota acima devolva erro.
    """
    quem = (por or "").strip()
    if not quem:
        raise CampoObrigatorioError("por")
    numero = (telefone or "").strip()
    if not numero:
        raise CampoObrigatorioError("telefone")
    texto = (mensagem or "").strip()
    if not texto:
        raise CampoObrigatorioError("mensagem")

    try:
        # `criar_transporte` levanta `RuntimeError` simples (não
        # `TransporteError` — que HERDA de `RuntimeError`, então este except
        # precisa ficar num bloco `try` separado do envio: um `except
        # RuntimeError` que viesse antes de `except TransporteError` no MESMO
        # bloco capturaria os dois, por `isinstance`, e a falha de envio
        # perderia o tipo específico) quando faltam
        # `EVOLUTION_API_BASE_URL`/`_API_KEY`/`_INSTANCE` no processo do
        # painel — caso real e esperado até o operador configurar o `.env`.
        transporte = criar_transporte("evolution")
    except RuntimeError as exc:
        erro = TransporteError("evolution", str(exc))
        db.registrar_envio_prospeccao(
            prospeccao_id, por=quem, sucesso=False, erro=str(erro)
        )
        raise erro from exc

    try:
        resultado = transporte.enviar(
            Destinatario(numero), texto, aprovado_por=quem
        )
    except TransporteError as exc:
        logger.warning(
            "Envio de prospecção %s falhou (aprovado por %s): %s",
            prospeccao_id, quem, exc,
        )
        db.registrar_envio_prospeccao(
            prospeccao_id, por=quem, sucesso=False, erro=str(exc)
        )
        raise

    logger.info(
        "Prospecção %s enviada pela Evolution API (aprovado por %s)",
        prospeccao_id, quem,
    )
    db.registrar_envio_prospeccao(prospeccao_id, por=quem, sucesso=True)
    return ResultadoEnvioProspeccao(ok=True, externa_id=resultado.externa_id)
