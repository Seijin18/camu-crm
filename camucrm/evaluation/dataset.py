"""Conjunto de avaliação: 30 conversas reais rotuladas à mão (§7).

"Sem isso, categorização por LLM é decoração: ela sempre devolve um rótulo, e
ninguém sabe se está certo."

O documento é claro sobre quem faz este arquivo: o Marcos. Custa ~1h e é
insubstituível — só quem conhece o cliente sabe o rótulo correto. Este módulo
só carrega e valida o formato; ele não gera rótulo nenhum, e um dataset gerado
por LLM mediria o modelo contra ele mesmo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..extraction.contract import FATOS_BOOLEANOS
from ..rules.sinais import ENTRADA, SAIDA, Mensagem
from ..taxonomia import B2B, B2C, OBJECOES, TODOS_ESTAGIOS

TAMANHO_MINIMO = 30  # §7


class DatasetInvalidoError(ValueError):
    """Linha do dataset fora do formato. Falha alto de propósito.

    Um rótulo silenciosamente ignorado tornaria a métrica melhor do que a
    realidade, que é exatamente o modo de falha que o eval existe para pegar.
    """


@dataclass(frozen=True)
class ConversaRotulada:
    """Uma conversa do conjunto de avaliação, com o rótulo humano."""

    id: str
    funil: str
    mensagens: tuple[Mensagem, ...]
    estagio_final: str
    objecao: str | None = None
    fatos: Mapping[str, bool] = field(default_factory=dict)
    marcos: frozenset[str] = frozenset()
    nota: str | None = None

    @property
    def transcricao(self) -> list[tuple[str, str]]:
        return [(m.direcao, m.texto) for m in self.mensagens]

    @property
    def textos(self) -> list[str]:
        return [m.texto for m in self.mensagens]


def carregar(caminho: str | Path) -> list[ConversaRotulada]:
    """Carrega um dataset JSONL (uma conversa por linha)."""
    caminho = Path(caminho)
    if not caminho.exists():
        raise DatasetInvalidoError(f"dataset não encontrado: {caminho}")
    conversas = []
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        linha = linha.strip()
        if not linha or linha.startswith("//"):
            continue
        try:
            bruto = json.loads(linha)
        except json.JSONDecodeError as exc:
            raise DatasetInvalidoError(f"{caminho}:{numero} JSON inválido: {exc}") from exc
        conversas.append(validar_entrada(bruto, f"{caminho}:{numero}"))
    return conversas


def validar_entrada(bruto: Mapping[str, Any], onde: str) -> ConversaRotulada:
    """Valida e converte uma entrada bruta (arquivo OU painel) em `ConversaRotulada`.

    Change `ground-truth-no-painel`: único lugar do sistema que valida uma
    entrada de ground truth — `carregar()` (lendo do arquivo) e as rotas
    `/api/eval/*` do painel (recebendo JSON do formulário/`conversa_id`)
    chamam esta função, nunca reimplementam a regra (mesma disciplina de
    `db.py` ser o único lugar com SQL). `onde` é só para a mensagem de erro
    apontar a origem (linha do arquivo, ou o id da entrada no painel).
    """
    identificador = str(bruto.get("id") or onde)
    funil = str(bruto.get("funil") or B2C).lower()
    if funil not in (B2B, B2C):
        raise DatasetInvalidoError(f"{onde}: funil inválido {funil!r}")

    rotulo = bruto.get("rotulo") or {}
    if not isinstance(rotulo, Mapping):
        raise DatasetInvalidoError(f"{onde}: `rotulo` precisa ser um objeto")

    estagio = str(rotulo.get("estagio_final") or "").strip().upper()
    if estagio not in TODOS_ESTAGIOS:
        raise DatasetInvalidoError(
            f"{onde}: estagio_final {estagio!r} fora da taxonomia"
        )

    objecao = rotulo.get("objecao")
    if objecao is not None:
        objecao = str(objecao).strip().lower()
        if objecao not in OBJECOES:
            raise DatasetInvalidoError(f"{onde}: objeção {objecao!r} fora da taxonomia")

    fatos_rotulados = rotulo.get("fatos") or {}
    if not isinstance(fatos_rotulados, Mapping):
        raise DatasetInvalidoError(f"{onde}: `rotulo.fatos` precisa ser um objeto")
    desconhecidos = set(fatos_rotulados) - set(FATOS_BOOLEANOS)
    if desconhecidos:
        raise DatasetInvalidoError(
            f"{onde}: fatos fora do contrato: {sorted(desconhecidos)}"
        )

    mensagens = tuple(_para_mensagem(m, onde) for m in bruto.get("mensagens") or [])
    if not mensagens:
        raise DatasetInvalidoError(f"{onde}: conversa sem mensagens")

    return ConversaRotulada(
        id=identificador,
        funil=funil,
        mensagens=mensagens,
        estagio_final=estagio,
        objecao=objecao,
        fatos={c: bool(fatos_rotulados.get(c, False)) for c in FATOS_BOOLEANOS},
        marcos=frozenset(rotulo.get("marcos") or ()),
        nota=bruto.get("nota"),
    )


def _para_mensagem(bruto: Mapping[str, Any], onde: str) -> Mensagem:
    direcao = str(bruto.get("direcao") or "").lower()
    if direcao not in (ENTRADA, SAIDA):
        raise DatasetInvalidoError(f"{onde}: direção inválida {direcao!r} (use in/out)")
    return Mensagem(
        direcao=direcao,
        enviada_em=_momento(bruto.get("enviada_em")),
        texto=str(bruto.get("texto") or ""),
    )


def _momento(bruto: Any) -> datetime:
    if isinstance(bruto, str) and bruto.strip():
        try:
            momento = datetime.fromisoformat(bruto.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise DatasetInvalidoError(f"timestamp inválido: {bruto!r}") from exc
        return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
    if isinstance(bruto, (int, float)):
        return datetime.fromtimestamp(bruto, tz=timezone.utc)
    raise DatasetInvalidoError(f"mensagem sem `enviada_em`: {bruto!r}")


def avisos_de_tamanho(conversas: Sequence[ConversaRotulada]) -> Iterator[str]:
    """Avisa quando o dataset é pequeno demais para o número significar algo."""
    if len(conversas) < TAMANHO_MINIMO:
        yield (
            f"dataset com {len(conversas)} conversa(s); §7 pede {TAMANHO_MINIMO}. "
            "Abaixo disso as taxas oscilam demais para orientar mudança de prompt."
        )
    com_objecao = sum(1 for c in conversas if c.objecao)
    if com_objecao < 5:
        yield (
            f"apenas {com_objecao} conversa(s) com objeção rotulada; "
            "a meta de 80% em objeção não é medível com essa amostra."
        )
