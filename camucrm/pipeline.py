"""Orquestração: junta fatos do banco, aplica as regras, grava o resultado.

É o único lugar onde as três camadas se encontram, e a ordem importa:

    fatos (LLM, já extraídos)  ->  regras (determinísticas)  ->  banco

`recalcular` é barato e não chama LLM nenhum. Isso é o ponto de §1: "se as
regras mudarem, basta reprocessar os fatos já extraídos — sem custo de LLM e
sem reinterpretar conversa antiga". Rodar `recalcular` sobre a base inteira é
uma operação de segundos, e é ela que mantém a temperatura correta mesmo em
conversas onde nada aconteceu — porque esfriar é o que acontece quando nada
acontece.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from .db import Database, Conversa
from .rules import estagio as regras_estagio
from .rules.sinais import SinaisConversa, construir_sinais
from .rules.temperatura import Classificacao, classificar
from .taxonomia import is_terminal

logger = logging.getLogger("camucrm.pipeline")


@dataclass(frozen=True)
class EstadoConversa:
    """Resultado de um recálculo: o que as regras dizem agora."""

    conversa_id: int
    estagio: str
    classificacao: Classificacao
    sinais: SinaisConversa
    transicao: regras_estagio.Transicao | None = None

    @property
    def temperatura(self) -> str:
        return self.classificacao.temperatura


def carregar_sinais(
    db: Database, conversa: Conversa, *, agora: datetime | None = None
) -> SinaisConversa:
    """Monta os sinais de uma conversa a partir do que está gravado."""
    marcos = db.marcos_da_conversa(conversa.id)
    return construir_sinais(
        db.listar_mensagens(conversa.id),
        funil=conversa.funil,
        agora=agora or datetime.now(timezone.utc),
        preco_apresentado_em=db.fato_registrado_em(conversa.id, "preco_apresentado"),
        autorizou_em=db.fato_registrado_em(conversa.id, "autorizou_envio_material"),
        followups_enviados=conversa.followups_enviados,
        ultimo_followup_em=db.ultimo_followup_em(conversa.id),
        avancou_estagio_em=db.ultimo_avanco_em(conversa.id),
        estagio_maximo_alcancado=db.estagio_maximo_alcancado(conversa.id),
        ganho="ganho" in marcos,
        consignacao_assinada="consignacao_assinada" in marcos,
        primeira_reposicao="primeira_reposicao" in marcos,
    )


def recalcular(
    db: Database,
    conversa: Conversa,
    *,
    agora: datetime | None = None,
    origem: str = regras_estagio.ORIGEM_LIVE,
    persistir: bool = True,
) -> EstadoConversa:
    """Aplica estágio e temperatura a uma conversa e grava o que mudou.

    Duas origens, dois comportamentos, e a diferença é a da §8:

    - **live**: grava o avanço observado, um evento por transição, no momento
      em que ela aconteceu. Só grava quando há transição de verdade —
      reprocessar não pode duplicar evento (§2).
    - **backfill**: reconstrói a **trilha** inteira a partir do estágio inicial
      do funil. Uma conversa de julho que recebeu a foto e depois esfriou
      deriva direto para `SX`; gravar só isso apagaria o fato de ela ter
      chegado em S2, e com ele a métrica de conversão que §8 diz que o
      backfill deve produzir. Os timestamps dessa trilha não valem nada — é
      exatamente por isso que ela sai marcada como backfill e fica fora de
      qualquer métrica de tempo.

    A temperatura, nos dois casos, é sobrescrita livremente: ela é função do
    relógio e muda sozinha.
    """
    agora = agora or datetime.now(timezone.utc)
    fatos = db.fatos_da_conversa(conversa.id)
    sinais = carregar_sinais(db, conversa, agora=agora)

    if origem == regras_estagio.ORIGEM_BACKFILL:
        estagio_final, transicoes = _trilha_de_backfill(db, conversa, fatos, sinais)
        momentos: dict[str, datetime] = {}
    else:
        estagio_final, transicoes = _avanco_ao_vivo(db, conversa, fatos, sinais)
        momentos = momentos_de_estagio(db, conversa, sinais)

    # §5 conta "avançou de estágio nas últimas 24h" pelo momento em que o
    # avanço ACONTECEU, não pelo momento em que o sistema o percebeu. Uma
    # extração em lote que descobre hoje um avanço de três dias atrás não pode
    # esquentar a conversa — senão rodar `make extrair` deixaria a base inteira
    # quente e a fila do dia perderia o sentido.
    #
    # Sem este ajuste a temperatura também oscilaria: `carregar_sinais` roda
    # antes de os eventos desta passada existirem, então a leitura seguinte
    # devolveria outra classificação sem que nada tivesse acontecido.
    if transicoes:
        sinais = replace(
            sinais,
            avancou_estagio_hoje=_avanco_recente(transicoes, momentos, agora),
        )

    classificacao = classificar(sinais, fatos)

    if persistir:
        for movimento in transicoes:
            db.gravar_evento_estagio(
                conversa.id,
                movimento.de,
                movimento.para,
                origem=movimento.origem,
                motivo=movimento.motivo,
                em=(
                    momentos.get(movimento.para, agora)
                    if movimento.origem == regras_estagio.ORIGEM_LIVE
                    else None
                ),
            )
            logger.info(
                "Conversa %s: %s -> %s (%s, %s)",
                conversa.id,
                movimento.de,
                movimento.para,
                movimento.motivo,
                movimento.origem,
            )
        db.atualizar_estado_conversa(
            conversa.id,
            estagio=estagio_final,
            temperatura=classificacao.temperatura,
            bola_com=sinais.bola_com,
        )

    return EstadoConversa(
        conversa_id=conversa.id,
        estagio=estagio_final,
        classificacao=classificacao,
        sinais=sinais,
        transicao=transicoes[-1] if transicoes else None,
    )


def _avanco_recente(
    transicoes: list[regras_estagio.Transicao],
    momentos: dict[str, datetime],
    agora: datetime,
) -> bool:
    """Se a transição mais avançada desta passada ocorreu nas últimas 24h."""
    ultima = transicoes[-1]
    quando = momentos.get(ultima.para, agora)
    return (agora - quando) < timedelta(hours=24)


def _estagio_de_partida(db: Database, conversa: Conversa) -> str:
    """De onde a derivação parte: o histórico, não o cache.

    `conversas.estagio` é conveniência para a fila não reprocessar tudo a cada
    consulta. `eventos_estagio` é o que aconteceu. Quando os dois divergem, o
    histórico ganha — é o que torna verdadeira a promessa de que o estado é
    recalculável, e o que permite consertar um estágio inflado apagando o
    evento que não devia existir.
    """
    return db.estagio_corrente(conversa.id) or conversa.estagio


def _avanco_ao_vivo(db, conversa, fatos, sinais):
    """O avanço observado — a trilha inteira, não só o estágio final.

    Parte do estágio que o histórico de eventos registra, não do cache em
    `conversas.estagio` (ver `_estagio_de_partida`).

    Um bloco de mensagens pode cruzar vários estágios de uma vez: a cliente
    manda a foto, recebe a prévia e o preço, e responde, tudo antes de a
    extração rodar. Gravar só `S5` deixaria S1, S2, S3 e S4 sem evento — e com
    isso `S1→S2` e `S4→S6`, que §14 chama de "as métricas que justificam o
    sistema", seriam permanentemente "sem amostra". A conversa passou por
    aqueles estágios; o histórico precisa dizer isso.

    Diferente do backfill, aqui cada transição leva o timestamp **do que a
    disparou** (a mensagem que evidencia o fato, o marco manual), e não o do
    processamento. É o que mantém `metrics.tempo_por_estagio` medindo tempo de
    verdade mesmo quando a extração roda em lote.
    """
    estagio_atual = _estagio_de_partida(db, conversa)
    transicoes: list[regras_estagio.Transicao] = []

    # Conversa encerrada por silêncio que voltou a falar reabre no maior
    # estágio já alcançado — não vira lead novo (§3, `reabrir`).
    if (
        is_terminal(estagio_atual)
        and not fatos.get("recusa_explicita")
        and sinais.bola_com == "camu"
    ):
        reabertura = regras_estagio.reabrir(
            estagio_atual, sinais.estagio_maximo_alcancado
        )
        if reabertura:
            transicoes.append(reabertura)
            estagio_atual = reabertura.para

    for derivacao in regras_estagio.trilha(fatos, sinais):
        movimento = regras_estagio.transicao(
            estagio_atual, derivacao, origem=regras_estagio.ORIGEM_LIVE
        )
        if movimento is None:
            continue
        transicoes.append(movimento)
        estagio_atual = movimento.para
    return estagio_atual, transicoes


def momentos_de_estagio(
    db: Database, conversa: Conversa, sinais: SinaisConversa
) -> dict[str, datetime]:
    """Quando cada estágio foi de fato atingido, para carimbar os eventos.

    Sem isto, uma extração em lote gravaria todas as transições da conversa com
    o mesmo timestamp (o do processamento), e `metrics.tempo_por_estagio`
    mediria zero hora entre estágios que na conversa real levaram dias. É o
    mesmo cuidado que §8 exige do backfill, aplicado ao caminho ao vivo.

    Estágio ausente do dicionário cai para `agora` em `recalcular` — o que
    acontece com os terminais, cujo gatilho é a passagem do tempo e não um
    evento datável.
    """
    momentos: dict[str, datetime] = {}

    def registrar(estagio: str, momento: datetime | None) -> None:
        if momento is not None:
            momentos[estagio] = momento

    registrar("S1", sinais.primeiro_inbound)
    registrar("S2", db.fato_registrado_em(conversa.id, "foto_pet_recebida"))
    registrar("S3", db.fato_registrado_em(conversa.id, "previa_enviada"))
    registrar("S4", db.fato_registrado_em(conversa.id, "preco_apresentado"))
    # S5 é a resposta ao preço, então o momento é o da última fala do cliente.
    registrar("S5", sinais.ultimo_inbound)
    registrar("S6", db.marco_em(conversa.id, "ganho"))

    registrar("P1", sinais.primeiro_outbound)
    registrar("P2", db.fato_registrado_em(conversa.id, "autorizou_envio_material"))
    registrar("P3", sinais.proposta_em)
    registrar("P4", db.fato_registrado_em(conversa.id, "visita_aceita"))
    registrar("P5", db.marco_em(conversa.id, "consignacao_assinada"))
    registrar("P6", db.marco_em(conversa.id, "primeira_reposicao"))

    return momentos


def _trilha_de_backfill(db, conversa, fatos, sinais):
    """Reconstrói o percurso inteiro, pulando o que já está registrado.

    Parte do estágio inicial do funil, e não do estágio atual da conversa: o
    backfill está reconstituindo história, não observando um avanço. Estágios
    já gravados são pulados, o que torna reexecutar o backfill seguro — §2
    vale aqui também, reprocessar não pode duplicar evento.
    """
    ja_registrados = db.estagios_registrados(conversa.id)
    estagio_atual = regras_estagio.estagio_inicial(conversa.funil)
    transicoes: list[regras_estagio.Transicao] = []

    for derivacao in regras_estagio.trilha(fatos, sinais):
        movimento = regras_estagio.transicao(
            estagio_atual, derivacao, origem=regras_estagio.ORIGEM_BACKFILL
        )
        if movimento is None:
            continue
        estagio_atual = movimento.para
        if movimento.para in ja_registrados:
            continue
        transicoes.append(movimento)
    return estagio_atual, transicoes


def recalcular_todas(
    db: Database, *, agora: datetime | None = None, limite: int = 1000
) -> list[EstadoConversa]:
    """Reprocessa as regras sobre toda a base aberta, sem tocar no LLM.

    Rodar isto é o que se faz depois de mudar um critério de estágio ou de
    temperatura. O resultado é reprodutível: mesmos fatos, mesmo relógio,
    mesmo resultado.
    """
    agora = agora or datetime.now(timezone.utc)
    return [
        recalcular(db, conversa, agora=agora)
        for conversa in db.listar_conversas_abertas(limite=limite)
    ]
