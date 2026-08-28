"""Receptor de webhook da Evolution API.

Serviço HTTP mínimo, com uma responsabilidade: aceitar o evento, confirmar
rápido e ingerir. Nenhuma regra de negócio mora aqui — o parsing é do
transporte (§11) e a decisão é de `rules/`; este módulo só liga os dois.

**Confirma antes de processar.** A Evolution API reentrega o que não recebe
`2xx` rápido, e reentrega multiplicada. Processar antes de responder
transformaria uma extração lenta numa avalanche de duplicatas — que o
`externa_id` deduplicaria, mas ao custo de uma fila de trabalho crescendo
sozinha. O trabalho vai para `BackgroundTasks`, depois da resposta.

**Não envia nada.** §10 é categórico, e este serviço não tem rota de envio nem
importa o transporte para escrever. Um webhook que pudesse responder sozinho
seria exatamente o disparo automático que a seção proíbe.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from . import config
from .db import Conversa, Database
from .ingest import ingerir

if TYPE_CHECKING:  # pragma: no cover
    from .extraction.extractor import Extrator
from .transport import criar_transporte

logger = logging.getLogger("camucrm.webhook")

ENV_TOKEN = "CAMU_WEBHOOK_TOKEN"
ENV_PORTA = "CAMU_WEBHOOK_PORT"
ENV_EXTRAIR = "CAMU_EXTRAIR_AO_RECEBER"
# Change `extracao-em-lote-por-janela`: gatilho híbrido — qualquer um dos
# dois limiares dispara extração imediata; abaixo dos dois, a mensagem fica
# pendente para `camucrm extrair` (cron externo, ver design.md do change).
ENV_LIMIAR_MENSAGENS = "CAMU_EXTRACAO_LIMIAR_MENSAGENS"
ENV_TETO_ESPERA_MINUTOS = "CAMU_EXTRACAO_TETO_ESPERA_MINUTOS"
LIMIAR_MENSAGENS_PADRAO = 6
TETO_ESPERA_MINUTOS_PADRAO = 3
PORTA_PADRAO = 8091  # 8090 é do ingress do WhatBot

app = FastAPI(title="camu-crm — ingestão", docs_url=None, redoc_url=None)

_db: Database | None = None
_extrator: "Extrator | None" = None


def extrair_ao_receber() -> bool:
    """Se a extração roda junto da ingestão. Ligado por padrão.

    Extrair depois, em lote, deixa a fila do dia atrasada em relação ao que
    já aconteceu — e a fila é o produto (§0). O custo é uma chamada de LLM por
    bloco de mensagens novas, não por mensagem: `Extrator.processar_conversa`
    só chama o modelo quando há texto ainda não processado (§2, delta).

    Desligue com `CAMU_EXTRAIR_AO_RECEBER=false` quando quiser observar a
    ingestão sem gastar cota, ou quando o provedor estiver fora do ar — a
    ingestão continua funcionando, e `make extrair` recupera o atraso depois.

    Ligado (o padrão) não significa mais "chama o LLM a cada evento" —
    change `extracao-em-lote-por-janela`: `_extrair` ainda consulta
    `_deve_extrair_agora` antes de chamar o modelo. Esta flag continua
    sendo o interruptor geral (extração nenhuma no caminho de ingestão);
    o gatilho híbrido decide o "quando", não o "se".
    """
    return os.getenv(ENV_EXTRAIR, "true").strip().lower() not in {
        "0", "false", "no", "off", "nao", "não",
    }


def limiar_mensagens() -> int:
    """Mensagens pendentes suficientes para disparar extração na hora,
    mesmo sem atingir o teto de espera (change `extracao-em-lote-por-
    janela`) — ver `design.md` do change para o porquê do default."""
    return int(os.getenv(ENV_LIMIAR_MENSAGENS, str(LIMIAR_MENSAGENS_PADRAO)))


def teto_espera_minutos() -> int:
    """Minutos que a mensagem pendente mais antiga pode esperar antes de
    forçar extração na hora, mesmo abaixo do limiar de contagem (change
    `extracao-em-lote-por-janela`)."""
    return int(
        os.getenv(ENV_TETO_ESPERA_MINUTOS, str(TETO_ESPERA_MINUTOS_PADRAO))
    )


def _deve_extrair_agora(
    db: Database, conversa: Conversa, *, agora: datetime | None = None
) -> bool:
    """Gatilho híbrido (change `extracao-em-lote-por-janela`): contagem OU
    espera, o que vier primeiro — nunca os dois juntos, nenhum dos dois
    isolado (ver `design.md` do change para o porquê de cada um sozinho
    falhar).

    Sem mensagem pendente, não há o que decidir — `False`. Esse caso não é
    esperado logo após um ingest bem-sucedido (a mensagem que acabou de
    chegar já é uma pendência), mas mantém a função segura para qualquer
    chamador.
    """
    agora = agora or datetime.now(timezone.utc)
    pendentes = db.mensagens_desde(conversa.id, conversa.ultima_mensagem_processada_id)
    if pendentes <= 0:
        return False
    if pendentes >= limiar_mensagens():
        return True
    mais_antiga = db.primeira_mensagem_pendente_em(
        conversa.id, conversa.ultima_mensagem_processada_id
    )
    if mais_antiga is None:  # defensivo: `pendentes > 0` deveria garantir isto
        return True
    return (agora - mais_antiga) >= timedelta(minutes=teto_espera_minutos())


def get_extrator() -> "Extrator | None":
    """Extrator único por processo, criado na primeira necessidade.

    Devolve `None` quando a extração está desligada ou quando o provedor não
    está configurado — nesse caso a mensagem é ingerida do mesmo jeito e o
    estágio simplesmente não avança além do que os fatos já conhecidos
    sustentam. Falha de LLM nunca pode custar a mensagem.
    """
    global _extrator
    if not extrair_ao_receber():
        return None
    if _extrator is None:
        from .extraction.extractor import Extrator
        from .llm import LlmIndisponivelError, criar_llm

        try:
            _extrator = Extrator(get_db(), criar_llm())
        except (LlmIndisponivelError, RuntimeError) as exc:
            logger.warning(
                "Extração ao receber desligada: LLM indisponível (%s). "
                "A ingestão continua; rode `camucrm extrair` depois.",
                exc,
            )
            return None
    return _extrator


def get_db() -> Database:
    """Pool único por processo, criado na primeira requisição."""
    global _db
    if _db is None:
        _db = Database(config.dsn())
        _db.init_pool()
    return _db


def _autorizado(token_recebido: str | None) -> bool:
    """Confere o token compartilhado, quando configurado.

    Sem `CAMU_WEBHOOK_TOKEN` a rota fica aberta — aceitável apenas em rede
    local. `compare_digest` em vez de `==` porque a comparação ingênua vaza o
    tamanho e o prefixo do token pelo tempo de resposta.
    """
    esperado = os.getenv(ENV_TOKEN, "").strip()
    if not esperado:
        return True
    return bool(token_recebido) and hmac.compare_digest(token_recebido, esperado)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "servico": "camu-crm"}


@app.post("/webhook/evolution")
async def webhook_evolution(
    request: Request,
    tarefas: BackgroundTasks,
    x_camu_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Recebe um evento da Evolution API.

    Responde `{"recebido": true}` de imediato; a ingestão roda depois.
    """
    if not _autorizado(x_camu_token):
        raise HTTPException(status_code=401, detail="token inválido")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - corpo inválido não derruba o serviço
        logger.warning("Webhook com corpo não-JSON, ignorado")
        return {"recebido": True, "ignorado": "corpo não-JSON"}

    tarefas.add_task(_processar, payload)
    return {"recebido": True}


def _processar(payload: dict[str, Any]) -> None:
    """Ingere o evento. Roda depois da resposta HTTP.

    Change `ingestao-a-prova-de-falha`: o payload cru é gravado em
    `eventos_recebidos_bruto` (design.md) ANTES de qualquer parsing/
    `ingerir()`. Uma exceção durante o processamento não perde mais o
    evento — a linha permanece `processado=False` com o erro registrado,
    disponível para `camucrm reprocessar-falhas`. Um evento malformado ainda
    não pode derrubar o worker nem impedir os próximos: a exceção é
    registrada (log + staging), nunca propagada.
    """
    db = get_db()
    try:
        evento_bruto_id = db.registrar_evento_bruto(payload)
    except Exception:  # noqa: BLE001
        # Se nem o staging grava, o banco está fora do ar — não há onde
        # registrar o rastro de reprocessamento. `ensure_schema()` já rodou
        # no boot (`servir()`), então isto é infraestrutura caindo depois de
        # já estar de pé, não schema ausente.
        logger.exception(
            "Falha ao gravar payload bruto em eventos_recebidos_bruto — "
            "evento perdido antes mesmo do staging (banco indisponível?)"
        )
        return

    try:
        # Sem credencial: este processo só sabe receber (ver `criar_transporte`).
        transporte = criar_transporte("evolution", para_envio=False)
        # Change `ingestao-restrita-por-instancia`: `instance` é campo
        # padrão do corpo do webhook da Evolution API (nível raiz, fora de
        # `data`) — identifica de qual número o evento veio (Camu, número
        # pessoal, número do Felipe). Ausente ou com nome inesperado, cai
        # em `None` e `ingerir` não aplica restrição nenhuma (design.md do
        # change, Decisão 3: falha segura do lado de restringir de menos,
        # nunca de mais).
        instancia = payload.get("instance") if isinstance(payload, dict) else None
        resultado = ingerir(
            db, transporte.receber(payload), origem="whatsapp", instancia=instancia
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Falha processando webhook — payload preservado em "
            "eventos_recebidos_bruto (id=%s) para reprocessamento manual "
            "(`camucrm reprocessar-falhas`)",
            evento_bruto_id,
        )
        db.marcar_evento_bruto_falhou(evento_bruto_id, str(exc))
        return

    if resultado.ignorada_por_restricao_instancia:
        # Change `ingestao-restrita-por-instancia`, revisão de 2026-08-27
        # (pedido explícito do usuário): telefone sem relação nenhuma com
        # a Camu não deixa rastro nem no staging técnico — o payload já
        # cumpriu seu único propósito (permitir reprocessamento se algo
        # tivesse falhado no meio) e a decisão foi tomada com sucesso.
        db.excluir_evento_bruto(evento_bruto_id)
        return

    db.marcar_evento_bruto_processado(evento_bruto_id)
    if resultado.ignorada:
        return
    logger.info("Webhook: %s", resultado)

    if resultado.duplicada or resultado.conversa_id is None:
        return
    _extrair(resultado.conversa_id)


def _extrair(conversa_id: int) -> None:
    """Extrai os fatos do bloco recém-ingerido — ou adia, se o gatilho
    híbrido decidir que ainda não vale a pena (change `extracao-em-lote-
    por-janela`, ver `_deve_extrair_agora`).

    Separado de `_processar` e com `try` próprio de propósito: a mensagem já
    está gravada neste ponto, e uma falha de extração não pode desfazer isso
    nem fazer parecer que o evento se perdeu. O bloco continua marcado como
    não processado, então a próxima rodada tenta de novo.
    """
    extrator = get_extrator()
    if extrator is None:
        return
    conversa = extrator.db.get_conversa(conversa_id)
    if conversa is None:
        return
    if not _deve_extrair_agora(extrator.db, conversa):
        logger.debug(
            "Conversa %s: extração adiada (abaixo dos limiares do gatilho "
            "híbrido) — `camucrm extrair` processa depois",
            conversa_id,
        )
        return
    try:
        resultado = extrator.processar_conversa(conversa_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Extração falhou na conversa %s (mensagem preservada)", conversa_id
        )
        return
    if resultado.erro:
        logger.warning(
            "Extração sem resultado na conversa %s: %s", conversa_id, resultado.erro
        )
        return
    if resultado.estado and resultado.estado.transicao:
        t = resultado.estado.transicao
        logger.info(
            "Conversa %s avançou: %s -> %s (%s)", conversa_id, t.de, t.para, t.motivo
        )
    for democao in resultado.democoes:
        logger.info("Conversa %s, rebaixado: %s", conversa_id, democao)


def servir(porta: int | None = None) -> None:
    """Sobe o serviço. Chamado por `camucrm servir`.

    Change `ingestao-a-prova-de-falha`: `ensure_schema()` roda aqui, no boot
    do processo, ANTES de aceitar qualquer requisição. É idempotente — seguro
    rodar de novo mesmo que o schema já exista — e falha de conectar ou de
    aplicar o schema derruba o processo com um erro alto, aqui, e não em
    silêncio no primeiro webhook recebido contra um banco novo/recriado ou
    uma migração ainda não aplicada (spec.md, "Schema ausente falha no boot,
    não no primeiro evento").
    """
    import uvicorn

    get_db().ensure_schema()

    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - precisa aceitar do container da Evolution
        port=porta or int(os.getenv(ENV_PORTA, PORTA_PADRAO)),
        log_level="info",
    )
