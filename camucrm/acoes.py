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

from dataclasses import dataclass

from .db import Database, MARCOS_MANUAIS
from .pipeline import EstadoConversa, carregar_sinais, recalcular
from .rules.estagio import Transicao, mudar_funil
from .taxonomia import B2B, B2C

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
    movimento = mudar_funil(atualizada.estagio, fatos, sinais)
    if movimento:
        db.gravar_evento_estagio(
            conversa_id, movimento.de, movimento.para, motivo=movimento.motivo
        )
        db.atualizar_estado_conversa(conversa_id, estagio=movimento.para)

    return ResultadoFunil(conversa_id, anterior, novo_funil, movimento=movimento)
