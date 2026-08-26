"""Roda o eval contra o conjunto rotulado e compara com as metas da §7.

Metas mínimas:
- Extração de fatos: ≥90% de concordância
- Objeção: ≥80%
- **Falso positivo de avanço de estágio: 0**

A terceira não é uma meta de precisão, é uma trava. §7: "Errar para menos é
perder tempo; errar para mais é abandonar um lead quente achando que ele já foi
tratado". Por isso ela é reportada como contagem absoluta, com a lista das
conversas, e não como porcentagem — 97% de acerto com 1 falso positivo reprova.

Roda sem banco e sem rede (com `FakeLlm`), o que é o que torna "rodar o eval a
cada mudança de prompt" (§7) barato de verdade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from ..extraction import prompt as prompt_mod
from ..extraction.contract import (
    FATOS_BOOLEANOS,
    ContratoInvalidoError,
    Extracao,
    build_corpus,
    extracao_vazia,
    validar,
)
from ..llm import LlmClient, LlmIndisponivelError
from ..rules.estagio import derive
from ..rules.sinais import construir_sinais
from ..taxonomia import rank_estagio, is_terminal
from .dataset import ConversaRotulada, avisos_de_tamanho

META_FATOS = 0.90
META_OBJECAO = 0.80
META_FALSOS_POSITIVOS = 0


@dataclass
class ResultadoConversa:
    id: str
    fatos_certos: int
    fatos_total: int
    objecao_esperada: str | None
    objecao_obtida: str | None
    estagio_esperado: str
    estagio_obtido: str
    erro: str | None = None

    @property
    def objecao_correta(self) -> bool:
        return self.objecao_esperada == self.objecao_obtida

    @property
    def falso_positivo_estagio(self) -> bool:
        """Avançou mais do que o rótulo humano diz — o erro caro.

        Estágio terminal não entra na comparação de rank: `SX` não é "mais
        avançado" que `S2`, é saída do funil. Divergência envolvendo terminal
        é erro, mas não é *falso positivo de avanço*, que é o que a trava mede.
        """
        if is_terminal(self.estagio_obtido) or is_terminal(self.estagio_esperado):
            return False
        return rank_estagio(self.estagio_obtido) > rank_estagio(self.estagio_esperado)

    @property
    def estagio_correto(self) -> bool:
        return self.estagio_obtido == self.estagio_esperado


@dataclass
class RelatorioEval:
    prompt_versao: str
    resultados: list[ResultadoConversa] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    rodado_em: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # -- taxas ------------------------------------------------------------

    @property
    def concordancia_fatos(self) -> float | None:
        total = sum(r.fatos_total for r in self.resultados)
        if not total:
            return None
        return sum(r.fatos_certos for r in self.resultados) / total

    @property
    def acerto_objecao(self) -> float | None:
        """Medido só onde há objeção a acertar (esperada ou obtida).

        Incluir as conversas sem objeção nenhuma inflaria a taxa com acertos
        triviais (`None` == `None`) e esconderia o que a meta de 80% quer
        medir.
        """
        relevantes = [
            r for r in self.resultados if r.objecao_esperada or r.objecao_obtida
        ]
        if not relevantes:
            return None
        return sum(1 for r in relevantes if r.objecao_correta) / len(relevantes)

    @property
    def acerto_estagio(self) -> float | None:
        if not self.resultados:
            return None
        return sum(1 for r in self.resultados if r.estagio_correto) / len(self.resultados)

    @property
    def falsos_positivos(self) -> list[ResultadoConversa]:
        return [r for r in self.resultados if r.falso_positivo_estagio]

    # -- veredito ---------------------------------------------------------

    @property
    def aprovado(self) -> bool:
        fatos = self.concordancia_fatos
        objecao = self.acerto_objecao
        if fatos is None or fatos < META_FATOS:
            return False
        if objecao is not None and objecao < META_OBJECAO:
            return False
        return len(self.falsos_positivos) <= META_FALSOS_POSITIVOS

    def __str__(self) -> str:
        linhas = [
            f"EVAL (§7) — prompt v{self.prompt_versao}, "
            f"{len(self.resultados)} conversa(s), {self.rodado_em:%Y-%m-%d %H:%M}",
            "=" * 60,
            _linha_meta("Extração de fatos", self.concordancia_fatos, META_FATOS),
            _linha_meta("Objeção", self.acerto_objecao, META_OBJECAO),
            _linha_meta("Estágio exato (informativo)", self.acerto_estagio, None),
        ]
        falsos = self.falsos_positivos
        marca = "OK" if not falsos else "REPROVA"
        linhas.append(
            f"  [{marca}] Falso positivo de avanço: {len(falsos)} "
            f"(meta: {META_FALSOS_POSITIVOS})"
        )
        for r in falsos:
            linhas.append(
                f"        {r.id}: rotulado {r.estagio_esperado}, "
                f"derivado {r.estagio_obtido}"
            )
        erros = [r for r in self.resultados if r.erro]
        if erros:
            linhas.append(f"  {len(erros)} conversa(s) com erro de extração:")
            linhas.extend(f"        {r.id}: {r.erro}" for r in erros)
        for aviso in self.avisos:
            linhas.append(f"  AVISO: {aviso}")
        linhas.append("")
        linhas.append("VEREDITO: " + ("APROVADO" if self.aprovado else "REPROVADO"))
        return "\n".join(linhas)


def _linha_meta(nome: str, valor: float | None, meta: float | None) -> str:
    if valor is None:
        return f"  [--] {nome}: sem amostra"
    if meta is None:
        return f"  [--] {nome}: {valor:.0%}"
    marca = "OK" if valor >= meta else "REPROVA"
    return f"  [{marca}] {nome}: {valor:.0%} (meta {meta:.0%})"


def rodar(
    llm: LlmClient, conversas: Sequence[ConversaRotulada], *, agora: datetime | None = None
) -> RelatorioEval:
    """Extrai e deriva estágio para cada conversa rotulada, e compara.

    O caminho exercitado é o mesmo da produção — mesmo prompt, mesmo contrato,
    mesma regra de derivação. Um eval que testasse um caminho paralelo mediria
    outra coisa.
    """
    agora = agora or datetime.now(timezone.utc)
    relatorio = RelatorioEval(prompt_versao=prompt_mod.PROMPT_VERSAO)
    relatorio.avisos.extend(avisos_de_tamanho(conversas))

    for conversa in conversas:
        extracao, erro = _extrair(llm, conversa)
        sinais = construir_sinais(
            conversa.mensagens,
            funil=conversa.funil,
            # O relógio é o da última mensagem, não o de hoje: rodar o eval
            # amanhã não pode mudar o estágio de uma conversa congelada, senão
            # a métrica varia sem o prompt ter mudado.
            agora=max(m.enviada_em for m in conversa.mensagens),
            preco_apresentado_em=(
                conversa.mensagens[0].enviada_em if extracao["preco_apresentado"] else None
            ),
            autorizou_em=(
                conversa.mensagens[0].enviada_em
                if extracao["autorizou_envio_material"]
                else None
            ),
            ganho="ganho" in conversa.marcos,
            consignacao_assinada="consignacao_assinada" in conversa.marcos,
            primeira_reposicao="primeira_reposicao" in conversa.marcos,
        )
        derivado = derive(extracao.fatos, sinais)

        certos = sum(
            1
            for campo in FATOS_BOOLEANOS
            if bool(extracao.fatos.get(campo)) == bool(conversa.fatos.get(campo))
        )
        relatorio.resultados.append(
            ResultadoConversa(
                id=conversa.id,
                fatos_certos=certos,
                fatos_total=len(FATOS_BOOLEANOS),
                objecao_esperada=conversa.objecao,
                objecao_obtida=extracao.objecao,
                estagio_esperado=conversa.estagio_final,
                estagio_obtido=derivado.estagio,
                erro=erro,
            )
        )
    return relatorio


def _extrair(llm: LlmClient, conversa: ConversaRotulada) -> tuple[Extracao, str | None]:
    try:
        bruto = llm.completar(
            prompt_mod.system_prompt(),
            prompt_mod.user_prompt(conversa.transcricao, funil=conversa.funil),
            json_estrito=True,
        )
        return validar(bruto, corpus=build_corpus(conversa.textos)), None
    except (LlmIndisponivelError, ContratoInvalidoError) as exc:
        return extracao_vazia(), str(exc)
