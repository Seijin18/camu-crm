"""Camada pura do painel: transforma dados já carregados em dicts JSON-prontos.

Nada aqui abre conexão, chama `Database` ou importa FastAPI — é por isso que
`tests/test_painel_views.py` testa esta camada sem banco e sem app. `api.py` é
quem carrega os dados e chama estas funções.

Consequência prática do isolamento: recalcular o kanban ou reordenar a lista
de conversas nunca é motivo para reabrir conexão nenhuma — os dados já vieram
de `pipeline.recalcular(persistir=False)` e das consultas de `db.py`.
"""

from __future__ import annotations

import shlex
from typing import Any, Iterable

from ..db import (
    ContatoResumido,
    Conversa,
    CorrecaoRegistro,
    EventoRegistro,
    FatoRegistro,
    FollowupRegistro,
    MarcoRegistro,
    MensagemRegistro,
    ObjecaoRegistro,
    RascunhoRegistro,
)
from ..pipeline import EstadoConversa
from ..rules.estagio import ORIGEM_BACKFILL
from ..rules.fila import ItemFila
from ..taxonomia import (
    ESTAGIOS_MANUAIS,
    ESTAGIOS_POR_FUNIL,
    TEMPERATURAS,
    TERMINAL_POR_FUNIL,
    estagio_label,
    is_terminal,
    rank_estagio,
)

# §8: o rótulo que avisa que um timestamp de backfill não é confiável para
# nada que meça tempo — o painel é a primeira superfície a mostrar isso.
AVISO_BACKFILL = "momento reconstruído, não confiável (§8)"


def erro(mensagem: str, regra: str | None) -> dict[str, Any]:
    """Formato uniforme de erro: `{"erro", "regra"}` em toda a API do painel."""
    return {"erro": mensagem, "regra": regra}


def card_conversa(conversa: Conversa, estado: EstadoConversa) -> dict[str, Any]:
    """Um card da fila/kanban: o que cabe numa linha, mais o sinal que a explica.

    `sinal` vem de `Classificacao.sinal` (`rules/temperatura.py`) — é a
    primeira superfície a mostrar essa justificativa fora de log (CLAUDE.md /
    plano: "hoje descartada").
    """
    sinais = estado.sinais
    horas_esperando = sinais.horas_desde_inbound
    if horas_esperando is None:
        dias = sinais.dias_sem_resposta
        horas_esperando = None if dias is None else dias * 24

    return {
        "id": conversa.id,
        "nome": conversa.nome_contato or f"#{conversa.id}",
        "funil": conversa.funil,
        "estagio": estado.estagio,
        "estagio_label": estagio_label(estado.estagio),
        "temperatura": estado.temperatura,
        "sinal": estado.classificacao.sinal,
        "bola_com": sinais.bola_com,
        "followups_enviados": conversa.followups_enviados,
        "horas_esperando": horas_esperando,
        "avancou_estagio_hoje": sinais.avancou_estagio_hoje,
    }


def colunas_kanban(funil: str) -> list[dict[str, Any]]:
    """Colunas do kanban de um funil, cada uma já dizendo se aceita drag.

    §3: S6/P5/P6 (`ESTAGIOS_MANUAIS`) e o terminal do funil são os únicos que
    um humano marca — arrastar um card para lá é uma ação válida (quando a
    escrita existir, em change futuro). Todo o resto é derivado de fato
    observável, e mover o card à mão não mudaria nada — a regra recalcularia
    de volta na próxima passagem. Por isso a coluna nasce marcada como não
    aceitando drop, com o motivo já pronto para a UI.
    """
    estagios = list(ESTAGIOS_POR_FUNIL[funil]) + [TERMINAL_POR_FUNIL[funil]]
    colunas = []
    for est in estagios:
        aceita_drop = est in ESTAGIOS_MANUAIS or is_terminal(est)
        derivada = not aceita_drop
        colunas.append(
            {
                "estagio": est,
                "label": estagio_label(est),
                "derivada": derivada,
                "aceita_drop": aceita_drop,
                "motivo_recusa": (
                    None
                    if aceita_drop
                    else f"§3: {est} é derivado de fato observável, não se marca à mão"
                ),
            }
        )
    return colunas


def montar_kanban(cards: list[dict[str, Any]], funil: str) -> dict[str, Any]:
    """Agrupa cards já montados nas colunas do funil."""
    colunas = colunas_kanban(funil)
    por_estagio: dict[str, list[dict[str, Any]]] = {c["estagio"]: [] for c in colunas}
    for card in cards:
        if card["funil"] != funil:
            continue
        por_estagio.setdefault(card["estagio"], []).append(card)
    for coluna in colunas:
        coluna["cards"] = por_estagio.get(coluna["estagio"], [])
    return {"funil": funil, "colunas": colunas}


def filtrar_conversas(
    cards: list[dict[str, Any]],
    *,
    estagio: str | None = None,
    temperatura: str | None = None,
    bola: str | None = None,
    ids_com_objecao: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    """Filtro puro sobre cards já montados.

    O filtro por objeção não consulta `objecoes` aqui — recebe o conjunto de
    `conversa_id` já resolvido por quem chamou (`api.py`), para esta função
    continuar sem I/O e o custo extra só existir quando o parâmetro é usado.
    """
    resultado = cards
    if estagio:
        resultado = [c for c in resultado if c["estagio"] == estagio]
    if temperatura:
        resultado = [c for c in resultado if c["temperatura"] == temperatura]
    if bola:
        resultado = [c for c in resultado if c["bola_com"] == bola]
    if ids_com_objecao is not None:
        ids = set(ids_com_objecao)
        resultado = [c for c in resultado if c["id"] in ids]
    return resultado


_ORDEM_TEMPERATURA = {t: i for i, t in enumerate(TEMPERATURAS)}


def _chave_ordenacao(card: dict[str, Any], campo: str):
    if campo == "nome":
        return card["nome"].lower()
    if campo == "estagio":
        return rank_estagio(card["estagio"])
    if campo == "temperatura":
        return _ORDEM_TEMPERATURA.get(card["temperatura"], len(_ORDEM_TEMPERATURA))
    if campo == "horas_esperando":
        valor = card["horas_esperando"]
        return -1.0 if valor is None else valor
    raise ValueError(f"campo de ordenação desconhecido: {campo!r}")


def ordenar_conversas(
    cards: list[dict[str, Any]], *, campo: str = "horas_esperando", direcao: str = "desc"
) -> list[dict[str, Any]]:
    if direcao not in ("asc", "desc"):
        raise ValueError(f"direção inválida: {direcao!r} (use 'asc' ou 'desc')")
    return sorted(
        cards, key=lambda c: _chave_ordenacao(c, campo), reverse=(direcao == "desc")
    )


def paginar(
    cards: list[dict[str, Any]], *, limite: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    return cards[offset : offset + limite]


def _fato_para_json(fato: FatoRegistro) -> dict[str, Any]:
    return {
        "chave": fato.chave,
        "valor": fato.valor,
        "evidencia": fato.evidencia,
        "extraido_em": fato.extraido_em.isoformat(),
        "mensagem_em": fato.mensagem_em.isoformat() if fato.mensagem_em else None,
    }


def _evento_para_json(evento: EventoRegistro) -> dict[str, Any]:
    item = {
        "de": evento.de,
        "para": evento.para,
        "em": evento.em.isoformat(),
        "origem": evento.origem,
        "motivo": evento.motivo,
    }
    if evento.origem == ORIGEM_BACKFILL:
        item["aviso"] = AVISO_BACKFILL
    return item


def _objecao_para_json(objecao: ObjecaoRegistro) -> dict[str, Any]:
    return {
        "id": objecao.id,
        "categoria": objecao.categoria,
        "estagio": objecao.estagio,
        "trecho": objecao.trecho,
        "em": objecao.em.isoformat(),
    }


def _followup_para_json(followup: FollowupRegistro) -> dict[str, Any]:
    return {
        "numero": followup.numero,
        "texto": followup.texto,
        "enviado_em": followup.enviado_em.isoformat(),
    }


def _marco_para_json(marco: MarcoRegistro) -> dict[str, Any]:
    return {"marco": marco.marco, "em": marco.em.isoformat(), "por": marco.por}


def _correcao_para_json(correcao: CorrecaoRegistro) -> dict[str, Any]:
    return {
        "id": correcao.id,
        "campo": correcao.campo,
        "antes": correcao.antes,
        "depois": correcao.depois,
        "em": correcao.em.isoformat(),
        "por": correcao.por,
    }


def _contato_para_json(contato: ContatoResumido | None) -> dict[str, Any] | None:
    if contato is None:
        return None
    # §12: nunca o telefone em claro, só o booleano de que ele existe.
    return {
        "id": contato.id,
        "nome": contato.nome,
        "tipo": contato.tipo,
        "tem_telefone": contato.tem_telefone,
        "criado_em": contato.criado_em.isoformat(),
    }


def detalhe_conversa(
    conversa: Conversa,
    estado: EstadoConversa,
    *,
    fatos: list[FatoRegistro],
    eventos: list[EventoRegistro],
    objecoes: list[ObjecaoRegistro],
    followups: list[FollowupRegistro],
    marcos: list[MarcoRegistro],
    correcoes: list[CorrecaoRegistro],
    contato: ContatoResumido | None,
) -> dict[str, Any]:
    """Payload completo de `GET /api/conversas/{id}`.

    Telefone nunca entra aqui — só o resumo de `contato_resumido`, que já
    reduz o campo a `tem_telefone: bool` (§12).
    """
    return {
        "card": card_conversa(conversa, estado),
        "fatos": [_fato_para_json(f) for f in fatos],
        "eventos": [_evento_para_json(e) for e in eventos],
        "objecoes": [_objecao_para_json(o) for o in objecoes],
        "followups": [_followup_para_json(f) for f in followups],
        "marcos": [_marco_para_json(m) for m in marcos],
        "correcoes": [_correcao_para_json(c) for c in correcoes],
        "contato": _contato_para_json(contato),
    }


def serializar_mensagens(
    mensagens: list[MensagemRegistro], *, desde_id: int | None
) -> dict[str, Any]:
    return {
        "desde_id": desde_id,
        "mensagens": [
            {
                "id": m.id,
                "direcao": m.direcao,
                "texto": m.texto,
                "enviada_em": m.enviada_em.isoformat(),
            }
            for m in mensagens
        ],
    }


def item_fila_para_json(item: ItemFila) -> dict[str, Any]:
    return {
        "conversa_id": item.conversa_id,
        "nome": item.nome,
        "funil": item.funil,
        "estagio": item.estagio,
        "estagio_label": estagio_label(item.estagio),
        "temperatura": item.temperatura,
        "prioridade": item.prioridade,
        "acao": item.acao,
        "motivo": item.motivo,
        "horas_esperando": item.horas_esperando,
    }


def comando_enviar(rascunho: RascunhoRegistro, opcao: int, texto: str | None) -> str:
    """O comando que o botão "copiar" mostra ao lado do texto (design.md:
    "o painel não envia, mas entrega o comando que carrega o vínculo").

    `shlex.quote` protege o `--texto` de aspas/quebras que o texto da opção
    possa conter — o operador cola e roda, sem editar escapes à mão.
    """
    return (
        f"camucrm enviar {rascunho.conversa_id} --texto {shlex.quote(texto or '')} "
        f"--rascunho {rascunho.id} --opcao {opcao}"
    )


def rascunho_para_json(rascunho: RascunhoRegistro) -> dict[str, Any]:
    """Payload de um rascunho — geração recusada ou com as duas opções.

    §12: nunca telefone (este payload nem carrega contato); os textos
    (`opcao_1`/`opcao_2`/`texto_final`) são exatamente o que o LLM produziu
    ou o humano escreveu, e podem já estar anonimizados pela purga
    (`TEXTO_RASCUNHO_PURGADO`) se a mensagem vinculada tiver saído há tempo
    demais (§12).
    """
    opcoes = None
    comandos = None
    if not rascunho.encerrar:
        opcoes = [rascunho.opcao_1, rascunho.opcao_2]
        comandos = {
            "1": comando_enviar(rascunho, 1, rascunho.opcao_1),
            "2": comando_enviar(rascunho, 2, rascunho.opcao_2),
        }
    return {
        "id": rascunho.id,
        "conversa_id": rascunho.conversa_id,
        "estagio": rascunho.estagio,
        "estagio_label": estagio_label(rascunho.estagio),
        "temperatura": rascunho.temperatura,
        "funil": rascunho.funil,
        "objecao": rascunho.objecao,
        "followups_enviados": rascunho.followups_enviados,
        "opcoes": opcoes,
        "avisos": rascunho.avisos.split("; ") if rascunho.avisos else [],
        "encerrar": rascunho.encerrar,
        "motivo": rascunho.motivo,
        "modelo": rascunho.modelo,
        "prompt_versao": rascunho.prompt_versao,
        "gerado_em": rascunho.gerado_em.isoformat(),
        "gerado_por": rascunho.gerado_por,
        "escolhida": rascunho.escolhida,
        "texto_final": rascunho.texto_final,
        "escolhido_em": rascunho.escolhido_em.isoformat() if rascunho.escolhido_em else None,
        "escolhido_por": rascunho.escolhido_por,
        "mensagem_id": rascunho.mensagem_id,
        "estagio_no_envio": rascunho.estagio_no_envio,
        "comandos": comandos,
    }


def metricas_para_json(metricas_chave, tempo_por_estagio, saude_taxonomia) -> dict[str, Any]:
    """Os três números da §14 + tempo por estágio + saúde da taxonomia.

    Este change (`painel-leitura`) não implementa `AMOSTRA_MINIMA` nem a rota
    `/funciona` — isso é escopo do change 6. Aqui só se serve o que
    `camucrm.metrics` já calcula, sem agregação nova.
    """
    return {
        "conversoes_chave": [
            {
                "de": c.de,
                "para": c.para,
                "alcancaram_de": c.alcancaram_de,
                "alcancaram_para": c.alcancaram_para,
                "taxa": c.taxa,
            }
            for c in metricas_chave
        ],
        "tempo_por_estagio": [
            {
                "estagio": t.estagio,
                "estagio_label": estagio_label(t.estagio),
                "conversas": t.conversas,
                "horas_medianas": t.horas_medianas,
            }
            for t in tempo_por_estagio
        ],
        "saude_taxonomia": {
            "total": saude_taxonomia.total,
            "outros": saude_taxonomia.outros,
            "proporcao_outro": saude_taxonomia.proporcao_outro,
            "veredito": saude_taxonomia.veredito,
            "distribuicao": saude_taxonomia.distribuicao,
        },
    }
