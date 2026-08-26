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
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from . import config
from .db import Database
from .ingest import ingerir
from .transport import criar_transporte

logger = logging.getLogger("camucrm.webhook")

ENV_TOKEN = "CAMU_WEBHOOK_TOKEN"
ENV_PORTA = "CAMU_WEBHOOK_PORT"
PORTA_PADRAO = 8091  # 8090 é do ingress do WhatBot

app = FastAPI(title="camu-crm — ingestão", docs_url=None, redoc_url=None)

_db: Database | None = None


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

    Toda exceção é registrada e engolida: um evento malformado não pode
    derrubar o worker nem impedir os próximos. O evento perdido reaparece na
    próxima mensagem da mesma conversa, e o `externa_id` garante que nada
    duplica quando isso acontece.
    """
    try:
        # Sem credencial: este processo só sabe receber (ver `criar_transporte`).
        transporte = criar_transporte("evolution", para_envio=False)
        resultado = ingerir(get_db(), transporte.receber(payload), origem="whatsapp")
        if not resultado.ignorada:
            logger.info("Webhook: %s", resultado)
    except Exception:  # noqa: BLE001
        logger.exception("Falha processando webhook (evento descartado)")


def servir(porta: int | None = None) -> None:
    """Sobe o serviço. Chamado por `camucrm servir`."""
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104 - precisa aceitar do container da Evolution
        port=porta or int(os.getenv(ENV_PORTA, PORTA_PADRAO)),
        log_level="info",
    )
