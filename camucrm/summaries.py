"""Resumo de conversa por LLM, gerado sob demanda (change `resumo-conversa`).

Terceira superfície de LLM do sistema, ao lado de `extraction/` e
`drafts.py` — uma divergência real com o `CLAUDE.md` ("o LLM aparece em
exatamente dois lugares"), registrada ali, em `openspec/project.md` e aqui,
porque nenhum dos três sozinho basta.

A propriedade que mantém §1 de pé, apesar da divergência: `extraction/`
alimenta `fatos`, que alimenta as regras de `rules/` — um erro ali é
corretude ESTRUTURAL, produz sistematicamente um estágio errado. `drafts.py`
e `summaries.py` são TERMINAIS: a saída daqui não retroalimenta `fatos` nem
nenhuma regra — `resumos_conversa` é FOLHA do grafo. Um erro deste módulo
custa uma leitura ruim para um humano, nunca um estágio errado gravado no
banco. A regra original permanece íntegra: se um módulo de `camucrm/rules/`
importar `llm`, `drafts`, `summaries` ou `extraction`, a arquitetura vazou.

O teste que sustenta essa propriedade como comportamento, não só como regra
de import, é `tests/test_summaries.py::TesteResumoNaoMudaEstado` (espelhado
em `tests/test_e2e.py::TesteResumoNaoMudaEstado`): roda o ciclo completo
(mensagem -> fatos -> estágio -> temperatura -> fila) com `FakeDatabase`,
guarda `(estagio, temperatura, fila)`, gera um resumo, roda de novo, afirma
igualdade exata. Apagar `resumos_conversa` inteira não muda nada em
`conversas`/`eventos_estagio` — é cache de leitura humana, nada mais.

Divisão de trabalho igual à de `drafts.py`: o LLM escreve, a regra verifica.
Uma retentativa com o motivo devolvido, copiando o padrão de `drafts.gerar`.

Diferente do prompt de extração (`extraction/prompt.py`), mudar
`PROMPT_VERSAO_RESUMO` NÃO exige rodar `make eval` — a regra do CLAUDE.md
("mudou o prompt? rode o eval") é sobre fatos com ground truth rotulado à
mão; não existe ground truth para prosa, e inventar um seria teatro. O bump
de versão já invalida o cache sozinho, via o índice único de
`resumos_conversa`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Sequence

from .db import CorrecaoRegistro, EventoRegistro, FatoRegistro, FollowupRegistro, ObjecaoRegistro
from .drafts import _PRECO
from .llm import LlmClient, LlmIndisponivelError
from .rules.estagio import ORIGEM_BACKFILL
from .taxonomia import MAX_FOLLOWUPS, TEMPERATURAS, TODOS_ESTAGIOS, estagio_label

logger = logging.getLogger("camucrm.summaries")

# Change `resumo-conversa`: gravada junto com cada linha de `resumos_conversa`
# para o histórico saber sob qual versão de prompt cada resumo foi gerado —
# mesma disciplina de `drafts.PROMPT_VERSAO`/`extraction.prompt.PROMPT_VERSAO`.
# Ver docstring do módulo: bump aqui NÃO exige `make eval`.
PROMPT_VERSAO_RESUMO = "1"

MAX_LINHAS_RESUMO = 5
MAX_LINHAS_PROXIMO_PASSO = 1

# §8: mesmo texto que `camucrm.painel.views.AVISO_BACKFILL` usa na tela —
# duplicado aqui de propósito (não importado de `painel/`, que é camada
# acima deste módulo de domínio) para as duas superfícies nunca divergirem
# de fórmula, só de string literal.
AVISO_BACKFILL = "momento reconstruído, não confiável (§8)"


@dataclass(frozen=True)
class ContextoResumo:
    """Tudo que o prompt de resumo precisa — já lido do banco pelo chamador.

    `summaries.py` não abre conexão nenhuma (mesma convenção de
    `camucrm.painel.views`): quem monta este dataclass é `camucrm.painel.api`
    (ou `camucrm.cli`), que já tem os métodos de leitura de `db.py` à mão.
    """

    funil: str
    estagio: str
    temperatura: str
    sinal: str
    fatos: Sequence[FatoRegistro]
    eventos: Sequence[EventoRegistro]
    objecoes: Sequence[ObjecaoRegistro]
    correcoes: Sequence[CorrecaoRegistro]
    followups: Sequence[FollowupRegistro]
    historico: Sequence[tuple[str, str]]
    max_followups: int = MAX_FOLLOWUPS


@dataclass(frozen=True)
class Resumo:
    """Resultado de uma geração válida — nunca meio resumo."""

    resumo: str
    proximo_passo: str


class ResumoInvalidoError(RuntimeError):
    """O modelo não produziu um resumo dentro das restrições verificáveis."""


def system_prompt() -> str:
    return (
        "Você resume, para um humano que vai assumir a conversa agora, o "
        "estado de uma negociação de peças personalizadas com a foto do pet "
        "do cliente (Camu). É uma passagem de bastão entre atendentes.\n\n"
        "RESTRIÇÕES (todas obrigatórias):\n"
        f"- Resumo em terceira pessoa, {MAX_LINHAS_RESUMO} linhas no máximo.\n"
        f"- Próximo passo: uma frase só ({MAX_LINHAS_PROXIMO_PASSO} linha).\n"
        "- NUNCA afirme estágio, temperatura ou prioridade — eles já são "
        "dados a você abaixo; repeti-los cria uma segunda fonte que pode "
        "divergir da real quando a regra recalcular.\n"
        "- NUNCA cite preço, nem valor aproximado.\n"
        "- NUNCA escreva um rascunho de mensagem para o cliente — isso é "
        "outro processo, com outra regra (duas opções, nunca uma).\n"
        "- Baseie-se só no que está listado abaixo. Não invente fato novo.\n\n"
        'Responda apenas com JSON: {"resumo": "...", "proximo_passo": "..."}'
    )


def user_prompt(contexto: ContextoResumo) -> str:
    linhas = [
        f"Funil: {contexto.funil.upper()}",
        f"Estágio: {contexto.estagio} ({estagio_label(contexto.estagio)})",
        f"Temperatura: {contexto.temperatura.upper()} — {contexto.sinal}",
    ]

    linhas.append("\nFatos afirmados (com evidência literal):")
    if contexto.fatos:
        for fato in contexto.fatos:
            quando = (fato.mensagem_em or fato.extraido_em).date().isoformat()
            linhas.append(f'- {fato.chave} — "{fato.evidencia or ""}" ({quando})')
    else:
        linhas.append("- nenhum fato extraído ainda")

    linhas.append("\nLinha do tempo de estágio:")
    if contexto.eventos:
        for evento in contexto.eventos:
            aviso = f" [{AVISO_BACKFILL}]" if evento.origem == ORIGEM_BACKFILL else ""
            linhas.append(
                f"- {evento.de or 'início'} → {evento.para} "
                f"em {evento.em.date().isoformat()}{aviso}"
            )
    else:
        linhas.append("- nenhuma transição registrada")

    linhas.append("\nObjeções levantadas:")
    if contexto.objecoes:
        for objecao in contexto.objecoes:
            linhas.append(
                f'- {objecao.categoria} em {objecao.estagio or "?"}: '
                f'"{objecao.trecho or ""}"'
            )
    else:
        linhas.append("- nenhuma objeção registrada")

    linhas.append("\nCorreções humanas já feitas (não repita o que já foi corrigido):")
    if contexto.correcoes:
        for correcao in contexto.correcoes:
            linhas.append(
                f'- {correcao.campo}: "{correcao.antes or ""}" -> '
                f'"{correcao.depois or ""}" (por {correcao.por or "?"})'
            )
    else:
        linhas.append("- nenhuma correção registrada")

    restantes = max(contexto.max_followups - len(contexto.followups), 0)
    linhas.append(
        f"\nFollow-ups enviados: {len(contexto.followups)}/{contexto.max_followups} "
        f"(restam {restantes} antes do teto §6):"
    )
    if contexto.followups:
        for followup in contexto.followups:
            linhas.append(f"- #{followup.numero}: {followup.texto or '(sem texto salvo)'}")
    else:
        linhas.append("- nenhum follow-up enviado")

    transcricao = "\n".join(
        f"{'CLIENTE' if direcao == 'in' else 'CAMU'}: {(texto or '').strip()}"
        for direcao, texto in list(contexto.historico)[-30:]
    )
    linhas.append("\nÚltimas mensagens:\n---\n" + transcricao + "\n---")

    return "\n".join(linhas)


def _contem_token(texto: str, tokens: Sequence[str]) -> str | None:
    for token in tokens:
        if re.search(rf"\b{re.escape(token)}\b", texto, re.IGNORECASE):
            return token
    return None


def validar_resumo(resumo: str, proximo_passo: str) -> tuple[str, str]:
    """Confere as proibições verificáveis por código (design.md). Levanta em
    violação; devolve `(resumo_limpo, proximo_passo_limpo)` quando válido.
    """
    resumo_limpo = (resumo or "").strip()
    proximo_limpo = (proximo_passo or "").strip()
    if not resumo_limpo:
        raise ResumoInvalidoError("resumo veio vazio")
    if not proximo_limpo:
        raise ResumoInvalidoError("próximo passo veio vazio")

    linhas_resumo = [l for l in resumo_limpo.splitlines() if l.strip()]
    if len(linhas_resumo) > MAX_LINHAS_RESUMO:
        raise ResumoInvalidoError(
            f"resumo tem {len(linhas_resumo)} linha(s); o limite é {MAX_LINHAS_RESUMO}"
        )
    linhas_proximo = [l for l in proximo_limpo.splitlines() if l.strip()]
    if len(linhas_proximo) > MAX_LINHAS_PROXIMO_PASSO:
        raise ResumoInvalidoError(
            f"próximo passo tem {len(linhas_proximo)} linha(s); "
            f"o limite é {MAX_LINHAS_PROXIMO_PASSO}"
        )

    texto_completo = f"{resumo_limpo}\n{proximo_limpo}"

    estagio_citado = _contem_token(texto_completo, sorted(TODOS_ESTAGIOS))
    if estagio_citado:
        raise ResumoInvalidoError(
            f"resumo cita estágio {estagio_citado!r} — é dado a ele, não algo "
            "que ele deveria afirmar"
        )
    temperatura_citada = _contem_token(texto_completo, TEMPERATURAS)
    if temperatura_citada:
        raise ResumoInvalidoError(f"resumo cita temperatura {temperatura_citada!r}")
    if _PRECO.search(texto_completo):
        raise ResumoInvalidoError("resumo cita preço — proibido (§10/reusa drafts._PRECO)")

    return resumo_limpo, proximo_limpo


def _extrair_campos(bruto: str) -> tuple[str, str]:
    texto = (bruto or "").strip()
    cerca = re.search(r"```(?:json)?\s*(.*?)```", texto, re.DOTALL)
    if cerca:
        texto = cerca.group(1).strip()
    try:
        carregado = json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ResumoInvalidoError(f"resposta não é JSON: {exc}") from exc
    if not isinstance(carregado, dict):
        raise ResumoInvalidoError("JSON não é um objeto")
    resumo = carregado.get("resumo")
    proximo_passo = carregado.get("proximo_passo")
    if not isinstance(resumo, str) or not isinstance(proximo_passo, str):
        raise ResumoInvalidoError(
            "JSON sem as chaves `resumo` e `proximo_passo`, ambas string"
        )
    return resumo, proximo_passo


def gerar(llm: LlmClient, contexto: ContextoResumo) -> Resumo:
    """Gera o resumo via LLM. Uma única retentativa em caso de violação, com
    o motivo devolvido ao modelo — mesma política de `drafts.gerar` (duas
    seria custo sem retorno).

    Levanta `ResumoInvalidoError` tanto para violação persistente quanto
    para LLM indisponível — quem chama (`camucrm.painel.api`) trata os dois
    do mesmo jeito: bloco determinístico com `resumo: null`, nunca 500
    (requirement "Falha de LLM não derruba a tela").
    """
    system = system_prompt()
    pedido = user_prompt(contexto)

    ultima_falha: str | None = None
    for tentativa in (1, 2):
        entrada = pedido if ultima_falha is None else (
            f"{pedido}\n\nA tentativa anterior foi recusada: {ultima_falha}. "
            "Corrija e responda de novo, no mesmo formato JSON."
        )
        try:
            bruto = llm.completar(system, entrada, json_estrito=True)
            resumo_bruto, proximo_bruto = _extrair_campos(bruto)
            resumo_limpo, proximo_limpo = validar_resumo(resumo_bruto, proximo_bruto)
            return Resumo(resumo=resumo_limpo, proximo_passo=proximo_limpo)
        except ResumoInvalidoError as exc:
            ultima_falha = str(exc)
            logger.info("Resumo recusado (tentativa %s): %s", tentativa, exc)
        except LlmIndisponivelError as exc:
            raise ResumoInvalidoError(f"LLM indisponível: {exc}") from exc

    raise ResumoInvalidoError(f"não foi possível gerar um resumo válido: {ultima_falha}")
