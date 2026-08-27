"""Extrator: chama o LLM sobre o delta e grava fatos, objeções e transições.

Faz o mínimo que só o LLM pode fazer (ler linguagem natural) e delega tudo
mais: validação para `contract`, decisão para `rules`, persistência para `db`.

Idempotência (§2): `conversas.ultima_mensagem_processada_id` marca até onde já
se leu. Reprocessar o mesmo bloco não duplica fato (índice único em `fatos`),
não duplica evento de estágio (`transicao` devolve `None` quando nada muda) e
não regride estágio (`merge` torna fato monotônico).

Chunking (§8, change `backfill-seguro-para-reexecucao`): um histórico grande
— sobretudo com `forcar=True`, que relê a conversa inteira, não só o delta —
não vai para o LLM numa única chamada. Estourar o contexto do modelo produz
extração vazia e SILENCIOSA (o LLM não avisa que ignorou metade da conversa),
e mesmo sem estourar, um corpus enorme degrada recall. `TAMANHO_MAXIMO_BLOCO`
divide o bloco em pedaços cronologicamente ordenados, cada um sua própria
chamada — ver `_dividir_em_blocos` e o laço em `processar_conversa`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from ..db import Database
from ..llm import LlmClient, LlmIndisponivelError
from ..pipeline import EstadoConversa, recalcular
from ..rules.estagio import ORIGEM_LIVE, estagio_inicial
from . import prompt as prompt_mod
from .contract import (
    ContratoInvalidoError,
    Democao,
    Extracao,
    build_corpus,
    extracao_vazia,
    merge,
    momento_da_evidencia,
    validar,
)

logger = logging.getLogger("camucrm.extracao")

# Tamanho máximo de mensagens por chamada de LLM (§8). Arbitrário mas
# deliberadamente conservador: o objetivo é nunca chegar perto do limite de
# contexto do modelo, não maximizar o tamanho do bloco. Um histórico maior
# que isso vira múltiplas chamadas, nunca uma monolítica.
TAMANHO_MAXIMO_BLOCO = 200


def _dividir_em_blocos(
    mensagens: list[tuple[int, str, str, datetime]], tamanho: int
) -> list[list[tuple[int, str, str, datetime]]]:
    """Divide um bloco de mensagens (já ordenadas cronologicamente, ver
    `Database.mensagens_novas`) em pedaços de até `tamanho`, preservando a
    ordem entre pedaços."""
    if tamanho <= 0:
        return [mensagens]
    return [
        mensagens[i : i + tamanho] for i in range(0, len(mensagens), tamanho)
    ]


@dataclass(frozen=True)
class ResultadoExtracao:
    """O que uma rodada de extração produziu, para log e para o eval."""

    conversa_id: int
    mensagens_processadas: int
    extracao: Extracao
    estado: EstadoConversa | None = None
    erro: str | None = None

    @property
    def democoes(self) -> tuple[Democao, ...]:
        return self.extracao.democoes


class Extrator:
    def __init__(self, db: Database, llm: LlmClient):
        self.db = db
        self.llm = llm

    def processar_conversa(
        self,
        conversa_id: int,
        *,
        agora: datetime | None = None,
        origem: str = ORIGEM_LIVE,
        forcar: bool = False,
    ) -> ResultadoExtracao:
        """Processa o bloco novo de uma conversa.

        `forcar=True` reprocessa desde o início — usado quando o prompt muda e
        se quer reextrair. Continua idempotente: os fatos já gravados não
        duplicam, e o estágio não regride.

        Histórico grande (§8): o bloco novo é dividido em pedaços de até
        `TAMANHO_MAXIMO_BLOCO` mensagens, cada um sua própria chamada de LLM,
        em vez de uma chamada monolítica para o histórico inteiro — ver
        docstring do módulo. Cada pedaço é persistido e recalculado antes do
        próximo, então uma falha no meio do caminho preserva o que já foi
        processado (o pedaço seguinte, não os anteriores, é reprocessado na
        próxima rodada).
        """
        agora = agora or datetime.now(timezone.utc)
        conversa = self.db.get_conversa(conversa_id)
        if conversa is None:
            raise ValueError(f"conversa {conversa_id} não existe")

        desde = None if forcar else conversa.ultima_mensagem_processada_id
        novas = self.db.mensagens_novas(conversa_id, desde)

        if not novas:
            # Sem texto novo não há o que extrair — mas o tempo passou, e
            # esfriar é o que acontece quando nada acontece. Recalcular aqui é
            # de graça e é o que mantém a fila honesta.
            estado = recalcular(self.db, conversa, agora=agora, origem=origem)
            return ResultadoExtracao(conversa_id, 0, extracao_vazia(), estado)

        fatos_antes = self.db.fatos_da_conversa(conversa_id)
        combinada = Extracao(fatos=dict(fatos_antes), evidencias={})

        # Estágio atribuído à objeção (§4): fora de `forcar`, o estágio da
        # conversa ANTES deste bloco chegar (`conversa.estagio`, o cache lido
        # no topo do método) — é o que separa "objetou antes da prévia" de
        # "objetou depois", e é exatamente o valor que
        # `test_bloco_novo_avanca_e_registra_objecao` (tests/test_e2e.py)
        # prova.
        #
        # Com `forcar=True` (backfill, ou `camucrm extrair --forcar`),
        # `conversa.estagio` NÃO serve mais: cada rodada relê a conversa
        # INTEIRA desde o início (`desde=None` acima), mas o cache já foi
        # reescrito pela rodada anterior — a primeira execução parte de
        # S0/P0, a segunda encontra o cache já em S4/SX e usa ISSO como "o
        # estágio de antes". A mesma objeção, gravada de novo pela mesma
        # releitura, ganharia um `estagio` diferente a cada rodada, e o
        # `ON CONFLICT` de `objecoes_dedupe_idx` (que inclui `estagio` na
        # chave, por especificação) nunca colidiria — a rodada duplicaria a
        # linha do mesmo jeito que motivou este change. `forcar=True` sempre
        # relê do zero, então "o estágio de antes" tem que ser sempre o
        # mesmo zero: o estágio inicial do funil, do mesmo jeito que
        # `pipeline._trilha_de_backfill` sempre reparte da origem, e não do
        # cache, para a trilha de estágio em si.
        #
        # Entre PEDAÇOS do mesmo bloco (chunking, §8): o estágio de
        # referência do pedaço seguinte é o estágio já recalculado depois do
        # pedaço anterior (`atualizada.estagio` abaixo) — o progresso feito
        # DENTRO desta mesma rodada continua valendo, só a rodada anterior
        # (cache stale de uma invocação passada) é que não serve.
        estagio_referencia = (
            estagio_inicial(conversa.funil) if forcar else conversa.estagio
        )

        total_processadas = 0
        erro: str | None = None
        estado: EstadoConversa | None = None

        for bloco in _dividir_em_blocos(novas, TAMANHO_MAXIMO_BLOCO):
            fatos_conhecidos = self.db.fatos_da_conversa(conversa_id)
            mensagens = [(direcao, texto) for _, direcao, texto, _ in bloco]
            corpus = build_corpus(mensagens)

            try:
                bruto = self.llm.completar(
                    prompt_mod.system_prompt(),
                    prompt_mod.user_prompt(
                        mensagens,
                        fatos_conhecidos=fatos_conhecidos,
                        funil=conversa.funil,
                    ),
                    json_estrito=True,
                )
                extracao = validar(bruto, corpus=corpus)
            except (LlmIndisponivelError, ContratoInvalidoError) as exc:
                # Falha de extração deixa a conversa exatamente onde estava. É
                # o lado seguro do erro (§7): nenhum avanço de estágio, e o
                # pedaço continua não processado, então a próxima rodada
                # tenta de novo — pedaços já processados neste laço ficam de
                # pé.
                logger.warning(
                    "Extração falhou na conversa %s: %s", conversa_id, exc
                )
                erro = str(exc)
                break

            if extracao.democoes:
                logger.info(
                    "Conversa %s: %s campo(s) rebaixado(s) por falta de "
                    "evidência: %s",
                    conversa_id,
                    len(extracao.democoes),
                    "; ".join(str(d) for d in extracao.democoes),
                )

            self._persistir(
                conversa_id,
                estagio_referencia,
                extracao,
                agora,
                [(texto, enviada_em) for _, _, texto, enviada_em in bloco],
            )

            ultima_id = bloco[-1][0]
            self.db.atualizar_estado_conversa(
                conversa_id, ultima_mensagem_processada_id=ultima_id
            )

            atualizada = self.db.get_conversa(conversa_id)
            assert atualizada is not None
            estado = recalcular(self.db, atualizada, agora=agora, origem=origem)
            estagio_referencia = atualizada.estagio

            combinada = merge(combinada, extracao)
            total_processadas += len(bloco)

        if estado is None:
            # Nenhum pedaço processou com sucesso (erro já no primeiro).
            atual = self.db.get_conversa(conversa_id)
            assert atual is not None
            estado = recalcular(self.db, atual, agora=agora, origem=origem)

        return ResultadoExtracao(
            conversa_id, total_processadas, combinada, estado, erro=erro
        )

    def _persistir(
        self,
        conversa_id: int,
        estagio_no_momento: str,
        extracao: Extracao,
        agora: datetime,
        mensagens: list[tuple[str, datetime]],
    ) -> None:
        # Cada fato guarda o momento da mensagem que o evidencia, não o da
        # extração — ver `momento_da_evidencia`.
        momentos = {
            campo: momento
            for campo, evidencia in extracao.evidencias.items()
            if (momento := momento_da_evidencia(evidencia, mensagens)) is not None
        }
        self.db.gravar_fatos(
            conversa_id,
            dict(extracao.fatos),
            dict(extracao.evidencias),
            extraido_em=agora,
            momentos=momentos,
        )
        if extracao.objecao:
            # A objeção guarda o estágio em que ela apareceu: §4 usa isso para
            # separar "objeta preço antes de ver a prévia" de "objeta preço
            # depois", que são problemas comerciais diferentes.
            self.db.gravar_objecao(
                conversa_id,
                extracao.objecao,
                estagio=estagio_no_momento,
                trecho=extracao.evidencias.get("objecao"),
                em=momentos.get("objecao", agora),
            )

    def processar_todas(
        self, *, agora: datetime | None = None, limite: int = 500
    ) -> list[ResultadoExtracao]:
        """Roda a extração sobre todas as conversas abertas com bloco novo."""
        agora = agora or datetime.now(timezone.utc)
        resultados = []
        for conversa in self.db.listar_conversas_abertas(limite=limite):
            try:
                resultados.append(
                    self.processar_conversa(conversa.id, agora=agora)
                )
            except Exception as exc:  # noqa: BLE001 - uma conversa não derruba o lote
                logger.exception("Erro processando conversa %s: %s", conversa.id, exc)
        return resultados
