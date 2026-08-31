"""Rotas de leitura do painel — `APIRouter` fino, sem regra de negócio.

Toda rota é `def`, nunca `async def`: `psycopg`/`psycopg_pool` são síncronos,
e Starlette só roda uma rota `def` fora do event loop, numa thread do
threadpool. Uma rota `async def` chamando uma consulta síncrona bloquearia o
loop inteiro — o bug mais provável neste tipo de serviço, e o motivo do
`CLAUDE.md` pedir atenção a isso explicitamente.

N+1 de REDE otimizado (2026-08-28): `_carregar_candidatos`/`_carregar_fechadas`
usam `pipeline.recalcular_lote`, que pré-carrega em seis consultas o que antes
era uma ida ao banco por conversa por campo (`Database.
contexto_para_recalculo`). Motivador: contra Postgres local o padrão antigo
era de graça (latência ~0); contra um banco remoto (Supabase), a fila com
poucas conversas abertas levava 40-50s. N+1 de PROCESSAMENTO em Python
continua existindo (cada conversa ainda passa pela mesma lógica de decisão,
uma de cada vez) — é barato e cresce sub-linearmente com o volume real de
conversas abertas, então não é o gargalo. `?limite=200` é o teto; acima de
150 conversas abertas o módulo loga aviso.

Este módulo (`api.py`) não importa `camucrm.transport` diretamente — a única
rota com "enviar" no path (`POST /prospeccao/{id}/enviar`, change
`envio-prospeccao-pela-evolution-api`) delega para `camucrm.painel.envio`,
o único módulo do pacote que importa transporte (ver docstring de
`envio.py`). `tests/test_painel_api.py` confere por AST que nenhum OUTRO
módulo do painel importa `camucrm.transport`.

**Change `acoes-no-painel`**: as três rotas de escrita abaixo (marcos, funil,
correções) existem para o kanban ter drag-and-drop. Nenhuma delas implementa a
sequência de efeitos aqui — todas chamam `camucrm.acoes`, o mesmo módulo que
`cli.cmd_marcar`/`cli.cmd_tipo` chamam, para os dois caminhos nunca divergirem
(ver `camucrm/acoes.py`).

**Change `resumo-conversa`**: este módulo é um dos dois importadores
autorizados de `camucrm.summaries` (o outro é `camucrm.cli`) — conjunto
fechado, provado por `tests/test_summaries.py`. `POST
/conversas/{id}/resumo` é a única rota que chama LLM para gerar resumo;
`GET` é leitura pura do cache (nunca gera, requirement "Geração só ao
clicar, nunca automática").
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .. import acoes, config, metrics, summaries
from ..backfill import importar_conversas
from ..db import Database
from ..drafts import PROMPT_VERSAO, RascunhoInvalidoError
from ..drafts import gerar as gerar_rascunho
from ..evaluation.dataset import DatasetInvalidoError, TAMANHO_MINIMO, avisos_de_tamanho, carregar, validar_entrada
from ..evaluation.runner import rodar as rodar_eval
from ..extraction.extractor import Extrator
from ..llm import LlmIndisponivelError, criar_llm
from ..pipeline import recalcular, recalcular_lote
from ..rules.fila import Candidato, montar_fila
from ..taxonomia import B2B, B2C, FUNIS
from ..whatsapp_export import (
    ExportacaoDeGrupoError,
    NomeOperadorNaoEncontradoError,
)
from ..whatsapp_export import parse as parse_whatsapp_export
from . import envio, server, views
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


def _carregar_candidatos(
    db: Database, *, limite: int = LIMITE_CONVERSAS_PADRAO, apenas_teste: bool = False
):
    """Conversas abertas, recalculadas, prontas para virar card ou item de fila.

    Recálculo em LOTE (change de 2026-08-28: otimização de consultas) — não
    mais uma ida ao banco por conversa por campo. `recalcular_lote` faz seis
    consultas totais, não seis por conversa; o aviso de log acima de
    `LIMITE_AVISO_N1` continua existindo porque o CUSTO DE PROCESSAMENTO em
    Python ainda cresce com o número de conversas, só o custo de REDE deixou
    de crescer.

    `apenas_teste` (change `contatos-de-teste-isolados`) é o toggle "Modo
    teste" do painel — sempre binário aqui (nunca `incluir_teste`, que é só
    da CLI): ligado mostra só contato de teste, desligado (padrão) só os
    reais, nunca os dois juntos na mesma tela.
    """
    conversas = db.listar_conversas_abertas(limite=limite, apenas_teste=apenas_teste)
    if len(conversas) > LIMITE_AVISO_N1:
        import logging

        logging.getLogger("camucrm.painel").warning(
            "%s conversas abertas recalculadas nesta requisição (custo de "
            "processamento cresce com N mesmo em lote — ver CLAUDE.md/plano "
            "do painel-leitura)",
            len(conversas),
        )
    agora = datetime.now(timezone.utc)
    estados = recalcular_lote(db, conversas, agora=agora, persistir=False)
    return list(zip(conversas, estados))


def _carregar_fechadas(
    db: Database, *, limite: int = LIMITE_CONVERSAS_PADRAO, apenas_teste: bool = False
):
    """Conversas fechadas por marco manual (`resultado` preenchido), recalculadas.

    Change `marco-manual-visivel-na-aba-conversas`: existe só para
    `listar_conversas` abaixo — nunca para `/kanban` nem para a fila, que
    continuam batendo exclusivamente em `_carregar_candidatos` (requirement
    "Kanban e fila continuam mostrando só conversas abertas"). Recálculo em
    lote (mesma otimização de `_carregar_candidatos`), sem persistir: barato,
    e não tem side-effect mesmo numa conversa já fechada.
    """
    conversas = db.listar_conversas_fechadas(limite=limite, apenas_teste=apenas_teste)
    agora = datetime.now(timezone.utc)
    estados = recalcular_lote(db, conversas, agora=agora, persistir=False)
    return list(zip(conversas, estados))


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
def get_kanban(funil: str | None = None, apenas_teste: bool = False, db: Database = Depends(_db)):
    """Kanban de um funil, ou dos dois quando `funil` não é passado.

    B2C primeiro por ser o funil principal do documento — decisão registrada
    no plano de execução, não uma preferência arbitrária de ordenação.

    `apenas_teste` (change `contatos-de-teste-isolados`): o toggle "Modo
    teste" do painel, propagado até `db.listar_conversas_abertas`.

    `total` (change `painel-mensagens-recentes-e-acoes-seguras`, requirement
    "Kanban e fila expõem contagem real"): a contagem REAL de conversas
    abertas, mesmo quando `_carregar_candidatos` corta pelo
    `LIMITE_CONVERSAS_PADRAO` — sem isto o operador não tinha como saber que
    existem mais conversas abertas do que as exibidas nas colunas.
    """
    if funil is not None and funil not in FUNIS:
        return views.erro(f"funil inválido: {funil!r}", "§3")

    pares = _carregar_candidatos(db, apenas_teste=apenas_teste)
    cards = [views.card_conversa(c, e) for c, e in pares]
    total = db.contar_conversas_abertas(apenas_teste=apenas_teste)

    funis = [funil] if funil else [B2C, B2B]
    return {"kanbans": [views.montar_kanban(cards, f) for f in funis], "total": total}


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
    apenas_teste: bool = False,
    db: Database = Depends(_db),
):
    """Change `contatos-de-teste-isolados`: `apenas_teste` é o toggle "Modo
    teste" do painel, propagado até `db.listar_conversas_abertas`.

    Change `marco-manual-visivel-na-aba-conversas`: diferente de `/kanban`,
    esta rota também inclui as conversas fechadas por marco manual
    (`_carregar_fechadas`) — cada card carrega `resultado` para a UI marcar
    a diferença, mas a conversa não desaparece só porque foi encerrada.
    """
    pares = _carregar_candidatos(db, apenas_teste=apenas_teste) + _carregar_fechadas(
        db, apenas_teste=apenas_teste
    )
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
    antes_de: int | None = None,
    limite: int = 200,
    db: Database = Depends(_db),
):
    """Change `painel-mensagens-recentes-e-acoes-seguras`: sem `desde_id`, o
    padrão agora é trazer as `limite` mensagens MAIS RECENTES da conversa
    (não mais as mais antigas) — requirement "Mensagens recentes aparecem
    por padrão". `antes_de` é o cursor de paginação para trás ("antes desta
    mensagem"), para a tela poder carregar mais histórico sob demanda.
    `desde_id`, quando informado, continua servindo o catch-up incremental
    do SSE (`painel/stream.py`) — comportamento inalterado, sempre em ordem
    crescente a partir de um id conhecido.
    """
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return views.erro(f"conversa {conversa_id} não existe", None)
    mensagens = db.listar_mensagens_registradas(
        conversa_id=conversa_id, desde_id=desde_id, antes_de=antes_de, limite=limite
    )
    total = db.contar_mensagens(conversa_id)
    return views.serializar_mensagens(mensagens, desde_id=desde_id, total=total)


@router.get("/fila")
def get_fila(limite: int = 10, apenas_teste: bool = False, db: Database = Depends(_db)):
    """Change `contatos-de-teste-isolados`: `apenas_teste` é o toggle "Modo
    teste" do painel.

    `total` (change `painel-mensagens-recentes-e-acoes-seguras`, requirement
    "Kanban e fila expõem contagem real"): a contagem REAL de conversas
    abertas — a fila em si já é limitada por `limite` (prioridade dela, não
    corte por volume), mas `_carregar_candidatos` corta antes disso pelo
    `LIMITE_CONVERSAS_PADRAO`, e é esse corte que o operador precisa saber
    que existe.
    """
    pares = _carregar_candidatos(db, apenas_teste=apenas_teste)
    candidatos = [_candidato_de(c, e) for c, e in pares]
    itens = montar_fila(candidatos, limite=limite)
    total = db.contar_conversas_abertas(apenas_teste=apenas_teste)
    return {"itens": [views.item_fila_para_json(i) for i in itens], "total": total}


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
def get_metricas(dias: int = 30, apenas_teste: bool = False, db: Database = Depends(_db)):
    """Change `contatos-de-teste-isolados`: `apenas_teste` é o toggle "Modo
    teste" do painel, propagado a toda métrica desta rota."""
    desde = datetime.now(timezone.utc) - timedelta(days=dias) if dias else None
    return views.metricas_para_json(
        metrics.metricas_chave(db, desde=desde, apenas_teste=apenas_teste),
        metrics.tempo_por_estagio(db, apenas_teste=apenas_teste),
        metrics.saude_taxonomia(db, desde=desde, apenas_teste=apenas_teste),
    )


@router.get("/o-que-funciona")
def get_o_que_funciona(dias: int = 90, apenas_teste: bool = False, db: Database = Depends(_db)):
    """Tela `/funciona` (change `analise-desempenho`) — agrega tudo que é
    respondível hoje sem depender de `ground-truth-marcos` (restrição
    herdada de `openspec/project.md`: esta rota nunca devolve acurácia de
    extração).

    DIVERGÊNCIA registrada: `proposal.md` do change nomeia a rota
    `/api/analise`; a execução seguiu `/api/o-que-funciona` (nome pedido na
    instrução de execução, e o que casa com a rota de tela `/funciona`).
    Nenhum requirement do `spec.md` amarra o nome da rota — o contrato
    normativo é o payload, não o path.

    `desde` filtra conversão, objeção e correção pelo período; "onde as
    conversas morrem" e o A/B de rascunho olham o histórico inteiro de
    propósito — cortar por `dias` uma pergunta sobre conversas já encerradas
    ou rascunhos já vinculados descartaria amostra sem necessidade.

    `apenas_teste` (change `contatos-de-teste-isolados`): o toggle "Modo
    teste" do painel, propagado a CADA uma das métricas abaixo — nenhum
    bloco desta tela escapa do filtro (requirement "Leitura agregada exclui
    teste por padrão").
    """
    desde = datetime.now(timezone.utc) - timedelta(days=dias) if dias else None
    return views.o_que_funciona_para_json(
        metricas_chave=metrics.metricas_chave(db, desde=desde, apenas_teste=apenas_teste),
        conversao_b2c=metrics.conversao_adjacente(db, B2C, desde=desde, apenas_teste=apenas_teste),
        conversao_b2b=metrics.conversao_adjacente(db, B2B, desde=desde, apenas_teste=apenas_teste),
        onde_morrem=metrics.onde_morrem(db, apenas_teste=apenas_teste),
        tempo_por_estagio=metrics.tempo_por_estagio(db, apenas_teste=apenas_teste),
        objecao_por_estagio=metrics.objecao_por_estagio(db, desde=desde, apenas_teste=apenas_teste),
        saude_taxonomia=metrics.saude_taxonomia(db, desde=desde, apenas_teste=apenas_teste),
        padrao_correcoes=metrics.padrao_correcoes(db, desde=desde, apenas_teste=apenas_teste),
        retorno_followup=metrics.retorno_por_followup(db, apenas_teste=apenas_teste),
        ab_rascunhos=metrics.ab_rascunhos(db, apenas_teste=apenas_teste),
        resultado_eval=_ler_cache_resultado_eval(),
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


class ContatoTesteBody(BaseModel):
    e_teste: bool
    por: str | None = None


class DesconsiderarRecusaBody(BaseModel):
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


@router.post("/conversas/{conversa_id}/teste")
def marcar_contato_teste(
    conversa_id: int, corpo: ContatoTesteBody, db: Database = Depends(_db)
):
    """Botão "marcar/desmarcar contato de teste" no detalhe da conversa
    (change `contatos-de-teste-isolados`). A marca é por CONTATO — resolvida
    aqui a partir da conversa que a tela já tem aberta — não por conversa
    (requirement "Marca de teste é por contato"). Delega a
    `db.marcar_contato_teste`, que grava a correção (§7) sempre.
    """
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return JSONResponse(
            status_code=422, content=views.erro(f"conversa {conversa_id} não existe", None)
        )
    try:
        db.marcar_contato_teste(conversa.contato_id, corpo.e_teste, por=corpo.por)
    except ValueError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), None))
    return {"ok": True, "contato_id": conversa.contato_id, "e_teste": corpo.e_teste}


@router.post("/conversas/{conversa_id}/desconsiderar-recusa")
def desconsiderar_recusa(
    conversa_id: int, corpo: DesconsiderarRecusaBody, db: Database = Depends(_db)
):
    """Botão "desconsiderar recusa explícita (falso positivo)" no detalhe da
    conversa (design.md, change `estagio-reabertura-manual-e-relogio`).

    Delega inteiro a `acoes.desconsiderar_recusa` — mesma função que
    `cli.cmd_desconsiderar_recusa` chama, para os dois caminhos nunca
    produzirem estados diferentes. O fato `recusa_explicita` em `fatos`
    nunca é tocado; só a interpretação da regra de estágio muda, registrada
    em `correcoes` com `por` obrigatório.
    """
    try:
        estado = acoes.desconsiderar_recusa(db, conversa_id, por=corpo.por)
    except acoes.AcaoInvalidaError as exc:
        regra = getattr(exc, "regra", None)
        return JSONResponse(status_code=422, content=views.erro(str(exc), regra))
    conversa = db.get_conversa(conversa_id)
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


# --------------------------------------------------------------------------
# Rascunho (change `rascunho-registrado`) — o painel gera e registra, nunca
# envia. `POST`, sempre: gerar gasta cota de LLM e grava linha; registrar
# escolha grava linha. Um `GET` reexecutado por qualquer prefetch de
# navegador custaria dinheiro sozinho (mesma regra das rotas de resumo,
# design.md).
# --------------------------------------------------------------------------


class GerarRascunhoBody(BaseModel):
    por: str | None = None


class EscolhaRascunhoBody(BaseModel):
    opcao: int | None = None
    texto_final: str | None = None
    por: str | None = None


@router.post("/conversas/{conversa_id}/rascunho")
def gerar_rascunho_da_conversa(
    conversa_id: int, corpo: GerarRascunhoBody, db: Database = Depends(_db)
):
    """Gera duas opções via LLM e persiste (§10). O painel NÃO envia — a
    resposta traz, para cada opção, o comando `camucrm enviar ... --rascunho
    <id> --opcao N` pronto para copiar (design.md: "o botão copiar do painel
    copia o texto *e* mostra ao lado o comando pronto").
    """
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return JSONResponse(
            status_code=422, content=views.erro(f"conversa {conversa_id} não existe", None)
        )

    estado = recalcular(db, conversa, persistir=False)
    historico = [(m.direcao, m.texto) for m in db.listar_mensagens(conversa_id)]
    llm = criar_llm()
    try:
        rascunho = gerar_rascunho(
            llm,
            historico[-20:],
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            funil=conversa.funil,
            followups_enviados=conversa.followups_enviados,
            playbook=config.playbook(),
        )
    except (RascunhoInvalidoError, LlmIndisponivelError) as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), "§10"))

    rascunho_id = db.gravar_rascunho(
        conversa_id,
        estagio=estado.estagio,
        temperatura=estado.temperatura,
        funil=conversa.funil,
        followups_enviados=conversa.followups_enviados,
        opcoes=rascunho.opcoes if not rascunho.encerrar else None,
        avisos=rascunho.avisos,
        encerrar=rascunho.encerrar,
        motivo=rascunho.motivo,
        modelo=getattr(llm, "nome", None),
        prompt_versao=PROMPT_VERSAO,
        gerado_por=corpo.por,
    )
    return views.rascunho_para_json(db.rascunho(rascunho_id))


@router.get("/conversas/{conversa_id}/rascunhos")
def rascunhos_da_conversa(
    conversa_id: int, limite: int = 5, db: Database = Depends(_db)
):
    """Histórico de rascunhos da conversa — leitura pura, sem LLM."""
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return views.erro(f"conversa {conversa_id} não existe", None)
    rascunhos = db.rascunhos_da_conversa(conversa_id, limite=limite)
    return {"rascunhos": [views.rascunho_para_json(r) for r in rascunhos]}


@router.post("/rascunhos/{rascunho_id}/escolha")
def registrar_escolha_rascunho(
    rascunho_id: int, corpo: EscolhaRascunhoBody, db: Database = Depends(_db)
):
    """Registro manual da escolha (design.md, caminho 3) — sem `mensagem_id`:
    o operador diz "usei a opção 1" sem que o sistema saiba qual mensagem
    concreta corresponde. Ver `cli.cmd_enviar --rascunho/--opcao` (caminho 1)
    e `acoes.reconciliar_rascunho` (caminho 2) para os vínculos com mensagem.
    """
    rascunho = db.rascunho(rascunho_id)
    if rascunho is None:
        return JSONResponse(
            status_code=422, content=views.erro(f"rascunho {rascunho_id} não existe", None)
        )
    if corpo.opcao is not None and corpo.opcao not in (1, 2):
        return JSONResponse(
            status_code=422, content=views.erro("`opcao` deve ser 1, 2 ou omitida", None)
        )
    if corpo.opcao is None and not corpo.texto_final:
        return JSONResponse(
            status_code=422,
            content=views.erro("informe `opcao` (1 ou 2) ou `texto_final`", None),
        )
    db.registrar_escolha_rascunho(
        rascunho_id, escolhida=corpo.opcao, texto_final=corpo.texto_final, por=corpo.por
    )
    return views.rascunho_para_json(db.rascunho(rascunho_id))


# --------------------------------------------------------------------------
# Resumo (change `resumo-conversa`) — terceira superfície de LLM (ver
# docstring de `camucrm/summaries.py` e CLAUDE.md/§1). `POST`, sempre: gera
# gasta cota de LLM e grava linha; nunca `GET` (requirement "Geração só ao
# clicar, nunca automática"). Cache é conferido ANTES de chamar o LLM
# (requirement "Cache por versão de prompt e mensagem"); LLM indisponível ou
# resumo inválido devolve 200 com `resumo: null`, nunca 500 (requirement
# "Falha de LLM não derruba a tela").
# --------------------------------------------------------------------------


class GerarResumoBody(BaseModel):
    por: str | None = None
    forcar: bool = False


def _montar_contexto_resumo(db: Database, conversa, estado) -> summaries.ContextoResumo:
    historico = [(m.direcao, m.texto) for m in db.listar_mensagens(conversa.id)]
    return summaries.ContextoResumo(
        funil=conversa.funil,
        estagio=estado.estagio,
        temperatura=estado.temperatura,
        sinal=estado.classificacao.sinal,
        fatos=db.fatos_detalhados(conversa.id),
        eventos=db.eventos_da_conversa(conversa.id),
        objecoes=db.objecoes_da_conversa(conversa.id),
        correcoes=db.correcoes_da_conversa(conversa.id),
        followups=db.followups_da_conversa(conversa.id),
        historico=historico,
    )


@router.post("/conversas/{conversa_id}/resumo")
def gerar_resumo_da_conversa(
    conversa_id: int, corpo: GerarResumoBody, db: Database = Depends(_db)
):
    """Gera (ou reaproveita do cache) o resumo da conversa.

    Sem `forcar`, um cache que já viu a última mensagem registrada (staleness
    zero) é devolvido sem chamar o LLM — é a checagem que o requirement
    "Cache por versão de prompt e mensagem" pede antes da chamada. Com
    `forcar=true`, ou sem cache válido, gera de novo e grava via
    `db.gravar_resumo` (`ON CONFLICT ... DO UPDATE` cobre a mesma fronteira).
    """
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return JSONResponse(
            status_code=422, content=views.erro(f"conversa {conversa_id} não existe", None)
        )

    mensagens = db.listar_mensagens_registradas(conversa_id=conversa_id)
    ultima_mensagem_id = mensagens[-1].id if mensagens else None

    cache = db.resumo_vigente(conversa_id, summaries.PROMPT_VERSAO_RESUMO)
    if not corpo.forcar and cache is not None:
        pendentes = db.mensagens_desde(conversa_id, cache.ultima_mensagem_id)
        if pendentes == 0:
            return views.resumo_para_json(cache, mensagens_desde=0)

    estado = recalcular(db, conversa, persistir=False)
    contexto = _montar_contexto_resumo(db, conversa, estado)
    llm = criar_llm()
    try:
        resumo = summaries.gerar(llm, contexto)
    except (summaries.ResumoInvalidoError, LlmIndisponivelError) as exc:
        # Requirement "Falha de LLM não derruba a tela": 200, não 422/500.
        return views.resumo_para_json(None, mensagens_desde=None, erro=str(exc))

    db.gravar_resumo(
        conversa_id,
        resumo=resumo.resumo,
        proximo_passo=resumo.proximo_passo,
        ultima_mensagem_id=ultima_mensagem_id,
        estagio=estado.estagio,
        temperatura=estado.temperatura,
        prompt_versao=summaries.PROMPT_VERSAO_RESUMO,
        modelo=getattr(llm, "nome", None),
        gerado_por=corpo.por,
    )
    novo_cache = db.resumo_vigente(conversa_id, summaries.PROMPT_VERSAO_RESUMO)
    return views.resumo_para_json(novo_cache, mensagens_desde=0)


@router.get("/conversas/{conversa_id}/resumo")
def resumo_da_conversa(conversa_id: int, db: Database = Depends(_db)):
    """Leitura pura do cache — nunca chama LLM, nunca gera."""
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return views.erro(f"conversa {conversa_id} não existe", None)
    cache = db.resumo_vigente(conversa_id, summaries.PROMPT_VERSAO_RESUMO)
    if cache is None:
        return views.resumo_para_json(None, mensagens_desde=None)
    pendentes = db.mensagens_desde(conversa_id, cache.ultima_mensagem_id)
    return views.resumo_para_json(cache, mensagens_desde=pendentes)


# --------------------------------------------------------------------------
# Extração manual (change `extracao-em-lote-por-janela`): o webhook agora
# adia a extração de eventos abaixo do gatilho híbrido
# (`webhook._deve_extrair_agora`) para `camucrm extrair` processar depois —
# esta rota é a válvula de escape para o operador que não quer esperar.
# `POST`, nunca `GET`: gasta cota de LLM, mesma regra de `/rascunho` e
# `/resumo` acima.
# --------------------------------------------------------------------------


@router.post("/conversas/{conversa_id}/extrair")
def extrair_conversa_agora(conversa_id: int, db: Database = Depends(_db)):
    """Extrai a conversa na hora, ignorando o gatilho híbrido do webhook.

    Chama `Extrator.processar_conversa` sem `forcar` — processa só o bloco
    pendente, o mesmo que o webhook faria se o gatilho tivesse permitido, ou
    que `camucrm extrair` faria na próxima rodada. Esta rota só adianta
    QUANDO isso acontece, nunca reprocessa mais do que o pendente.
    """
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        return JSONResponse(
            status_code=422, content=views.erro(f"conversa {conversa_id} não existe", None)
        )
    try:
        extrator = Extrator(db, criar_llm())
        resultado = extrator.processar_conversa(conversa_id)
    except LlmIndisponivelError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), None))
    if resultado.erro:
        return JSONResponse(status_code=422, content=views.erro(resultado.erro, None))
    atualizada = db.get_conversa(conversa_id)
    return {
        "ok": True,
        "mensagens_processadas": resultado.mensagens_processadas,
        "card": views.card_conversa(atualizada, resultado.estado),
    }


# --------------------------------------------------------------------------
# Prospecção B2B (change `prospeccao-b2b-shortlist`) — shortlist separada de
# contatos/conversas (design.md, requirement "Shortlist separada de
# contatos/conversas"): nenhuma rota aqui toca `contatos`/`conversas` para
# escrever, e nenhuma rota de kanban/fila/conversas/métricas lê
# `prospeccoes`. Nenhuma chamada a `camucrm.llm` (o texto é template fixo,
# `camucrm.prospeccao.montar_mensagem`).
#
# ATUALIZAÇÃO (change `envio-prospeccao-pela-evolution-api`): a decisão 2 do
# design.md original — nunca chamar `camucrm.transport`, só o link
# `api.whatsapp.com/send` — foi revertida a pedido do usuário, só para esta
# aba. `POST /prospeccao/{id}/enviar` chama `camucrm.painel.envio.
# enviar_prospeccao`, que É o único lugar de `camucrm/painel/` que importa
# `camucrm.transport` (ver docstring de `envio.py` para o porquê e o que
# continua garantido). Este módulo (`api.py`) continua sem importar
# `camucrm.transport` diretamente — só `envio`. O link `wa.me` continua
# existindo, sem mudança; os dois caminhos coexistem.
# --------------------------------------------------------------------------


class AbrirProspeccaoBody(BaseModel):
    por: str | None = None


class EnviarProspeccaoBody(BaseModel):
    telefone: str
    mensagem: str
    por: str | None = None
    # Change `escolher-instancia-no-envio-prospeccao`: número escolhido no
    # popup entre os cadastrados. Ausente/vazio = instância única do `.env`.
    instancia: str | None = None


class MarcaProspeccaoBody(BaseModel):
    # Change `prospeccao-marcar-enviada-e-nao-whatsapp`: `por` obrigatório
    # (mesma disciplina de toda ação manual do painel — quem marcou fica
    # rastreável); `valor=False` desfaz a marca.
    por: str | None = None
    valor: bool = True


@router.post("/prospeccao/importar")
def importar_prospeccao(arquivo: UploadFile, db: Database = Depends(_db)):
    """Upload de CSV (`petshop,bairro,zona,telefone,nota,avaliacoes,site,
    tier_origem,status_origem`) — parsing aqui, upsert em
    `db.importar_prospeccoes`. `utf-8-sig` tolera planilha exportada com BOM
    (comum em CSV salvo por Excel), sem exigir que o operador reexporte.

    Change `tier-calculado-na-importacao`: mesmo se a planilha trouxer a
    coluna `tier_origem`, o valor é ignorado — `db.importar_prospeccoes`
    calcula o tier a partir de `nota`/`avaliacoes`."""
    bruto = arquivo.file.read().decode("utf-8-sig")
    linhas = list(csv.DictReader(io.StringIO(bruto)))
    resumo = db.importar_prospeccoes(linhas)
    return views.resumo_importacao_para_json(resumo)


@router.get("/prospeccao")
def listar_prospeccao(
    zona: str | None = None,
    bairro: str | None = None,
    nota_minima: float | None = None,
    tier: str | None = None,
    nao_convertidas: bool = False,
    ordenar: str = "nome",
    db: Database = Depends(_db),
):
    """Lista + link de WhatsApp pronto por linha, calculado a partir do
    template de `config.mensagem_prospeccao()` — `mensagem`/`link_whatsapp`
    vêm `None` quando a linha já é conversa real (`views.
    prospeccao_para_json` decide) ou quando não há template configurado.

    `ordenar` (change `prospeccao-filtro-e-ordenacao`): repassado direto a
    `db.listar_prospeccoes` — chave desconhecida cai em `"nome"` lá dentro
    (`prospeccao.ordem_prospeccao_valida`), nunca em erro 4xx."""
    registros = db.listar_prospeccoes(
        zona=zona,
        bairro=bairro,
        nota_minima=nota_minima,
        tier=tier,
        apenas_nao_convertidas=nao_convertidas,
        ordenar=ordenar,
    )
    template = config.mensagem_prospeccao()
    return {
        "prospeccoes": [views.prospeccao_para_json(p, template) for p in registros]
    }


@router.post("/prospeccao/{prospeccao_id}/abrir")
def abrir_prospeccao(
    prospeccao_id: int, corpo: AbrirProspeccaoBody, db: Database = Depends(_db)
):
    """Registra a abertura do link (intenção, não confirmação de envio —
    design.md). Nunca chama `transport.enviar`: o clique já abriu o link em
    outra aba antes desta chamada acontecer (front-end)."""
    db.marcar_prospeccao_aberta(prospeccao_id, por=corpo.por)
    return {"ok": True}


@router.post("/prospeccao/{prospeccao_id}/enviada-manual")
def marcar_prospeccao_enviada_manual_rota(
    prospeccao_id: int, corpo: MarcaProspeccaoBody, db: Database = Depends(_db)
):
    """Botão "Marcar como já enviado" da aba de prospecção (change
    `prospeccao-marcar-enviada-e-nao-whatsapp`) — o operador já contatou o
    petshop por outro caminho e quer o número fora da fila de disparo. NÃO
    toca a Evolution API (por isso não passa por `envio.py`): só grava as
    colunas `enviado_*` com `enviado_instancia = 'manual'`. 422 sem `por`.
    """
    if not (corpo.por or "").strip():
        return JSONResponse(status_code=422, content=views.erro("`por` é obrigatório", None))
    db.marcar_prospeccao_enviada_manual(prospeccao_id, por=corpo.por, valor=corpo.valor)
    return {"ok": True}


@router.post("/prospeccao/{prospeccao_id}/nao-whatsapp")
def marcar_prospeccao_nao_whatsapp_rota(
    prospeccao_id: int, corpo: MarcaProspeccaoBody, db: Database = Depends(_db)
):
    """Botão "Não é número de WhatsApp" da aba de prospecção (change
    `prospeccao-marcar-enviada-e-nao-whatsapp`) — o telefone comercial não
    atende no WhatsApp. A linha continua na tabela (a planilha reimportada
    não deve ressuscitar o número), mas a tela some com o disparo. 422 sem
    `por`.
    """
    if not (corpo.por or "").strip():
        return JSONResponse(status_code=422, content=views.erro("`por` é obrigatório", None))
    db.marcar_prospeccao_nao_whatsapp(prospeccao_id, por=corpo.por, valor=corpo.valor)
    return {"ok": True}


@router.post("/prospeccao/{prospeccao_id}/enviar")
def enviar_prospeccao_rota(
    prospeccao_id: int, corpo: EnviarProspeccaoBody, db: Database = Depends(_db)
):
    """Envia `corpo.mensagem` para `corpo.telefone` pela Evolution API
    (change `envio-prospeccao-pela-evolution-api`) — `telefone`/`mensagem`
    são o que o operador reviu/editou no popup do painel, não relidos do
    banco nem recalculados do template aqui (ver `envio.enviar_prospeccao`).

    422 quando `por`/`telefone`/`mensagem` vier vazio — recusado ANTES de
    `envio.enviar_prospeccao` tocar rede (`CampoObrigatorioError`). 502
    quando a Evolution API recusar ou estiver inacessível
    (`TransporteError`, inclusive faltando configuração no processo do
    painel) — o operador vê o detalhe no popup e decide se tenta de novo ou
    usa o link `wa.me`.
    """
    try:
        resultado = envio.enviar_prospeccao(
            db,
            prospeccao_id,
            telefone=corpo.telefone,
            mensagem=corpo.mensagem,
            por=corpo.por or "",
            instancia=corpo.instancia,
        )
    except envio.CampoObrigatorioError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), None))
    except envio.TransporteError as exc:
        # `TransporteError` não é importado diretamente aqui — `envio.py` é
        # o único módulo do painel que importa `camucrm.transport`; este
        # arquivo referencia o tipo como atributo de `envio` (reexportado por
        # causa do `from ..transport import ... TransporteError` de lá), não
        # com um `from ..transport import TransporteError` próprio, que
        # quebraria a garantia que `tests/test_painel_api.py` prova por AST.
        return JSONResponse(status_code=502, content=views.erro(str(exc), None))
    return {"ok": resultado.ok, "externa_id": resultado.externa_id}


@router.get("/prospeccao/instancias")
def listar_instancias_prospeccao():
    """Números cadastrados na Evolution API, para o popup de envio escolher
    por qual enviar (change `escolher-instancia-no-envio-prospeccao`).

    Delega a `envio.instancias_disponiveis` — `envio.py` continua o único
    módulo do painel que toca `camucrm.transport`. 502 (com detalhe) quando
    falta credencial no processo do painel ou a Evolution API não responde;
    a tela então esconde o seletor e envia pela instância única do `.env`.
    """
    try:
        instancias = envio.instancias_disponiveis()
    except envio.TransporteError as exc:
        return JSONResponse(status_code=502, content=views.erro(str(exc), None))
    return {
        "instancias": [
            {"nome": i.nome, "conectada": i.conectada} for i in instancias
        ]
    }


# --------------------------------------------------------------------------
# Importação de conversa via exportação do WhatsApp (change
# `importacao-conversas-whatsapp`) — parte do contato deixou de acontecer
# só pelo número da Camu (a única porta de entrada automática, via
# `camucrm/webhook.py`); esta rota é o lado de importação do `.txt` que o
# próprio WhatsApp exporta ("Exportar conversa").
#
# Parse em memória (`whatsapp_export.parse`, sem I/O), nunca grava o
# arquivo bruto em disco — mesmo padrão do upload de CSV de prospecção
# acima. `backfill.importar_conversas` é reaproveitado sem nenhuma
# alteração para gravar as mensagens.
#
# Extração é passo separado desta rota, de propósito: o operador revisa o
# resumo do parse primeiro (mídia preservada, linhas não reconhecidas)
# antes de gastar uma chamada de LLM. O front chama a rota que JÁ EXISTE,
# `POST /conversas/{conversa_id}/extrair` (change
# `extracao-em-lote-por-janela`, acima) — nenhuma rota nova para isso, e a
# extração roda com `origem='live'` (o padrão do método, sem `forcar`),
# não `'backfill'`: o `.txt` carrega timestamp real por mensagem, e só
# `origem='backfill'` descarta isso (`design.md` do change, Decisão 1) —
# usar `'live'` é o que deixa estas conversas entrarem em métrica de tempo
# por estágio como qualquer outra.
# --------------------------------------------------------------------------


@router.post("/importacao-whatsapp")
def importar_conversa_whatsapp(
    arquivo: UploadFile,
    telefone: str = Form(...),
    tipo: str = Form(...),
    nome_operador: str = Form(...),
    nome: str | None = Form(None),
    origem: str | None = Form(None),
    db: Database = Depends(_db),
):
    """Upload do `.txt` exportado do WhatsApp — parse, depois
    `backfill.importar_conversas`. `nome_operador` é o nome que aparece no
    export do lado de quem responde pela Camu (decide direção por
    correspondência de nome — `whatsapp_export.parse`, obrigatório, sem
    fallback silencioso). `tipo` precisa ser `b2b`/`b2c` explícito: erro de
    digitação num campo preenchido à mão não deve virar B2C por padrão em
    silêncio, diferente do dump JSON de `camucrm backfill`.
    """
    telefone_normalizado = (telefone or "").strip()
    if not telefone_normalizado:
        return JSONResponse(
            status_code=422, content=views.erro("telefone é obrigatório", None)
        )
    tipo_normalizado = (tipo or "").strip().lower()
    if tipo_normalizado not in (B2B, B2C):
        return JSONResponse(
            status_code=422,
            content=views.erro(f"tipo inválido: {tipo!r} (use 'b2b' ou 'b2c')", None),
        )

    bruto = arquivo.file.read().decode("utf-8-sig")
    try:
        resultado = parse_whatsapp_export(bruto, nosso_nome=nome_operador)
    except (ExportacaoDeGrupoError, NomeOperadorNaoEncontradoError) as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), None))

    nome_final = (nome or resultado.nome_contato or "").strip() or None
    origem_final = (origem or "").strip() or "whatsapp-manual"
    registro = {
        "telefone": telefone_normalizado,
        "nome": nome_final,
        "tipo": tipo_normalizado,
        "origem": origem_final,
        "mensagens": resultado.mensagens,
    }
    resumo = importar_conversas(db, [registro])

    # Mesmas chamadas que `importar_conversas` já fez internamente —
    # idempotentes (`upsert_contato`/`get_or_create_conversa`), só para
    # obter o `conversa_id` que a função não devolve (contrato dela não
    # muda por causa desta rota).
    contato = db.upsert_contato(
        telefone_normalizado,
        nome=nome_final,
        tipo=tipo_normalizado,
        origem=origem_final,
    )
    conversa = db.get_or_create_conversa(contato.id, funil=tipo_normalizado)

    return views.resumo_importacao_whatsapp_para_json(resumo, resultado, conversa.id)


# --------------------------------------------------------------------------
# Ground truth / eval (§7) — change `ground-truth-no-painel`
#
# O dataset continua sendo o arquivo `data/eval/conversas.jsonl` (`design.md`
# deste change: não migra para Postgres, mesma fronteira file-based do
# playbook). `dataset.validar_entrada` é o único lugar que valida uma
# entrada — nenhuma regra é reimplementada aqui, só leitura/escrita do
# arquivo e tradução de erro para HTTP.
#
# `POST /eval/rodar` é a única rota desta seção que chama LLM (via
# `evaluation.runner.rodar`, que por sua vez chama `extraction.prompt` — não
# é um quarto lugar de LLM, é o mesmo caminho de `extraction/` exercitado
# pelo harness de eval). Por isso é `POST`, nunca `GET`, e recusa com 422
# abaixo de `TAMANHO_MINIMO` — estrutural, não só de UI.
# --------------------------------------------------------------------------

# Ground truth pede a conversa inteira, não as últimas 200 (o corte de
# `LIMITE_CONVERSAS_PADRAO`/rota de mensagens é sobre outra tela, ver
# backlog `painel-mensagens-recentes-e-acoes-seguras`) — o teto aqui é só
# uma rede de segurança contra uma conversa anormalmente longa.
LIMITE_MENSAGENS_EVAL = 2000


def _caminho_dataset_eval() -> Path:
    return config.eval_dataset_caminho()


def _caminho_resultado_eval() -> Path:
    """Cache de `POST /eval/rodar` — sempre irmão do dataset (nunca um
    caminho fixo separado), para um teste que aponta `CAMU_EVAL_DATASET`
    para um arquivo temporário nunca escrever no cache real por acidente.
    """
    return _caminho_dataset_eval().parent / "ultimo_resultado.json"


def _carregar_dataset_eval():
    """`dataset.carregar`, mas tolerante a dataset ainda inexistente —
    antes da primeira entrada, o arquivo não existe, e isso não é erro."""
    caminho = _caminho_dataset_eval()
    if not caminho.exists():
        return []
    return carregar(caminho)


def _ler_entradas_brutas_eval(caminho: Path) -> list[dict[str, Any]]:
    if not caminho.exists():
        return []
    entradas = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("//"):
            continue
        entradas.append(json.loads(linha))
    return entradas


def _escrever_entradas_brutas_eval(caminho: Path, entradas: list[dict[str, Any]]) -> None:
    """Grava o dataset inteiro de uma vez — só chamada depois que a entrada
    nova/editada já passou por `validar_entrada` (requirement "Entrada
    malformada nunca corrompe o arquivo": a validação vem sempre antes da
    escrita, nunca depois).
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conteudo = "\n".join(json.dumps(e, ensure_ascii=False) for e in entradas)
    if conteudo:
        conteudo += "\n"
    caminho.write_text(conteudo, encoding="utf-8")


def _indice_entrada_eval(entradas: list[dict[str, Any]], entrada_id: str) -> int | None:
    for i, entrada in enumerate(entradas):
        if str(entrada.get("id")) == entrada_id:
            return i
    return None


def _mensagens_de_conversa_para_bruto(db: Database, conversa_id: int) -> list[dict[str, Any]]:
    # `mais_recentes=False` (change `painel-mensagens-recentes-e-acoes-
    # seguras`): `listar_mensagens_registradas` mudou o PADRÃO para "mais
    # recentes" — o ground truth continua precisando da conversa inteira
    # DESDE O INÍCIO (comentário de `LIMITE_MENSAGENS_EVAL` acima), não da
    # cauda recente, então pede o comportamento antigo explicitamente.
    registros = db.listar_mensagens_registradas(
        conversa_id=conversa_id, limite=LIMITE_MENSAGENS_EVAL, mais_recentes=False
    )
    return [
        {"direcao": m.direcao, "texto": m.texto, "enviada_em": m.enviada_em.isoformat()}
        for m in registros
    ]


def _ler_cache_resultado_eval() -> dict[str, Any] | None:
    caminho = _caminho_resultado_eval()
    if not caminho.exists():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


class NovaEntradaEvalBody(BaseModel):
    id: str | None = None
    funil: str | None = None
    conversa_id: int | None = None
    mensagens: list[dict[str, Any]] | None = None
    rotulo: dict[str, Any]
    nota: str | None = None


class RodarEvalBody(BaseModel):
    por: str | None = None


@router.get("/eval/status")
def status_eval():
    """Contagem, `completo`, lista resumida e avisos — requirement "Status
    do dataset reflete completude real"."""
    try:
        conversas = _carregar_dataset_eval()
    except DatasetInvalidoError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), "§7"))
    return views.status_eval_para_json(conversas, avisos_de_tamanho(conversas))


@router.get("/eval/rotulos/{entrada_id}")
def detalhe_rotulo_eval(entrada_id: str):
    """Detalhe completo (mensagens + rótulo) de uma entrada, para edição."""
    entradas = _ler_entradas_brutas_eval(_caminho_dataset_eval())
    indice = _indice_entrada_eval(entradas, entrada_id)
    if indice is None:
        return views.erro(f"entrada {entrada_id!r} não existe", "§7")
    try:
        entrada = validar_entrada(entradas[indice], entrada_id)
    except DatasetInvalidoError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), "§7"))
    return views.entrada_eval_detalhe_para_json(entrada)


@router.post("/eval/rotulos")
def criar_rotulo_eval(corpo: NovaEntradaEvalBody, db: Database = Depends(_db)):
    """Cria uma entrada nova — `conversa_id` puxa mensagens reais do CRM
    (requirement "Criar entrada a partir de conversa real puxa as
    mensagens"), ou `mensagens[]` digitadas como fallback. Valida via
    `dataset.validar_entrada` antes de gravar (requirement "Entrada
    malformada nunca corrompe o arquivo").
    """
    if corpo.conversa_id is None and not corpo.mensagens:
        return JSONResponse(
            status_code=422,
            content=views.erro("informe `conversa_id` ou `mensagens[]`", "§7"),
        )

    if corpo.conversa_id is not None:
        conversa = db.get_conversa(corpo.conversa_id)
        if conversa is None:
            return JSONResponse(
                status_code=422,
                content=views.erro(f"conversa {corpo.conversa_id} não existe", None),
            )
        mensagens_bruto = _mensagens_de_conversa_para_bruto(db, corpo.conversa_id)
        funil = corpo.funil or conversa.funil
        identificador = corpo.id or f"conversa-{corpo.conversa_id}"
    else:
        mensagens_bruto = corpo.mensagens
        funil = corpo.funil or B2C
        identificador = corpo.id or uuid4().hex[:8]

    bruto = {
        "id": identificador,
        "funil": funil,
        "mensagens": mensagens_bruto,
        "rotulo": corpo.rotulo,
        "nota": corpo.nota,
    }
    try:
        entrada = validar_entrada(bruto, "painel")
    except DatasetInvalidoError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), "§7"))

    caminho = _caminho_dataset_eval()
    entradas = _ler_entradas_brutas_eval(caminho)
    if _indice_entrada_eval(entradas, identificador) is not None:
        return JSONResponse(
            status_code=422, content=views.erro(f"id {identificador!r} já existe", "§7")
        )
    entradas.append(bruto)
    _escrever_entradas_brutas_eval(caminho, entradas)
    return {"ok": True, "entrada": views.entrada_eval_detalhe_para_json(entrada)}


@router.put("/eval/rotulos/{entrada_id}")
def editar_rotulo_eval(entrada_id: str, corpo: NovaEntradaEvalBody, db: Database = Depends(_db)):
    """Edita o rótulo de uma entrada existente, revalidando (requirement
    "Detalhe de entrada é editável" — o `id` é sempre preservado, vem do
    path, nunca do corpo).
    """
    caminho = _caminho_dataset_eval()
    entradas = _ler_entradas_brutas_eval(caminho)
    indice = _indice_entrada_eval(entradas, entrada_id)
    if indice is None:
        return JSONResponse(
            status_code=422, content=views.erro(f"entrada {entrada_id!r} não existe", "§7")
        )
    existente = entradas[indice]

    if corpo.conversa_id is not None:
        conversa = db.get_conversa(corpo.conversa_id)
        if conversa is None:
            return JSONResponse(
                status_code=422,
                content=views.erro(f"conversa {corpo.conversa_id} não existe", None),
            )
        mensagens_bruto = _mensagens_de_conversa_para_bruto(db, corpo.conversa_id)
        funil = corpo.funil or conversa.funil
    elif corpo.mensagens is not None:
        mensagens_bruto = corpo.mensagens
        funil = corpo.funil or existente.get("funil")
    else:
        mensagens_bruto = existente.get("mensagens")
        funil = corpo.funil or existente.get("funil")

    bruto = {
        "id": entrada_id,
        "funil": funil,
        "mensagens": mensagens_bruto,
        "rotulo": corpo.rotulo,
        "nota": corpo.nota if corpo.nota is not None else existente.get("nota"),
    }
    try:
        entrada = validar_entrada(bruto, entrada_id)
    except DatasetInvalidoError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), "§7"))

    entradas[indice] = bruto
    _escrever_entradas_brutas_eval(caminho, entradas)
    return {"ok": True, "entrada": views.entrada_eval_detalhe_para_json(entrada)}


@router.delete("/eval/rotulos/{entrada_id}")
def excluir_rotulo_eval(entrada_id: str):
    """Remove uma entrada (requirement "Detalhe de entrada é editável")."""
    caminho = _caminho_dataset_eval()
    entradas = _ler_entradas_brutas_eval(caminho)
    indice = _indice_entrada_eval(entradas, entrada_id)
    if indice is None:
        return JSONResponse(
            status_code=422, content=views.erro(f"entrada {entrada_id!r} não existe", "§7")
        )
    del entradas[indice]
    _escrever_entradas_brutas_eval(caminho, entradas)
    return {"ok": True}


@router.post("/eval/rodar")
def rodar_eval_route(corpo: RodarEvalBody):
    """Roda `evaluation.rodar()` contra o dataset inteiro — gasta cota de
    LLM, por isso `POST`, nunca `GET`. Recusa com 422 abaixo de
    `TAMANHO_MINIMO` (requirement "Rodar eval abaixo do tamanho mínimo é
    estruturalmente recusado") sem chamar o LLM. Resultado é cacheado em
    arquivo, nunca em tabela (design.md). Autenticação já vem do
    `Depends(exigir_token)` do `router` (não precisa de `Database`: o
    dataset é lido do arquivo, não do Postgres).
    """
    try:
        conversas = _carregar_dataset_eval()
    except DatasetInvalidoError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), "§7"))

    if len(conversas) < TAMANHO_MINIMO:
        return JSONResponse(
            status_code=422,
            content=views.erro(
                f"dataset com {len(conversas)} conversa(s); §7 exige "
                f"{TAMANHO_MINIMO} para rodar o eval",
                "§7",
            ),
        )

    try:
        llm = criar_llm()
        relatorio = rodar_eval(llm, conversas)
    except LlmIndisponivelError as exc:
        return JSONResponse(status_code=422, content=views.erro(str(exc), "§7"))

    cache = views.relatorio_eval_para_cache(relatorio)
    caminho_resultado = _caminho_resultado_eval()
    caminho_resultado.parent.mkdir(parents=True, exist_ok=True)
    caminho_resultado.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return views.resultado_eval_para_json(cache)


@router.get("/eval/resultado")
def resultado_eval_route():
    """Lê o cache de `POST /eval/rodar`; `disponivel: false` antes da
    primeira execução."""
    cache = _ler_cache_resultado_eval()
    if cache is None:
        return {"disponivel": False}
    return views.resultado_eval_para_json(cache)
