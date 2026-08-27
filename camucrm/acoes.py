"""Ações humanas — o que um operador faz sobre uma conversa (§1, §3, §7).

Módulo de topo, não dentro de `painel/`, pelo mesmo motivo declarado em
`ingest.py`: a CLI (`cli.cmd_marcar`, `cli.cmd_tipo`) e o painel (drag-and-drop
no kanban) precisam da mesma sequência de efeitos — marco → resultado →
recalcular; tipo/funil → correção → reclassificação de estágio. Se cada
caminho reimplementasse a sequência, os dois divergiriam sem ninguém notar; se
o painel importasse `cli`, a UI passaria a depender de outra UI. Este módulo
existe para que os dois caminhos cheguem sempre ao mesmo estado final.

`acoes.py` só chama métodos de `Database` — nunca SQL cru (`db.py` continua o
único lugar com SQL do repo, CLAUDE.md).

**Correção real de comportamento, não só refatoração:** hoje
`db.registrar_marco` não valida se o marco combina com o funil da conversa —
`camucrm marcar 5 consignacao_assinada` numa conversa B2C é aceito e o marco
fica órfão, sem nunca produzir P5/P6 porque a derivação (`rules.estagio.
_derive_b2b`) só olha esses marcos no funil B2B. `marco_permitido` fecha esse
buraco tanto na CLI quanto na API do painel.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .db import Database, MARCOS_MANUAIS
from .db import _normalizar_texto as _normalizar
from .pipeline import EstadoConversa, carregar_sinais, estagio_de_partida, recalcular
from .rules.estagio import Transicao, mudar_funil
from .taxonomia import B2B, B2C

# §10, change `rascunho-registrado`: caminho 2 de vínculo (reconciliação
# pelo eco da Evolution). `_normalizar` é reexportado de `db._normalizar_texto`
# — não uma segunda definição — para as duas pontas da comparação (o texto
# do eco e o texto gravado em `rascunhos`) nunca divergirem por acidente.
ENV_RECONCILIAR_RASCUNHO = "CAMU_RECONCILIAR_RASCUNHO"

# design.md: janela de 48h — rascunho gerado há mais tempo que isso não é
# mais candidato a ter sido "o que acabou de ser enviado".
JANELA_RECONCILIACAO_HORAS = 48

# Marcos que só fazem sentido no funil B2B — §3: "consignação assinada" e
# "primeira reposição" são conceitos de petshop revendedor, sem equivalente
# no funil B2C (DM). `ganho`/`perdido` são universais: qualquer conversa pode
# fechar ou morrer, nos dois funis.
_MARCOS_SO_B2B = {"consignacao_assinada", "primeira_reposicao"}


class AcaoInvalidaError(ValueError):
    """Ação recusada antes de tocar no banco — nada foi gravado."""


class MarcoNaoPermitidoError(AcaoInvalidaError):
    """Marco incompatível com o funil da conversa (§3).

    Carrega `regra` para as rotas do painel devolverem
    `{"erro": str(exc), "regra": exc.regra}` sem reformular a mensagem.
    """

    def __init__(self, motivo: str, *, regra: str = "§3") -> None:
        super().__init__(motivo)
        self.regra = regra


@dataclass(frozen=True)
class ResultadoMarco:
    """O que `marcar_marco` produziu — o suficiente para CLI e painel
    informarem o operador sem reconsultar o banco."""

    conversa_id: int
    marco: str
    por: str | None
    estado: EstadoConversa


@dataclass(frozen=True)
class ResultadoFunil:
    """O que `mudar_funil_conversa` produziu."""

    conversa_id: int
    anterior: str
    novo: str
    movimento: Transicao | None


def marco_permitido(funil: str, marco: str) -> str | None:
    """Recusa marco incompatível com o funil. Função pura — sem I/O.

    Devolve o motivo da recusa (citando §3), ou `None` se o marco é válido
    para o funil dado. `ganho` e `perdido` valem nos dois funis;
    `consignacao_assinada` e `primeira_reposicao` só fazem sentido em B2B.
    """
    if marco not in MARCOS_MANUAIS:
        return f"§3: marco desconhecido {marco!r} (use {MARCOS_MANUAIS})"
    if marco in _MARCOS_SO_B2B and funil != B2B:
        return (
            f"§3: marco '{marco}' só é válido no funil B2B; "
            f"esta conversa é {funil.upper()}"
        )
    return None


def marcar_marco(
    db: Database, conversa_id: int, marco: str, *, por: str | None
) -> ResultadoMarco:
    """Marca marco → resultado (ganho/perdido) → recalcula estágio.

    Extraído de `cli.cmd_marcar`. Divergência corrigida em relação ao
    comportamento anterior: a conversa é confirmada e o marco validado
    (`marco_permitido`) *antes* de qualquer escrita — a versão antiga
    chamava `db.registrar_marco` antes mesmo de checar se a conversa
    existia, e não validava o marco contra o funil.
    """
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        raise AcaoInvalidaError(f"conversa {conversa_id} não existe")

    motivo = marco_permitido(conversa.funil, marco)
    if motivo:
        raise MarcoNaoPermitidoError(motivo)

    db.registrar_marco(conversa_id, marco, por=por)
    if marco == "perdido":
        db.atualizar_estado_conversa(conversa_id, resultado="perdido")
    elif marco == "ganho":
        db.atualizar_estado_conversa(conversa_id, resultado="ganho")

    estado = recalcular(db, conversa)
    return ResultadoMarco(conversa_id=conversa_id, marco=marco, por=por, estado=estado)


def mudar_funil_conversa(
    db: Database, conversa_id: int, novo_funil: str, *, por: str | None
) -> ResultadoFunil:
    """Muda o tipo do contato/funil da conversa, sempre com correção gravada.

    Extraído de `cli.cmd_tipo`. §7: "correção que só ajusta a tela e não é
    gravada é informação jogada fora" — por isso `db.registrar_correcao` é
    chamado sempre que o funil de fato muda, incluindo quando `mudar_funil`
    (§3) não produz nenhuma transição de estágio (a reclassificação sozinha
    já é a correção que importa registrar).

    Change `estagio-reabertura-manual-e-relogio`: o estágio de partida vem de
    `pipeline.estagio_de_partida` (reconciliado contra `eventos_estagio`), não
    de `conversas.estagio` cru — mesma reconciliação que
    `pipeline.recalcular`/`_avanco_ao_vivo` já fazem. Sem isto, um cache
    desalinhado (a regressão de watermark que `literalidade-e-idempotencia-
    da-extracao` corrigiu é um jeito real de chegar lá) gravaria um `de`
    errado no evento desta reclassificação.
    """
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        raise AcaoInvalidaError(f"conversa {conversa_id} não existe")
    if novo_funil not in (B2B, B2C):
        raise AcaoInvalidaError(f"funil inválido: {novo_funil!r}")

    anterior = conversa.funil
    if anterior == novo_funil:
        return ResultadoFunil(conversa_id, anterior, novo_funil, movimento=None)

    db.set_tipo_contato(conversa.contato_id, novo_funil)
    db.set_funil_conversa(conversa_id, novo_funil)
    # A reclassificação é uma correção (§7): o padrão delas mostra o que o
    # sistema não está vendo na hora de classificar.
    db.registrar_correcao(conversa_id, "funil", anterior, novo_funil, por=por)

    atualizada = db.get_conversa(conversa_id)
    assert atualizada is not None
    fatos = db.fatos_da_conversa(conversa_id)
    sinais = carregar_sinais(db, atualizada)
    estagio_atual = estagio_de_partida(db, atualizada)
    movimento = mudar_funil(estagio_atual, fatos, sinais)
    if movimento:
        db.gravar_evento_estagio(
            conversa_id,
            movimento.de,
            movimento.para,
            motivo=movimento.motivo,
            causada_por=movimento.causada_por,
        )
        db.atualizar_estado_conversa(conversa_id, estagio=movimento.para)

    return ResultadoFunil(conversa_id, anterior, novo_funil, movimento=movimento)


def desconsiderar_recusa(
    db: Database, conversa_id: int, *, por: str | None
) -> EstadoConversa:
    """Desconsidera um `recusa_explicita=true` tratado como falso positivo
    de extração (design.md, change `estagio-reabertura-manual-e-relogio`).

    Exceção explícita e auditada ao invariante #2/§3 do CLAUDE.md ("estágio
    nunca regride") — não uma violação silenciosa: o fato `recusa_explicita`
    em `fatos` continua intacto, só a INTERPRETAÇÃO dele pela regra de
    estágio muda a partir daqui (`db.registrar_desconsideracao_recusa`, que
    grava em `correcoes`, nunca reescreve `fatos`).

    `por` é exigido (nunca ação anônima) ANTES de tocar no banco — mesmo
    padrão de `marcar_marco`/`mudar_funil_conversa`: recusado com
    `AcaoInvalidaError`, nada é gravado.

    Recalcula a conversa em seguida: se a bola já estiver com a Camu (o
    cliente falou por último — o que costuma já ser o caso, já que a própria
    recusa costuma ter sido a última mensagem dele), a conversa reabre no
    mesmo instante, no maior estágio já alcançado (nunca S1/P0, mesmo padrão
    de `rules.estagio.reabrir`). Sem uma mensagem nova do cliente ainda, a
    reabertura acontece na próxima vez que ele responder.
    """
    if not por or not por.strip():
        raise AcaoInvalidaError(
            "desconsiderar recusa exige identificação de quem decidiu (--por)"
        )
    conversa = db.get_conversa(conversa_id)
    if conversa is None:
        raise AcaoInvalidaError(f"conversa {conversa_id} não existe")

    fatos = db.fatos_da_conversa(conversa_id)
    if not fatos.get("recusa_explicita"):
        raise AcaoInvalidaError(
            f"conversa {conversa_id} não tem recusa_explicita registrada — "
            "nada a desconsiderar"
        )

    db.registrar_desconsideracao_recusa(conversa_id, por=por)
    return recalcular(db, conversa)


def reconciliar_rascunho(
    db: Database, conversa_id: int, mensagem_id: int, texto: str
) -> int | None:
    """Caminho 2 de vínculo rascunho -> mensagem (design.md, `rascunho-registrado`).

    Chamado por `ingest.ingerir` sempre que uma mensagem `out` NOVA (não
    duplicata) é gravada. Procura um rascunho pendente (`mensagem_id IS
    NULL`) da mesma conversa, dentro de `JANELA_RECONCILIACAO_HORAS`, cujo
    texto normalizado (`_normalizar`) bate EXATAMENTE com o texto recebido.

    Sem fuzzy matching, sem LLM: se o operador editou o texto antes de
    enviar, o casamento falha e a função devolve `None` — o rascunho fica
    sem vínculo automático. Isso é o comportamento correto, não uma lacuna:
    um vínculo errado envenena em silêncio a análise agregada que a tabela
    existe para sustentar (requirement "Reconciliação pelo eco não usa
    casamento aproximado").

    Desligável por `CAMU_RECONCILIAR_RASCUNHO=false` (qualquer outro valor,
    inclusive ausente, mantém a reconciliação ligada).
    """
    if os.getenv(ENV_RECONCILIAR_RASCUNHO, "true").strip().lower() == "false":
        return None
    if not _normalizar(texto):
        return None
    rascunho_id = db.rascunho_pendente_por_texto(
        conversa_id, texto, janela_horas=JANELA_RECONCILIACAO_HORAS
    )
    if rascunho_id is None:
        return None
    db.vincular_rascunho(rascunho_id, mensagem_id)
    return rascunho_id
