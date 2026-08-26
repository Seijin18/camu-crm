"""Extrator: chama o LLM sobre o delta e grava fatos, objeções e transições.

Faz o mínimo que só o LLM pode fazer (ler linguagem natural) e delega tudo
mais: validação para `contract`, decisão para `rules`, persistência para `db`.

Idempotência (§2): `conversas.ultima_mensagem_processada_id` marca até onde já
se leu. Reprocessar o mesmo bloco não duplica fato (índice único em `fatos`),
não duplica evento de estágio (`transicao` devolve `None` quando nada muda) e
não regride estágio (`merge` torna fato monotônico).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from ..db import Database
from ..llm import LlmClient, LlmIndisponivelError
from ..pipeline import EstadoConversa, recalcular
from ..rules.estagio import ORIGEM_LIVE
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

        fatos_conhecidos = self.db.fatos_da_conversa(conversa_id)
        mensagens = [(direcao, texto) for _, direcao, texto, _ in novas]
        corpus = build_corpus(texto for _, _, texto, _ in novas)

        try:
            bruto = self.llm.completar(
                prompt_mod.system_prompt(),
                prompt_mod.user_prompt(
                    mensagens, fatos_conhecidos=fatos_conhecidos, funil=conversa.funil
                ),
                json_estrito=True,
            )
            extracao = validar(bruto, corpus=corpus)
        except (LlmIndisponivelError, ContratoInvalidoError) as exc:
            # Falha de extração deixa a conversa exatamente onde estava. É o
            # lado seguro do erro (§7): nenhum avanço de estágio, e o bloco
            # continua não processado, então a próxima rodada tenta de novo.
            logger.warning("Extração falhou na conversa %s: %s", conversa_id, exc)
            estado = recalcular(self.db, conversa, agora=agora, origem=origem)
            return ResultadoExtracao(
                conversa_id, 0, extracao_vazia(), estado, erro=str(exc)
            )

        if extracao.democoes:
            logger.info(
                "Conversa %s: %s campo(s) rebaixado(s) por falta de evidência: %s",
                conversa_id,
                len(extracao.democoes),
                "; ".join(str(d) for d in extracao.democoes),
            )

        self._persistir(
            conversa_id,
            conversa.estagio,
            extracao,
            agora,
            [(texto, enviada_em) for _, _, texto, enviada_em in novas],
        )

        ultima_id = novas[-1][0]
        self.db.atualizar_estado_conversa(
            conversa_id, ultima_mensagem_processada_id=ultima_id
        )

        atualizada = self.db.get_conversa(conversa_id)
        assert atualizada is not None
        estado = recalcular(self.db, atualizada, agora=agora, origem=origem)

        combinada = merge(
            Extracao(fatos=dict(fatos_conhecidos), evidencias={}), extracao
        )
        return ResultadoExtracao(conversa_id, len(novas), combinada, estado)

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
