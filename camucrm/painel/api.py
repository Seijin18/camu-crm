"""Rotas de leitura do painel — `APIRouter` fino, sem regra de negócio.

Toda rota é `def`, nunca `async def`: `psycopg`/`psycopg_pool` são síncronos,
e Starlette só roda uma rota `def` fora do event loop, numa thread do
threadpool. Uma rota `async def` chamando uma consulta síncrona bloquearia o
loop inteiro — o bug mais provável neste tipo de serviço, e o motivo do
`CLAUDE.md` pedir atenção a isso explicitamente.

N+1 aceito conscientemente: cada conversa aberta é recalculada (`pipeline.
recalcular(persistir=False)`) uma a uma. `?limite=200` é o teto; acima de 150
conversas abertas o módulo loga aviso — não é otimizado agora porque o número
de conversas abertas de verdade fica muito abaixo disso, e otimizar cedo
trocaria simplicidade por um problema que ainda não existe.

Nenhuma rota tem "enviar" no path, e este módulo não importa
`camucrm.transport` — o painel não manda mensagem nenhuma (ver
`tests/test_painel_api.py::test_nenhum_path_contem_enviar` e
`test_nenhum_modulo_do_painel_importa_transport`, que conferem isso por AST,
não por grep).

**Change `acoes-no-painel`**: as três rotas de escrita abaixo (marcos, funil,
correções) existem para o kanban ter drag-and-drop. Nenhuma delas implementa a
sequência de efeitos aqui — todas chamam `camucrm.acoes`, o mesmo módulo que
`cli.cmd_marcar`/`cli.cmd_tipo` chamam, para os dois caminhos nunca divergirem
(ver `camucrm/acoes.py`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .. import acoes, metrics
from ..db import Database
from ..pipeline import recalcular
from ..rules.fila import Candidato, montar_fila
from ..taxonomia import B2B, B2C, FUNIS
from . import server, views
from .server import exigir_token
from .stream import gerador_sse

router = APIRouter(dependencies=[Depends(exigir_token)])

LIMITE_CONVERSAS_PADRAO = 200
LIMITE_AVISO_N1 = 150


def _db() -> Database:
    """Indireção fina para `server.get_db`, resolvida a cada requisição.

    Não usar `from .server import get_db` direto como default de `Depends`:
    isso capturaria a função no momento da definição da rota, e um teste que
    faz `patch.object(server, "get_db")` (mesmo padrão de
    `test_webhook.TesteToken`) não teria efeito nenhum — o `Depends` já
    estaria segurando a referência antiga. Chamando `server.get_db()` por
    atributo, o patch é visto na hora.
    """
    return server.get_db()


def _carregar_candidatos(db: Database, *, limite: int = LIMITE_CONVERSAS_PADRAO):
    """Conversas abertas, recalculadas, prontas para virar card ou item de fila.

    Um recálculo por conversa (N+1 aceito, ver docstring do módulo). O aviso
    de log acima de `LIMITE_AVISO_N1` é o que o plano pede: sinalizar o custo
    sem bloquear a rota por causa dele.
    """
    conversas = db.listar_conversas_abertas(limite=limite)
    if len(conversas) > LIMITE_AVISO_N1:
        import logging

        logging.getLogger("camucrm.painel").warning(
            "%s conversas abertas recalculadas nesta requisição (N+1 aceito, "
            "ver CLAUDE.md/plano do painel-leitura)",
            len(conversas),
        )
    agora = datetime.now(timezone.utc)
    resultado = []
    for conversa in conversas:
        estado = recalcular(db, conversa, agora=agora, persistir=False)
        resultado.append((conversa, estado))
    return resultado


def _candidato_de(conversa, estado) -> Candidato:
    return Candidato(
        conversa_id=conversa.id,
        nome=conversa.nome_contato or f"#{conversa.id}",
        funil=conversa.funil,
        estagio=estado.estagio,
        classificacao=estado.classificacao,
        sinais=estado.sinais,
    )


@router.get("/kanban")
def get_kanban(funil: str | None = None, db: Database = Depends(_db)):
    """Kanban de um funil, ou dos dois quando `funil` não é passado.

    B2C primeiro por ser o funil principal do documento — decisão registrada
    no plano de execução, não uma preferência arbitrária de ordenação.
    """
    if funil is not None and funil not in FUNIS:
        return views.erro(f"funil inválido: {funil!r}", "§3")

    pares = _carregar_candidatos(db)
    cards = [views.card_conversa(c, e) for c, e in pares]

    funis = [funil] if funil else [B2C, B2B]
    return {"kanbans": [views.montar_kanban(cards, f) for f in funis]}


@router.get("/conversas")
def listar_conversas(
    funil: str | None = None,
    estagio: str | None = None,
    temperatura: str | None = None,
    bola: str | None = None,
    objecao: str | None = None,
    ordenar: str = "horas_esperando",
    direcao: str = "desc",
    limite: int = 50,
    offset: int = 0,
    db: Database = Depends(_db),
):
    pares = _carregar_candidatos(db)
    cards = [views.card_conversa(c, e) for c, e in pares]
    if funil:
        cards = [c for c in cards if c["funil"] == funil]

    ids_com_objecao = None
    if objecao:
        # Só roda a consulta extra por conversa quando o filtro é usado —
        # fora deste caminho o custo comum não muda (decisão do plano).
        ids_com_objecao = {
            c["id"]
            for c in cards
            if any(o.categoria == objecao for o in db.objecoes_da_conversa(c["id"]))
        }

    try:
        filtrados = views.filtrar_conversas(
            cards,
            estagio=estagio,
            temperatura=temperatura,
            bola=bola,
            ids_com_objecao=ids_com_objecao,
        )
        ordenados = views.ordenar_conversas(filtrados, campo=ordenar, direcao=direcao)
    except ValueError as exc:
        return views.erro(str(exc), None)

    pagina = views.paginar(ordenados, limite=limite, offset=offset)
    return {"total": len(ordenados), "conversas": pagina}


@router.get("/conversas/{conversa_id}")
def detalhe_conversa(conversa_id: int, db: Database = Depends(_db)):
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return views.erro(f"conversa {conversa_id} não existe", None)

    estado = recalcular(db, conversa, persistir=False)
    return views.detalhe_conversa(
        conversa,
        estado,
        fatos=db.fatos_detalhados(conversa_id),
        eventos=db.eventos_da_conversa(conversa_id),
        objecoes=db.objecoes_da_conversa(conversa_id),
        followups=db.followups_da_conversa(conversa_id),
        marcos=db.marcos_detalhados(conversa_id),
        correcoes=db.correcoes_da_conversa(conversa_id),
        contato=db.contato_resumido(conversa_id),
    )


@router.get("/conversas/{conversa_id}/mensagens")
def mensagens_da_conversa(
    conversa_id: int,
    desde_id: int | None = None,
    limite: int = 200,
    db: Database = Depends(_db),
):
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return views.erro(f"conversa {conversa_id} não existe", None)
    mensagens = db.listar_mensagens_registradas(
        conversa_id=conversa_id, desde_id=desde_id, limite=limite
    )
    return views.serializar_mensagens(mensagens, desde_id=desde_id)


@router.get("/fila")
def get_fila(limite: int = 10, db: Database = Depends(_db)):
    pares = _carregar_candidatos(db)
    candidatos = [_candidato_de(c, e) for c, e in pares]
    itens = montar_fila(candidatos, limite=limite)
    return {"itens": [views.item_fila_para_json(i) for i in itens]}


@router.get("/stream")
async def stream(desde_id: int | None = None, db: Database = Depends(_db)):
    """SSE do painel (change `painel-tempo-real`) — a única rota `async def`
    deste módulo.

    Único ponto de `camucrm/painel/` onde `psycopg` é chamado de dentro de
    uma coroutine; toda leitura de banco fica dentro de `stream.gerador_sse`,
    que despacha via `asyncio.to_thread` (ver docstring do módulo `stream`
    para o motivo — bloquear o event loop aqui congelaria todos os clientes
    conectados, não só este). O poller que alimenta o gerador é o único por
    processo, criado em `server.poller`.

    `desde_id` é o cursor de reconexão (`?desde_id=N`) — nunca o token de
    autenticação, que continua indo só pelo header `X-Camu-Token` (requirement
    "Token nunca na URL").
    """
    return StreamingResponse(
        gerador_sse(db, server.poller, desde_id=desde_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/metricas")
def get_metricas(dias: int = 30, db: Database = Depends(_db)):
    desde = datetime.now(timezone.utc) - timedelta(days=dias) if dias else None
    return views.metricas_para_json(
        metrics.metricas_chave(db, desde=desde),
        metrics.tempo_por_estagio(db),
        metrics.saude_taxonomia(db, desde=desde),
    )


# --------------------------------------------------------------------------
# Rotas de escrita (change `acoes-no-painel`) — sempre via `camucrm.acoes`
# --------------------------------------------------------------------------
#
# `AcaoInvalidaError` (e sua subclasse `MarcoNaoPermitidoError`) viram HTTP
# 422 com `{"erro", "regra"}` — diferente das rotas de leitura acima, que
# devolvem 200 com corpo de erro para "conversa não existe" (padrão já
# fixado em `test_conversa_inexistente_404_shape`). Aqui a diferença importa:
# 422 é o contrato que o requirement "Coluna derivada recusa drop com 422"
# exige, e a mesma fôrma serve para qualquer outra ação recusada por este
# router — nenhum marco fica meio-gravado.


class MarcoBody(BaseModel):
    marco: str
    por: str | None = None


class FunilBody(BaseModel):
    funil: str
    por: str | None = None


class CorrecaoBody(BaseModel):
    campo: str
    antes: str | None = None
    depois: str | None = None
    por: str | None = None


@router.post("/conversas/{conversa_id}/marcos")
def marcar_marco(conversa_id: int, corpo: MarcoBody, db: Database = Depends(_db)):
    """Drop numa coluna de marco do kanban (S6/SX/P5/P6/PX).

    Delega inteiro a `acoes.marcar_marco` — mesma função que `cli.cmd_marcar`
    chama, para os dois caminhos nunca produzirem estados diferentes.
    """
    try:
        resultado = acoes.marcar_marco(db, conversa_id, corpo.marco, por=corpo.por)
    except acoes.AcaoInvalidaError as exc:
        regra = getattr(exc, "regra", None)
        return JSONResponse(status_code=422, content=views.erro(str(exc), regra))
    conversa = db.get_conversa(conversa_id)
    return {"ok": True, "card": views.card_conversa(conversa, resultado.estado)}


@router.post("/conversas/{conversa_id}/funil")
def mudar_funil(conversa_id: int, corpo: FunilBody, db: Database = Depends(_db)):
    """Arrastar um card entre os dois kanbans do funil.

    Delega a `acoes.mudar_funil_conversa`, que grava a correção (§7) sempre
    que o funil muda de verdade — arrastar de volta ao mesmo funil não conta
    como mudança e não grava nada, mesmo comportamento de `cli.cmd_tipo`.
    """
    try:
        resultado = acoes.mudar_funil_conversa(db, conversa_id, corpo.funil, por=corpo.por)
    except acoes.AcaoInvalidaError as exc:
        regra = getattr(exc, "regra", None)
        return JSONResponse(status_code=422, content=views.erro(str(exc), regra))
    conversa = db.get_conversa(conversa_id)
    estado = recalcular(db, conversa, persistir=False)
    return {"ok": True, "card": views.card_conversa(conversa, estado)}


@router.post("/conversas/{conversa_id}/correcoes")
def registrar_correcao(conversa_id: int, corpo: CorrecaoBody, db: Database = Depends(_db)):
    """Correção humana avulsa, gravada sempre (§7).

    Caminho genérico para qualquer campo corrigido na tela de detalhe — não
    passa pela reclassificação de funil de `acoes.mudar_funil_conversa`
    (essa é a rota `/funil` acima); esta é só o registro em `correcoes`.
    """
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return JSONResponse(
            status_code=422, content=views.erro(f"conversa {conversa_id} não existe", None)
        )
    db.registrar_correcao(
        conversa_id, corpo.campo, corpo.antes, corpo.depois, por=corpo.por
    )
    return {"ok": True}
