"""Servidor do painel web de leitura (§13, antecipado — change `painel-leitura`).

Mesma forma de `webhook.py`: `FastAPI` mínimo, token compartilhado no mesmo
padrão de `webhook._autorizado`, e nenhum caminho de escrita. A diferença é o
bind: o webhook precisa aceitar conexão da Evolution API (`0.0.0.0`); o painel
só é usado localmente por quem está operando, então o padrão é `127.0.0.1` —
expor a rede exige `CAMU_PAINEL_HOST` explícito.

**Este painel não envia nada.** Nenhuma rota daqui escreve no banco nem chama
`camucrm.transport` — só leitura, por enquanto (SSE é change 2, escrita é
changes 3/4/5).
"""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from ..db import Database
from .stream import PollerMudanca

logger = logging.getLogger("camucrm.painel")

ENV_TOKEN = "CAMU_PAINEL_TOKEN"
ENV_PORTA = "CAMU_PAINEL_PORT"
ENV_HOST = "CAMU_PAINEL_HOST"
PORTA_PADRAO = 8093
HOST_PADRAO = "127.0.0.1"

# DIVERGÊNCIA do plano original (registrada, não silenciosa): o painel abre
# mais conexões concorrentes por operador (fila + kanban + detalhe, cada tela
# podendo estar aberta ao mesmo tempo) do que o pool de 5 usado por
# `cli._db`/`webhook.get_db`. `Database.__init__` ganhou o parâmetro opcional
# `max_size` (ver `db.py`) só para isso — retrocompatível, os outros
# chamadores continuam com o padrão de 5.
_MAX_SIZE_PAINEL = 20

_ESTATICOS = Path(__file__).resolve().parent / "static"

app = FastAPI(title="camu-crm — painel", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(_ESTATICOS)), name="static")

_db: Database | None = None


def get_db() -> Database:
    """Pool único por processo, criado na primeira requisição.

    Mesmo padrão de `webhook.get_db` — inclusive para o teste poder
    substituir com `patch.object(server, "get_db")`, como `test_webhook.py`
    já faz com o webhook.
    """
    global _db
    if _db is None:
        _db = Database(config.dsn(), max_size=_MAX_SIZE_PAINEL)
        _db.init_pool()
    return _db


# Poller único por processo (change `painel-tempo-real`, `stream.py`): todos
# os geradores SSE de `/api/stream` aguardam o mesmo `PollerMudanca`, nunca
# criam um consultando o banco por conta própria.
poller = PollerMudanca(lambda: get_db().token_de_mudanca())


@app.on_event("startup")
async def _iniciar_poller() -> None:
    poller.iniciar()


@app.on_event("shutdown")
async def _parar_poller() -> None:
    await poller.parar()


def _autorizado(token_recebido: str | None) -> bool:
    """Mesmo padrão de `webhook._autorizado`: token vazio = rota aberta.

    Aceitável só porque o bind padrão é `127.0.0.1` — expor numa rede maior
    sem configurar `CAMU_PAINEL_TOKEN` é erro de operação, não deste código.
    """
    esperado = os.getenv(ENV_TOKEN, "").strip()
    if not esperado:
        return True
    return bool(token_recebido) and hmac.compare_digest(token_recebido, esperado)


def exigir_token(x_camu_token: str | None = Header(default=None)) -> None:
    """Dependency usada em cada rota do painel — não em middleware.

    A escolha de `Depends` por rota (em vez de middleware transparente) é de
    propósito: é o que permite `tests/test_painel_api.py` testar 401 por rota
    do mesmo jeito que `test_webhook.TesteToken` já testa o webhook, sem um
    caminho de autorização diferente para testar.
    """
    if not _autorizado(x_camu_token):
        raise HTTPException(status_code=401, detail="token inválido")


@app.middleware("http")
async def _csp(request: Request, call_next):
    """CSP restritiva: o front-end é só HTML/CSS/JS locais, sem CDN nenhum."""
    resposta = await call_next(request)
    resposta.headers["Content-Security-Policy"] = "default-src 'self'"
    return resposta


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "servico": "camu-crm-painel"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(_ESTATICOS / "index.html"))


from . import api  # noqa: E402 - depois de `app` existir, para o router usar `Depends(exigir_token)`

app.include_router(api.router, prefix="/api")


def servir(porta: int | None = None) -> None:
    """Sobe o painel. Chamado por `camucrm painel`."""
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv(ENV_HOST, HOST_PADRAO),
        port=porta or int(os.getenv(ENV_PORTA, PORTA_PADRAO)),
        log_level="info",
    )
