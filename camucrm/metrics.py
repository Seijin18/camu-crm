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

Change `analise-desempenho` (§7, §13, §14) acrescenta as consultas de "o que
está funcionando": conversão de todo par adjacente, onde as conversas morrem,
objeção por estágio, padrão de correções, retorno por follow-up e o A/B
natural de rascunho. Nenhuma delas é usada por `rules/` nem por `pipeline.py`
— é leitura estritamente posterior, a mesma garantia de "folha" (CLAUDE.md/§1)
que já vale para o resto deste módulo.

**Honestidade sobre amostra (§7, mesmo espírito da linha de tendência que a
seção condena):** toda porcentagem aqui devolve `n` junto — o cálculo nunca
esconde o dado, mesmo quando `n` é baixo demais para confiar nele. Quem
decide **exibir** "sem amostra" é a camada de apresentação (`painel/views.py`
e `static/`), comparando `n` com `AMOSTRA_MINIMA`. Este módulo só constata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .db import Database, _condicao_teste
from .rules.estagio import ORIGEM_LIVE
from .taxonomia import (
    ESTAGIOS_POR_FUNIL,
    FUNIS,
    OUTRO_LIMITE_INFERIOR,
    OUTRO_LIMITE_SUPERIOR,
    OBJECAO_OUTRO,
    estagio_label,
    is_terminal,
    rank_estagio,
)

# Change `analise-desempenho`: constante única. Nenhuma porcentagem desta
# tela é exibida com `n < AMOSTRA_MINIMA` — a supressão é decisão da
# apresentação (ver docstring do módulo), não deste cálculo.
AMOSTRA_MINIMA = 10

# Plano ("O que está funcionando"): o bloco de rascunhos nasce bloqueado até
# acumular este tanto de ENVIOS VINCULADOS (não de rascunhos gerados) — um
# limiar mais alto que `AMOSTRA_MINIMA` de propósito, porque cada linha do
# A/B carrega três perguntas (opção/edição/avanço), não uma.
LIMIAR_RASCUNHOS_VINCULADOS = 30


def amostra_suficiente(n: int) -> bool:
    """Único ponto de comparação com `AMOSTRA_MINIMA` — evita `n < 10`
    espalhado e divergente entre `views.py` e o front-end."""
    return n >= AMOSTRA_MINIMA

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
    db: Database,
    de: str,
    para: str,
    *,
    desde: datetime | None = None,
    incluir_teste: bool = False,
    apenas_teste: bool = False,
) -> Conversao:
    """Quantas conversas que chegaram a `de` também chegaram a `para`.

    Conta por conversa e por estágio *alcançado*, não pelo estágio atual: uma
    conversa hoje em S5 chegou em S2 no caminho, e ignorá-la subestimaria a
    conversão de quem avançou rápido.

    Change `contatos-de-teste-isolados`: exclui contato de teste por padrão
    — junta até `contatos` (via `conversas`) para aplicar `_condicao_teste`,
    já que `eventos_estagio` só carrega `conversa_id`.
    """
    condicao = _condicao_teste("ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste)
    filtro = "AND ee.em >= %s" if desde else ""
    filtro_b = "AND b.em >= %s" if desde else ""
    args_de = (de, desde) if desde else (de,)
    args_para = (de, para, desde) if desde else (de, para)

    with db._conn() as conn:  # noqa: SLF001 - módulo de leitura do próprio pacote
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT ee.conversa_id)
                  FROM eventos_estagio ee
                  JOIN conversas c ON c.id = ee.conversa_id
                  JOIN contatos ct ON ct.id = c.contato_id
                 WHERE ee.para = %s {filtro} {condicao}
                """,
                args_de,
            )
            alcancaram_de = cur.fetchone()[0] or 0
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT a.conversa_id)
                  FROM eventos_estagio a
                  JOIN eventos_estagio b ON b.conversa_id = a.conversa_id
                  JOIN conversas c ON c.id = a.conversa_id
                  JOIN contatos ct ON ct.id = c.contato_id
                 WHERE a.para = %s AND b.para = %s {filtro_b} {condicao}
                """,
                args_para,
            )
            alcancaram_para = cur.fetchone()[0] or 0
    return Conversao(de, para, alcancaram_de, alcancaram_para)


def metricas_chave(
    db: Database,
    *,
    desde: datetime | None = None,
    incluir_teste: bool = False,
    apenas_teste: bool = False,
) -> list[Conversao]:
    """Os três números da §14, na ordem."""
    return [
        conversao(db, de, para, desde=desde, incluir_teste=incluir_teste, apenas_teste=apenas_teste)
        for de, para in METRICAS_CHAVE
    ]


def conversao_adjacente(
    db: Database,
    funil: str,
    *,
    desde: datetime | None = None,
    incluir_teste: bool = False,
    apenas_teste: bool = False,
) -> list[Conversao]:
    """Conversão de TODO par adjacente do funil, não só os três da §14
    (change `analise-desempenho`) — S0→S1, S1→S2, ..., S5→S6 no B2C (e o
    equivalente P0..P6 no B2B), na ordem do funil.
    """
    if funil not in FUNIS:
        raise ValueError(f"funil inválido: {funil!r} (use {FUNIS})")
    estagios = ESTAGIOS_POR_FUNIL[funil]
    return [
        conversao(
            db, de, para, desde=desde, incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        for de, para in zip(estagios, estagios[1:])
    ]


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


def tempo_por_estagio(
    db: Database, *, incluir_teste: bool = False, apenas_teste: bool = False
) -> list[TempoNoEstagio]:
    """Quanto tempo as conversas passam em cada estágio — só eventos ao vivo.

    §8: eventos de backfill ficam de fora. O filtro está no SQL de propósito,
    e não numa checagem opcional em Python — a exclusão precisa ser difícil de
    esquecer, porque esquecê-la produz um número plausível e errado.

    Change `contatos-de-teste-isolados`: mesma disciplina para contato de
    teste — exclui por padrão, junto ao filtro de origem, dentro do próprio
    CTE (`_condicao_teste`).
    """
    condicao = _condicao_teste("ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste)
    with db._conn() as conn:  # noqa: SLF001
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH transicoes AS (
                    SELECT ee.conversa_id, ee.para AS estagio, ee.em,
                           LEAD(ee.em) OVER (PARTITION BY ee.conversa_id ORDER BY ee.em) AS proximo
                      FROM eventos_estagio ee
                      JOIN conversas c ON c.id = ee.conversa_id
                      JOIN contatos ct ON ct.id = c.contato_id
                     WHERE ee.origem = '{ORIGEM_LIVE}' {condicao}
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
class OndeConversasMorrem:
    """Distribuição do maior estágio alcançado, entre conversas ENCERRADAS
    (`resultado IS NOT NULL`) — change `analise-desempenho`.

    Plano: "o número mais acionável no dia um, e `metrics.py` não o tem".
    `distribuicao` é `estagio -> contagem`; `n` é o total de conversas
    encerradas com pelo menos um estágio não-terminal identificável (uma
    conversa encerrada sem nenhum evento de estágio — não deveria existir,
    mas não é contada aqui em vez de inflar um denominador incerto).
    """

    distribuicao: dict[str, int]
    n: int

    def __str__(self) -> str:
        if self.n == 0:
            return "onde as conversas morrem: sem amostra"
        partes = ", ".join(
            f"{estagio_label(e)} ({e}): {c}"
            for e, c in sorted(self.distribuicao.items(), key=lambda kv: -kv[1])
        )
        return f"onde as conversas morrem (n={self.n}): {partes}"


def onde_morrem(
    db: Database, *, incluir_teste: bool = False, apenas_teste: bool = False
) -> OndeConversasMorrem:
    """Maior rank de estágio alcançado, entre conversas encerradas.

    A ordenação por rank é feita aqui, em Python, com `taxonomia.
    rank_estagio` — mesma regra de `db.estagio_maximo_alcancado`, não
    duplicada em SQL (`db.estagios_de_conversas_encerradas` só devolve os
    eventos crus).

    Change `contatos-de-teste-isolados`: exclui contato de teste por padrão.
    """
    por_conversa = db.estagios_de_conversas_encerradas(
        incluir_teste=incluir_teste, apenas_teste=apenas_teste
    )
    distribuicao: dict[str, int] = {}
    for estagios in por_conversa.values():
        nao_terminais = [e for e in estagios if not is_terminal(e)]
        if not nao_terminais:
            continue
        maximo = max(nao_terminais, key=rank_estagio)
        distribuicao[maximo] = distribuicao.get(maximo, 0) + 1
    return OndeConversasMorrem(distribuicao, sum(distribuicao.values()))


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


def saude_taxonomia(
    db: Database,
    *,
    desde: datetime | None = None,
    incluir_teste: bool = False,
    apenas_teste: bool = False,
) -> SaudeTaxonomia:
    """Change `contatos-de-teste-isolados`: exclui contato de teste por
    padrão (propagado a `db.distribuicao_objecoes`)."""
    distribuicao = db.distribuicao_objecoes(
        desde, incluir_teste=incluir_teste, apenas_teste=apenas_teste
    )
    total = sum(distribuicao.values())
    return SaudeTaxonomia(total, distribuicao.get(OBJECAO_OUTRO, 0), distribuicao)


@dataclass(frozen=True)
class ObjecaoPorEstagio:
    """`objecoes.estagio` existe desde o início e `distribuicao_objecoes` o
    descartava — change `analise-desempenho` preserva o cruzamento.

    `contagem` é `(estagio, categoria) -> n` (`estagio` pode ser `None`
    quando a objeção foi gravada sem estágio conhecido). `n` é o total de
    objeções no período, para a apresentação decidir amostra por célula ou
    no agregado, conforme o corte que a tela precisar.
    """

    contagem: dict[tuple[str | None, str], int]
    n: int


def objecao_por_estagio(
    db: Database,
    *,
    desde: datetime | None = None,
    incluir_teste: bool = False,
    apenas_teste: bool = False,
) -> ObjecaoPorEstagio:
    """Change `contatos-de-teste-isolados`: exclui contato de teste por
    padrão (propagado a `db.distribuicao_objecoes_por_estagio`)."""
    contagem = db.distribuicao_objecoes_por_estagio(
        desde, incluir_teste=incluir_teste, apenas_teste=apenas_teste
    )
    return ObjecaoPorEstagio(contagem, sum(contagem.values()))


@dataclass(frozen=True)
class PadraoCorrecao:
    """Uma linha do padrão de correções: quantas vezes `campo` foi corrigido
    de `antes` para `depois` (change `analise-desempenho`).

    Plano: "'funil corrigido 9× de b2c para b2b' diz que a classificação B2B
    falha na ingestão" — o padrão aponta o defeito, a correção isolada não.
    """

    campo: str
    antes: str | None
    depois: str | None
    n: int


def padrao_correcoes(
    db: Database,
    *,
    desde: datetime | None = None,
    incluir_teste: bool = False,
    apenas_teste: bool = False,
) -> list[PadraoCorrecao]:
    """Change `contatos-de-teste-isolados`: exclui contato de teste por
    padrão (propagado a `db.padrao_correcoes`)."""
    return [
        PadraoCorrecao(campo, antes, depois, n)
        for campo, antes, depois, n in db.padrao_correcoes(
            desde, incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
    ]


@dataclass(frozen=True)
class RetornoFollowup:
    """Retorno (qualquer mensagem inbound depois) por número de follow-up
    (1º ou 2º toque) — change `analise-desempenho`.

    Plano: "responde se o 2º toque funciona alguma vez — decide se o teto
    devia ser 1".
    """

    numero: int
    n: int
    com_retorno: int

    @property
    def taxa(self) -> float | None:
        return self.com_retorno / self.n if self.n else None


def retorno_por_followup(
    db: Database, *, incluir_teste: bool = False, apenas_teste: bool = False
) -> list[RetornoFollowup]:
    """Change `contatos-de-teste-isolados`: exclui contato de teste por
    padrão (propagado a `db.retorno_por_numero_followup`)."""
    dados = db.retorno_por_numero_followup(
        incluir_teste=incluir_teste, apenas_teste=apenas_teste
    )
    return [
        RetornoFollowup(numero, total, com_retorno)
        for numero, (total, com_retorno) in sorted(dados.items())
    ]


@dataclass(frozen=True)
class AbRascunhos:
    """A/B natural de rascunho (§10) — só respondível depois que
    `rascunhos` acumular envios vinculados (change `analise-desempenho`,
    bloqueado por `rascunho-registrado`).

    Nasce bloqueado: `amostra_suficiente` compara `n_vinculados` com
    `LIMIAR_RASCUNHOS_VINCULADOS` (30), não com `AMOSTRA_MINIMA` (10) — cada
    linha aqui sustenta três perguntas (opção/edição/avanço), não uma, e o
    plano pede um limiar mais alto de propósito.
    """

    n_vinculados: int
    escolha_1: int
    escolha_2: int
    escreveu_do_zero: int
    editado: int
    sem_edicao: int
    avancou_72h: int
    n_avaliavel_avanco: int

    @property
    def amostra_suficiente(self) -> bool:
        return self.n_vinculados >= LIMIAR_RASCUNHOS_VINCULADOS

    @property
    def proporcao_opcao_1(self) -> float | None:
        """Viés de posição: opção 1 vs opção 2, entre quem escolheu uma das
        duas (exclui quem escreveu do zero)."""
        total = self.escolha_1 + self.escolha_2
        return self.escolha_1 / total if total else None

    @property
    def proporcao_editado(self) -> float | None:
        """Aceito sem edição vs editado, entre quem escolheu uma opção
        (`texto_final` preenchido junto com `escolhida` é edição — não
        recomparamos texto aqui, ver `db.rascunhos_vinculados_para_analise`)."""
        total = self.editado + self.sem_edicao
        return self.editado / total if total else None

    @property
    def taxa_avanco_72h(self) -> float | None:
        return self.avancou_72h / self.n_avaliavel_avanco if self.n_avaliavel_avanco else None


def ab_rascunhos(
    db: Database, *, incluir_teste: bool = False, apenas_teste: bool = False
) -> AbRascunhos:
    """Compõe `db.rascunhos_vinculados_para_analise` — a comparação de rank
    (avançou ou não) é regra de domínio e mora aqui, não em SQL.

    Change `contatos-de-teste-isolados`: exclui contato de teste por padrão.
    """
    linhas = db.rascunhos_vinculados_para_analise(
        incluir_teste=incluir_teste, apenas_teste=apenas_teste
    )
    escolha_1 = escolha_2 = escreveu_do_zero = 0
    editado = sem_edicao = 0
    avancou_72h = 0
    n_avaliavel_avanco = 0

    for linha in linhas:
        if linha.escolhida == 1:
            escolha_1 += 1
        elif linha.escolhida == 2:
            escolha_2 += 1
        else:
            escreveu_do_zero += 1

        if linha.escolhida is not None:
            if linha.editado:
                editado += 1
            else:
                sem_edicao += 1

        if linha.estagio_no_envio is not None:
            n_avaliavel_avanco += 1
            rank_partida = rank_estagio(linha.estagio_no_envio)
            if any(
                not is_terminal(e) and rank_estagio(e) > rank_partida
                for e in linha.estagios_72h
            ):
                avancou_72h += 1

    return AbRascunhos(
        n_vinculados=len(linhas),
        escolha_1=escolha_1,
        escolha_2=escolha_2,
        escreveu_do_zero=escreveu_do_zero,
        editado=editado,
        sem_edicao=sem_edicao,
        avancou_72h=avancou_72h,
        n_avaliavel_avanco=n_avaliavel_avanco,
    )


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
