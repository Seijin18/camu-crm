"""Tempo real do painel: SSE com `token_de_mudanca` como cursor (§13,
antecipado — change `painel-tempo-real`, `design.md`).

## Decisão: poller único por processo, não `LISTEN/NOTIFY`

`PollerMudanca` roda um único `asyncio.Task` por processo, que consulta
`Database.token_de_mudanca` a cada `INTERVALO_POLL` segundos e acorda todos os
geradores SSE conectados quando o token muda — N clientes conectados geram 1
consulta por ciclo, não N (`design.md`, requirement "Poller único por
processo").

Por que não `LISTEN/NOTIFY` já nesta entrega: seria exato (zero polling), mas
não resolve sozinho o buraco de reconexão — um cliente que caiu e volta ainda
precisa de um cursor para saber o que perdeu entre a queda e a reconexão. O
token de 3 partes (`db.token_de_mudanca`) **é** esse cursor
(`?desde_id=N` no stream), então ele é necessário de qualquer forma.

**Caminho de upgrade, registrado para ficar barato no futuro:** trocar o
corpo de `PollerMudanca._ciclo` por `LISTEN camu_mudanca` (com `NOTIFY`
disparado pelos mesmos pontos que hoje mudam o token: inserir mensagem,
gravar evento de estágio, atualizar `conversas.atualizado_em`) não muda uma
linha do contrato SSE exposto ao cliente — só a implementação interna de
como este módulo descobre que algo mudou. `aguardar_mudanca` continua
devolvendo o mesmo booleano, e o gerador (`gerador_sse`) nem precisa saber
qual dos dois mecanismos está por trás.

## Risco documentado: bloqueio do event loop

`Database` é `psycopg` **síncrono**. Toda leitura de banco feita de dentro do
gerador `async def` do SSE passa por `asyncio.to_thread` — uma chamada direta
aqui congelaria todos os clientes SSE conectados, não só o que fez a chamada
(o bug mais provável identificado no `design.md`). As rotas normais de
`api.py` continuam em `def` puro, sem mudança — Starlette já usa threadpool
para elas.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import AsyncIterator, Callable

from ..db import Database, MensagemRegistro

logger = logging.getLogger("camucrm.painel.stream")

INTERVALO_POLL = 1.5
INTERVALO_HEARTBEAT = 20.0


class PollerMudanca:
    """Poller único por processo: um `asyncio.Task`, um `asyncio.Event`
    compartilhado por todos os geradores SSE conectados.

    `obter_token` é uma chamada **síncrona** (tipicamente
    `Database.token_de_mudanca`) — `_ciclo` é quem a despacha via
    `asyncio.to_thread`, nunca a chama direto no event loop.
    """

    def __init__(self, obter_token: Callable[[], str]):
        self._obter_token = obter_token
        self._token_atual: str | None = None
        self._event = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def token_atual(self) -> str | None:
        return self._token_atual

    async def verificar_uma_vez(self) -> bool:
        """Um ciclo de verificação, sem `sleep` — é o que os testes chamam
        diretamente para não depender de tempo real (CLAUDE.md/`tasks.md`:
        "nada de sleep real na suíte").

        Devolve `True` quando o token mudou (e o `Event` foi disparado para
        quem estava esperando), `False` quando não mudou nada.
        """
        novo_token = await asyncio.to_thread(self._obter_token)
        if novo_token == self._token_atual:
            return False
        self._token_atual = novo_token
        # Broadcast sem risco de corrida: quem já estava em `await
        # evento.wait()` segura a referência ao objeto antigo, então o
        # `.set()` abaixo os acorda; quem chamar `aguardar_mudanca` depois
        # deste ponto pega o `Event` novo, ainda não disparado. Isso evita o
        # problema clássico de "um cliente limpa o Event antes do outro ver
        # que ele foi setado".
        evento_antigo, self._event = self._event, asyncio.Event()
        evento_antigo.set()
        return True

    async def _ciclo(self) -> None:
        while True:
            try:
                await self.verificar_uma_vez()
            except Exception:  # noqa: BLE001 - poller não pode morrer por um erro passageiro de banco
                logger.exception("erro ao consultar token_de_mudanca; tentando de novo no próximo ciclo")
            await asyncio.sleep(INTERVALO_POLL)

    def iniciar(self) -> None:
        """Sobe o `asyncio.Task` único — chamado no startup do FastAPI
        (`server.py`). Idempotente: chamar de novo com o task já rodando não
        cria um segundo poller.
        """
        if self._task is None:
            self._task = asyncio.create_task(self._ciclo())

    async def parar(self) -> None:
        """Cancela o `asyncio.Task` — chamado no shutdown do FastAPI."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def aguardar_mudanca(self, timeout: float = INTERVALO_HEARTBEAT) -> bool:
        """Espera o próximo disparo do poller, ou o `timeout` do heartbeat.

        Devolve `True` quando o token mudou dentro do prazo, `False` quando
        o `timeout` venceu antes (momento de emitir `: ping`).
        """
        evento = self._event
        try:
            await asyncio.wait_for(evento.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


def _formatar_mensagem(mensagem: MensagemRegistro) -> str:
    dados = {
        "id": mensagem.id,
        "conversa_id": mensagem.conversa_id,
        "direcao": mensagem.direcao,
        "texto": mensagem.texto,
        "enviada_em": mensagem.enviada_em.isoformat(),
    }
    return f"id: {mensagem.id}\nevent: mensagem\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n"


def _formatar_mudanca(token: str) -> str:
    return f"event: mudanca\ndata: {json.dumps({'token': token})}\n\n"


_PING = ": ping\n\n"


async def gerador_sse(
    db: Database, poller: PollerMudanca, *, desde_id: int | None
) -> AsyncIterator[str]:
    """Gerador `async def` consumido pela rota `GET /api/stream`.

    Único ponto do painel onde `psycopg` é chamado de dentro de uma
    coroutine — por isso toda leitura aqui passa por `asyncio.to_thread`
    (requirement "Leitura de banco no gerador SSE nunca bloqueia o loop").

    `desde_id` é o cursor de reconexão: antes de entrar no laço de tempo
    real, o gerador entrega tudo o que foi registrado desde aquele id — é o
    que garante que um cliente que caiu e voltou não perde mensagem
    (`design.md`, requirement "Token de mudança como cursor", cenário
    "Reconexão com desde_id não perde eventos").

    Sem `desde_id` (conexão nova, não uma reconexão), o gerador não replaya o
    histórico inteiro de mensagens — isto é um feed ao vivo, não um backfill.
    O cursor nasce no teto atual (`max(mensagens.id)`, primeira parte de
    `token_de_mudanca` — `db.py`), e só o que acontecer daqui pra frente é
    emitido.
    """
    if desde_id is not None:
        cursor = desde_id
        pendentes = await asyncio.to_thread(db.listar_mensagens_registradas, desde_id=cursor)
        for mensagem in pendentes:
            cursor = mensagem.id
            yield _formatar_mensagem(mensagem)
    else:
        token_inicial = await asyncio.to_thread(db.token_de_mudanca)
        cursor = int(token_inicial.split(":")[0])

    while True:
        mudou = await poller.aguardar_mudanca(timeout=INTERVALO_HEARTBEAT)
        if not mudou:
            yield _PING
            continue

        novas = await asyncio.to_thread(db.listar_mensagens_registradas, desde_id=cursor)
        for mensagem in novas:
            cursor = mensagem.id
            yield _formatar_mensagem(mensagem)

        token = poller.token_atual
        if token is not None:
            yield _formatar_mudanca(token)
