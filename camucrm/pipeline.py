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

from .db import Conversa, ContextoConversa, Database
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
        avancou_causada_por=db.ultimo_avanco_causada_por(conversa.id),
        estagio_maximo_alcancado=db.estagio_maximo_alcancado(conversa.id),
        ganho="ganho" in marcos,
        consignacao_assinada="consignacao_assinada" in marcos,
        primeira_reposicao="primeira_reposicao" in marcos,
        recusa_desconsiderada=db.recusa_desconsiderada(conversa.id),
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
            avancou_causada_por=transicoes[-1].causada_por,
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
                causada_por=movimento.causada_por,
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


def estagio_de_partida(db: Database, conversa: Conversa) -> str:
    """De onde a derivação parte: o histórico, não o cache.

    `conversas.estagio` é conveniência para a fila não reprocessar tudo a cada
    consulta. `eventos_estagio` é o que aconteceu. Quando os dois divergem, o
    histórico ganha — é o que torna verdadeira a promessa de que o estado é
    recalculável, e o que permite consertar um estágio inflado apagando o
    evento que não devia existir.

    Público (não `_estagio_de_partida`) desde o change
    `estagio-reabertura-manual-e-relogio`: `acoes.mudar_funil_conversa`
    reconcilia contra o histórico do mesmo jeito, em vez de ler
    `conversas.estagio` cru — nenhuma segunda implementação da mesma regra.
    """
    return db.estagio_corrente(conversa.id) or conversa.estagio


def _avanco_ao_vivo(db, conversa, fatos, sinais):
    """O avanço observado — a trilha inteira, não só o estágio final.

    Parte do estágio que o histórico de eventos registra, não do cache em
    `conversas.estagio` (ver `estagio_de_partida`).

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
    estagio_atual = estagio_de_partida(db, conversa)
    transicoes: list[regras_estagio.Transicao] = []

    # Conversa encerrada por silêncio (ou por recusa já desconsiderada) que
    # voltou a falar reabre no maior estágio já alcançado — não vira lead
    # novo (§3, `reabrir`). A checagem "recusa não reabre sem desconsideração"
    # não precisa mais ser replicada aqui — `reabrir()` a faz sozinha (change
    # `estagio-reabertura-manual-e-relogio`, design.md).
    if is_terminal(estagio_atual) and sinais.bola_com == "camu":
        reabertura = regras_estagio.reabrir(
            estagio_atual,
            sinais.estagio_maximo_alcancado,
            recusa_explicita=bool(fatos.get("recusa_explicita")),
            recusa_desconsiderada=sinais.recusa_desconsiderada,
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
    backfill está reconstituindo história, não observando um avanço. Trilhas
    já gravadas são puladas, o que torna reexecutar o backfill seguro — §2
    vale aqui também, reprocessar não pode duplicar evento.

    Change `backfill-seguro-para-reexecucao`, §8: a checagem é pelo par
    `(de, para)` (`db.trilhas_registradas`), não só pelo destino. Pular por
    `para` sozinho descartaria uma transição legítima que chega ao mesmo
    estágio por uma origem diferente da já registrada — canto raro (funil
    trocado + backfill reexecutado), mas real o bastante para não silenciar.
    """
    ja_registrados = db.trilhas_registradas(conversa.id)
    estagio_atual = regras_estagio.estagio_inicial(conversa.funil)
    transicoes: list[regras_estagio.Transicao] = []

    for derivacao in regras_estagio.trilha(fatos, sinais):
        movimento = regras_estagio.transicao(
            estagio_atual, derivacao, origem=regras_estagio.ORIGEM_BACKFILL
        )
        if movimento is None:
            continue
        estagio_atual = movimento.para
        if (movimento.de, movimento.para) in ja_registrados:
            continue
        transicoes.append(movimento)
    return estagio_atual, transicoes


def recalcular_todas(
    db: Database, *, agora: datetime | None = None, limite: int = 1000
) -> list[EstadoConversa]:
    """Reprocessa as regras sobre toda a base aberta, sem tocar no LLM.

    Rodar isto é o que se faz depois de mudar um critério de estágio ou de
    temperatura. O resultado é reprodutível: mesmos fatos, mesmo relógio,
    mesmo resultado. Delega a `recalcular_lote` (otimização de 2026-08-28)
    — mesmo resultado de antes, com uma fração das idas ao banco.
    """
    agora = agora or datetime.now(timezone.utc)
    return recalcular_lote(db, db.listar_conversas_abertas(limite=limite), agora=agora)


class _ConversaCacheada:
    """Serve as leituras de UMA conversa a partir de um `ContextoConversa`
    já pré-carregado, delegando tudo o mais (inclusive toda escrita) ao
    `Database` real — ver o comentário de `Database.contexto_para_recalculo`
    para o porquê desta otimização existir.

    Existe para `recalcular_lote` poder chamar `recalcular` exatamente como
    `recalcular_todas`/o painel sempre chamaram, SEM duplicar a lógica de
    decisão de `carregar_sinais`/`_avanco_ao_vivo`/`momentos_de_estagio` —
    esse código continua fazendo `db.fato_registrado_em(...)`,
    `db.estagio_corrente(...)` etc. do jeito de sempre; só que aqui `db` é
    este objeto, que já tem a resposta em memória. Duplicar a lógica de
    decisão para um "caminho em lote" separado seria o tipo de divergência
    que este projeto trata como o erro mais caro que existe — um recálculo
    em lote que discordasse do recálculo de uma conversa só produziria
    exatamente a inconsistência que a garantia de `estagio_de_partida`
    (fonte de verdade = `eventos_estagio`, não cache) existe para evitar.

    `conversa_id` é fixo na criação e conferido em cada leitura — um objeto
    novo por conversa, nunca reaproveitado entre conversas diferentes, para
    que um erro de programação (passar o objeto errado) estoure na hora, não
    vire silenciosamente um estágio calculado com os fatos de outra conversa.
    """

    def __init__(self, db: Database, conversa_id: int, contexto: ContextoConversa):
        self._db = db
        self._id = conversa_id
        self._ctx = contexto

    def __getattr__(self, nome: str):
        return getattr(self._db, nome)

    def _conferir(self, conversa_id: int) -> None:
        if conversa_id != self._id:
            raise ValueError(
                f"_ConversaCacheada para conversa {self._id} usado com "
                f"conversa_id {conversa_id} — provável bug de programação"
            )

    def fatos_da_conversa(self, conversa_id: int) -> dict[str, bool]:
        self._conferir(conversa_id)
        return dict(self._ctx.fatos)

    def listar_mensagens(self, conversa_id: int):
        self._conferir(conversa_id)
        return list(self._ctx.mensagens)

    def fato_registrado_em(self, conversa_id: int, chave: str):
        self._conferir(conversa_id)
        return self._ctx.momentos_fatos.get(chave)

    def ultimo_followup_em(self, conversa_id: int):
        self._conferir(conversa_id)
        return self._ctx.ultimo_followup_em

    def ultimo_avanco_em(self, conversa_id: int):
        self._conferir(conversa_id)
        return self._ctx.ultimo_avanco_em

    def ultimo_avanco_causada_por(self, conversa_id: int):
        self._conferir(conversa_id)
        return self._ctx.ultimo_avanco_causada_por

    def estagio_maximo_alcancado(self, conversa_id: int):
        self._conferir(conversa_id)
        return self._ctx.estagio_maximo_alcancado

    def estagio_corrente(self, conversa_id: int):
        self._conferir(conversa_id)
        return self._ctx.estagio_corrente

    def marco_em(self, conversa_id: int, marco: str):
        self._conferir(conversa_id)
        return self._ctx.marcos.get(marco)

    def marcos_da_conversa(self, conversa_id: int, *, conn=None):
        self._conferir(conversa_id)
        return set(self._ctx.marcos.keys())

    def recusa_desconsiderada(self, conversa_id: int) -> bool:
        self._conferir(conversa_id)
        return self._ctx.recusa_desconsiderada


def recalcular_lote(
    db: Database,
    conversas: list[Conversa],
    *,
    agora: datetime | None = None,
    persistir: bool = True,
) -> list[EstadoConversa]:
    """`recalcular` para várias conversas, com o custo de rede de UMA.

    Pré-carrega tudo com `Database.contexto_para_recalculo` (seis consultas
    em lote, não uma por conversa por campo) e chama `recalcular` normal
    para cada conversa contra um `_ConversaCacheada` — mesma função, mesmo
    resultado, mesma ordem de decisão de sempre, só sem o round-trip de rede
    repetido. Ver `Database.contexto_para_recalculo` para a motivação
    completa (medido: 40-50s virou baixos segundos contra um banco remoto,
    com poucas conversas abertas).

    Usada por `recalcular_todas` (CLI `camucrm recalcular`, e por extensão
    `camucrm fila`, que chama `recalcular_lote` diretamente — era o mais
    lento dos dois na prática, por rodar com `persistir=True` por padrão) e
    por `camucrm/painel/api.py::_carregar_candidatos`/`_carregar_fechadas`
    (fila, kanban, aba Conversas — as rotas que motivaram a otimização).
    """
    agora = agora or datetime.now(timezone.utc)
    contexto_por_id = db.contexto_para_recalculo([c.id for c in conversas])
    resultados = []
    for conversa in conversas:
        cache = _ConversaCacheada(db, conversa.id, contexto_por_id[conversa.id])
        resultados.append(recalcular(cache, conversa, agora=agora, persistir=persistir))
    return resultados
