"""As métricas que justificam o sistema (§14).

`S1→S2` e `S4→S6` no B2C, `P5→P6` no B2B. §14 é explícito: "Se em 30 dias o
sistema não tiver produzido esses três números, ele virou INFRA que se sustenta
sozinha".

A separação que §8 exige está codificada aqui:

- **Conversão** (quantos chegaram a cada estágio) usa todos os eventos,
  inclusive backfill.
- **Tempo por estágio** usa apenas `origem = 'live'`. Um evento de backfill
  carrega o momento em que o backfill rodou, não o da transição — incluí-lo
  não daria um número impreciso, daria um número inventado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .db import Database
from .rules.estagio import ORIGEM_LIVE
from .taxonomia import (
    OUTRO_LIMITE_INFERIOR,
    OUTRO_LIMITE_SUPERIOR,
    OBJECAO_OUTRO,
    estagio_label,
)

# §14. A ordem é a de leitura: o que a abordagem produz, o que o preço e o
# frete custam, e o que o produto realmente vende.
METRICAS_CHAVE = (("S1", "S2"), ("S4", "S6"), ("P5", "P6"))


@dataclass(frozen=True)
class Conversao:
    de: str
    para: str
    alcancaram_de: int
    alcancaram_para: int

    @property
    def taxa(self) -> float | None:
        """`None` quando ninguém chegou em `de` — não é 0%, é sem amostra."""
        if self.alcancaram_de == 0:
            return None
        return self.alcancaram_para / self.alcancaram_de

    def __str__(self) -> str:
        if self.taxa is None:
            return f"{self.de}→{self.para}: sem amostra"
        return (
            f"{self.de}→{self.para}: {self.taxa:.0%} "
            f"({self.alcancaram_para}/{self.alcancaram_de}) — "
            f"{estagio_label(self.de)} para {estagio_label(self.para)}"
        )


def conversao(
    db: Database, de: str, para: str, *, desde: datetime | None = None
) -> Conversao:
    """Quantas conversas que chegaram a `de` também chegaram a `para`.

    Conta por conversa e por estágio *alcançado*, não pelo estágio atual: uma
    conversa hoje em S5 chegou em S2 no caminho, e ignorá-la subestimaria a
    conversão de quem avançou rápido.
    """
    filtro = "AND em >= %s" if desde else ""
    args_de = (de, desde) if desde else (de,)
    args_para = (de, para, desde) if desde else (de, para)

    with db._conn() as conn:  # noqa: SLF001 - módulo de leitura do próprio pacote
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(DISTINCT conversa_id) FROM eventos_estagio "
                f"WHERE para = %s {filtro}",
                args_de,
            )
            alcancaram_de = cur.fetchone()[0] or 0
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT a.conversa_id)
                  FROM eventos_estagio a
                  JOIN eventos_estagio b ON b.conversa_id = a.conversa_id
                 WHERE a.para = %s AND b.para = %s {filtro.replace('em', 'b.em')}
                """,
                args_para,
            )
            alcancaram_para = cur.fetchone()[0] or 0
    return Conversao(de, para, alcancaram_de, alcancaram_para)


def metricas_chave(db: Database, *, desde: datetime | None = None) -> list[Conversao]:
    """Os três números da §14, na ordem."""
    return [conversao(db, de, para, desde=desde) for de, para in METRICAS_CHAVE]


@dataclass(frozen=True)
class TempoNoEstagio:
    estagio: str
    conversas: int
    horas_medianas: float | None

    def __str__(self) -> str:
        if self.horas_medianas is None:
            return f"{self.estagio}: sem dado ao vivo"
        return (
            f"{self.estagio} ({estagio_label(self.estagio)}): "
            f"mediana {self.horas_medianas:.0f}h, n={self.conversas}"
        )


def tempo_por_estagio(db: Database) -> list[TempoNoEstagio]:
    """Quanto tempo as conversas passam em cada estágio — só eventos ao vivo.

    §8: eventos de backfill ficam de fora. O filtro está no SQL de propósito,
    e não numa checagem opcional em Python — a exclusão precisa ser difícil de
    esquecer, porque esquecê-la produz um número plausível e errado.
    """
    with db._conn() as conn:  # noqa: SLF001
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH transicoes AS (
                    SELECT conversa_id, para AS estagio, em,
                           LEAD(em) OVER (PARTITION BY conversa_id ORDER BY em) AS proximo
                      FROM eventos_estagio
                     WHERE origem = '{ORIGEM_LIVE}'
                )
                SELECT estagio,
                       COUNT(*),
                       PERCENTILE_CONT(0.5) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM (proximo - em)) / 3600.0
                       )
                  FROM transicoes
                 WHERE proximo IS NOT NULL
                 GROUP BY estagio
                 ORDER BY estagio
                """
            )
            return [
                TempoNoEstagio(estagio, n, float(mediana) if mediana is not None else None)
                for estagio, n, mediana in cur.fetchall()
            ]


@dataclass(frozen=True)
class SaudeTaxonomia:
    """Revisão mensal da §4: a proporção de `outro` diz se a taxonomia serve."""

    total: int
    outros: int
    distribuicao: dict[str, int]

    @property
    def proporcao_outro(self) -> float:
        return self.outros / self.total if self.total else 0.0

    @property
    def veredito(self) -> str:
        if self.total == 0:
            return "sem objeções registradas ainda"
        proporcao = self.proporcao_outro
        if proporcao > OUTRO_LIMITE_SUPERIOR:
            return (
                f"`outro` em {proporcao:.0%} (>{OUTRO_LIMITE_SUPERIOR:.0%}): "
                "falta uma categoria — a taxonomia está errada, não o modelo"
            )
        if proporcao < OUTRO_LIMITE_INFERIOR:
            return (
                f"`outro` em {proporcao:.0%} (<{OUTRO_LIMITE_INFERIOR:.0%}): "
                "provavelmente o modelo está forçando encaixe — conferir na amostra"
            )
        return f"`outro` em {proporcao:.0%}: taxonomia saudável"


def saude_taxonomia(db: Database, *, desde: datetime | None = None) -> SaudeTaxonomia:
    distribuicao = db.distribuicao_objecoes(desde)
    total = sum(distribuicao.values())
    return SaudeTaxonomia(total, distribuicao.get(OBJECAO_OUTRO, 0), distribuicao)


def relatorio(db: Database, *, desde: datetime | None = None) -> str:
    """Relatório de texto com os três números da §14 e a saúde da taxonomia."""
    linhas = ["MÉTRICAS QUE JUSTIFICAM O SISTEMA (§14)", "=" * 40]
    linhas.extend(f"  {c}" for c in metricas_chave(db, desde=desde))
    linhas.append("")
    linhas.append("TEMPO POR ESTÁGIO (apenas eventos ao vivo — §8)")
    tempos = tempo_por_estagio(db)
    linhas.extend(f"  {t}" for t in tempos) if tempos else linhas.append(
        "  sem transições ao vivo ainda"
    )
    linhas.append("")
    saude = saude_taxonomia(db, desde=desde)
    linhas.append("TAXONOMIA DE OBJEÇÕES (§4)")
    linhas.append(f"  {saude.veredito}")
    for categoria, quantidade in sorted(
        saude.distribuicao.items(), key=lambda kv: -kv[1]
    ):
        linhas.append(f"    {categoria}: {quantidade}")
    return "\n".join(linhas)
