"""Fila de follow-up: a saída do sistema.

§6 do documento, literalmente: "A saída do sistema **não é um painel**. É uma
lista de no máximo 10 nomes por dia."

Prioridade é política de negócio, não inferência — mora aqui, determinística e
replayable. O LLM não participa desta decisão.

O teto de 2 follow-ups aparece aqui como filtro (FRIO com 1 follow-up não
entra), mas a **garantia** de que ele não é furado é o CHECK constraint em
`db.ensure_schema` — §6 é explícito que isso é constraint de banco, não
validação de aplicação.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..taxonomia import (
    ESFRIANDO,
    ESTAGIOS_CAROS_B2B,
    ESTAGIOS_CAROS_B2C,
    FILA_TAMANHO_MAXIMO,
    FRIO,
    QUENTE,
    BOLA_CAMU,
    estagio_label,
)
from .sinais import SinaisConversa
from .temperatura import Classificacao

# Ações, uma por prioridade. Texto fechado de propósito: a fila é lida com
# pressa, e a ação precisa ser a mesma frase todo dia.
ACAO_RESPONDER = "Responder agora — isso é dívida, não follow-up"
ACAO_LEAD_CARO = "Lead mais caro de perder: já mandou a foto"
ACAO_PETSHOP = "Petshop autorizou e não decidiu"
ACAO_UM_TOQUE = "Um único toque, depois encerra"


@dataclass(frozen=True)
class ItemFila:
    """Uma linha da fila do dia."""

    conversa_id: int
    nome: str
    funil: str
    estagio: str
    temperatura: str
    prioridade: int
    acao: str
    motivo: str
    horas_esperando: float

    def __str__(self) -> str:
        espera = _formatar_espera(self.horas_esperando)
        return (
            f"[{self.prioridade}] {self.nome} — {estagio_label(self.estagio)} "
            f"({self.estagio}), {self.temperatura.upper()}, {espera} — {self.acao}"
        )


@dataclass(frozen=True)
class Candidato:
    """Entrada de `montar_fila`: uma conversa já classificada.

    Agrupa o que o banco carrega (`conversa_id`, `nome`, `estagio`) com o que
    as regras derivaram (`classificacao`, `sinais`), para que a priorização
    seja uma função pura de dados já calculados.
    """

    conversa_id: int
    nome: str
    funil: str
    estagio: str
    classificacao: Classificacao
    sinais: SinaisConversa


def prioridade(candidato: Candidato) -> tuple[int, str, str] | None:
    """Prioridade 1..4 de um candidato, ou `None` se ele não entra na fila.

    A tabela de §6 é fechada: o que não está nela **não aparece**. Isso é
    deliberado — uma fila que cresce com "casos parecidos" deixa de ser uma
    lista de 10 nomes e vira um painel, que é o que §6 recusa.
    """
    temp = candidato.classificacao.temperatura
    sinais = candidato.sinais

    if temp == QUENTE and sinais.bola_com == BOLA_CAMU:
        return 1, ACAO_RESPONDER, candidato.classificacao.sinal

    if temp == ESFRIANDO and candidato.estagio in ESTAGIOS_CAROS_B2C:
        return 2, ACAO_LEAD_CARO, candidato.classificacao.sinal

    if temp == ESFRIANDO and candidato.estagio in ESTAGIOS_CAROS_B2B:
        return 3, ACAO_PETSHOP, candidato.classificacao.sinal

    if temp == FRIO and sinais.followups_enviados == 0:
        return 4, ACAO_UM_TOQUE, candidato.classificacao.sinal

    # FRIO com 1 follow-up: encerrado (§6, linha "—"). E qualquer combinação
    # fora da tabela também não entra.
    return None


def montar_fila(
    candidatos: list[Candidato], *, limite: int = FILA_TAMANHO_MAXIMO
) -> list[ItemFila]:
    """Monta a fila do dia: no máximo `limite` nomes, mais urgente primeiro.

    Dentro da mesma prioridade, quem espera há mais tempo vem antes — a
    ordenação secundária que evita que a dívida mais antiga fique perpetuamente
    no fim de uma prioridade cheia.

    O corte em `limite` é o ponto do sistema: o que sobra não é "adiado para a
    segunda página", é simplesmente não feito hoje. §0 já diz onde está o
    custo real — atenção diária —, e uma fila que não cabe no dia não é
    atendida, só parece atendida.
    """
    itens: list[ItemFila] = []
    for candidato in candidatos:
        resultado = prioridade(candidato)
        if resultado is None:
            continue
        nivel, acao, motivo = resultado
        itens.append(
            ItemFila(
                conversa_id=candidato.conversa_id,
                nome=candidato.nome,
                funil=candidato.funil,
                estagio=candidato.estagio,
                temperatura=candidato.classificacao.temperatura,
                prioridade=nivel,
                acao=acao,
                motivo=motivo,
                horas_esperando=_horas_esperando(candidato.sinais),
            )
        )

    itens.sort(key=lambda i: (i.prioridade, -i.horas_esperando, i.conversa_id))
    return itens[:limite]


def _horas_esperando(sinais: SinaisConversa) -> float:
    """Há quanto tempo esta conversa espera uma ação nossa."""
    horas = sinais.horas_desde_inbound
    if horas is not None:
        return horas
    dias = sinais.dias_sem_resposta
    return 0.0 if dias is None else dias * 24


def _formatar_espera(horas: float) -> str:
    if horas < 1:
        return f"{int(horas * 60)}min"
    if horas < 48:
        return f"{horas:.0f}h"
    return f"{horas / 24:.0f}d"


def formatar_fila(itens: list[ItemFila], *, data: str | None = None) -> str:
    """Renderiza a fila para leitura rápida no terminal ou no WhatsApp."""
    if not itens:
        return "Fila vazia — nada a fazer hoje."
    cabecalho = f"Fila de follow-up{f' — {data}' if data else ''} ({len(itens)})"
    linhas = [cabecalho, "=" * len(cabecalho)]
    linhas.extend(f"{i + 1}. {item}" for i, item in enumerate(itens))
    return "\n".join(linhas)
