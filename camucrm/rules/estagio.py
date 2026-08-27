"""Derivação de estágio: regra determinística, nunca o LLM.

§3 do documento. Estágio é fato observável, e a regra de avanço precisa ser
inequívoca — senão o dado vira opinião. Consequência prática de §1: se estes
critérios mudarem, basta reprocessar os fatos já extraídos, sem custo de LLM e
sem reinterpretar conversa antiga.

Duas garantias que este módulo precisa entregar:

1. **Estágio nunca regride** (§3). Cliente que volta atrás gera objeção, não
   retrocesso — senão o histórico deixa de ser reconstituível.
2. **Replayable.** Mesmos fatos + mesmos sinais = mesmo estágio, sempre.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..taxonomia import (
    B2B,
    B2C,
    funil_do_estagio,
    CAUSADA_POR_CAMU,
    CAUSADA_POR_CLIENTE,
    DIAS_ATE_PERDIDO_B2C,
    ESTAGIO_TERMINAL_B2B,
    ESTAGIO_TERMINAL_B2C,
    MAX_FOLLOWUPS,
    is_terminal,
    rank_estagio,
)
from .sinais import SinaisConversa

ORIGEM_LIVE = "live"
ORIGEM_BACKFILL = "backfill"
ORIGENS = (ORIGEM_LIVE, ORIGEM_BACKFILL)


@dataclass(frozen=True)
class Derivacao:
    """Estágio derivado + o fato-gatilho que o produziu.

    O motivo não é enfeite: §5 exige que a classificação seja auditável, e a
    mesma exigência vale aqui — quando o Marcos discordar do estágio, ele
    precisa ver qual campo disparou, não reconstruir a regra de cabeça.

    `causada_por` (change `estagio-reabertura-manual-e-relogio`, design.md):
    formaliza o mapa que já era implícito nas condições abaixo — se quem
    produziu o avanço foi o CLIENTE (respondeu, mandou foto, autorizou,
    pagou) ou a própria CAMU (mandou prévia, apresentou preço, entregou
    proposta B2B sem resposta ainda). `rules/temperatura.py::classificar`
    consulta este campo para não confundir atividade nossa com reciprocidade
    do cliente (§5) — nenhuma segunda implementação da mesma regra.
    """

    estagio: str
    motivo: str
    causada_por: str = CAUSADA_POR_CLIENTE

    def __str__(self) -> str:
        return f"{self.estagio} ({self.motivo})"


@dataclass(frozen=True)
class Transicao:
    """Uma mudança de estágio a ser gravada em `eventos_estagio`."""

    de: str | None
    para: str
    motivo: str
    origem: str = ORIGEM_LIVE
    causada_por: str = CAUSADA_POR_CLIENTE


def derive(fatos: Mapping[str, bool], sinais: SinaisConversa) -> Derivacao:
    """Estágio que os fatos e sinais sustentam, ignorando o estágio atual.

    Deliberadamente sem memória: quem impede regressão é `transicao()`. Manter
    a derivação pura torna possível responder "que estágio estes fatos
    sustentam?" — a pergunta do eval (§7) e do backfill (§8).
    """
    if sinais.funil == B2B:
        return _derive_b2b(fatos, sinais)
    return _derive_b2c(fatos, sinais)


def _derive_b2c(fatos: Mapping[str, bool], sinais: SinaisConversa) -> Derivacao:
    # Ganho vem antes de tudo: pagamento confirmado não é revogado por
    # silêncio posterior, e um cliente que pagou e sumiu não é "perdido".
    if sinais.ganho:
        return Derivacao("S6", "ganho manual (pagamento confirmado)")

    # Change `estagio-reabertura-manual-e-relogio`, design.md: uma
    # desconsideração ativa (registrada em `correcoes`, nunca apagando o
    # fato) faz a derivação pular esta condição, caindo para o que os
    # demais fatos sustentam — é o que permite reabrir um falso positivo
    # de extração sem jamais reescrever `fatos.recusa_explicita`.
    if fatos.get("recusa_explicita") and not sinais.recusa_desconsiderada:
        return Derivacao(ESTAGIO_TERMINAL_B2C, "recusa_explicita")
    dias = sinais.dias_sem_resposta
    if dias is not None and dias >= DIAS_ATE_PERDIDO_B2C:
        return Derivacao(
            ESTAGIO_TERMINAL_B2C, f"{DIAS_ATE_PERDIDO_B2C} dias sem resposta"
        )

    # S5 exige as duas metades de "respondeu ao preço sem recusar": a recusa
    # já foi tratada acima, então aqui basta o preço mais a resposta a ele.
    if fatos.get("preco_apresentado") and sinais.inbound_apos_preco:
        return Derivacao("S5", "respondeu ao preço sem recusar")
    if fatos.get("preco_apresentado"):
        # Camu que apresentou o preço, sem resposta ainda — não é
        # reciprocidade do cliente (§5), ver `Derivacao.causada_por`.
        return Derivacao("S4", "preco_apresentado", causada_por=CAUSADA_POR_CAMU)
    if fatos.get("previa_enviada"):
        return Derivacao("S3", "previa_enviada", causada_por=CAUSADA_POR_CAMU)
    # S2 é o estágio-chave (§3): quem manda a foto do pet já se comprometeu.
    if fatos.get("foto_pet_recebida"):
        return Derivacao("S2", "foto_pet_recebida")
    if sinais.tem_inbound:
        return Derivacao("S1", "mensagem espontânea do cliente")
    return Derivacao("S0", "conversa existe")


def _derive_b2b(fatos: Mapping[str, bool], sinais: SinaisConversa) -> Derivacao:
    # P6 é a única validação real (§3): P5 prova que o lojista aceitou algo de
    # graça; P6 prova que o produto vende.
    if sinais.primeira_reposicao:
        return Derivacao("P6", "primeira reposição (manual)")
    if sinais.consignacao_assinada:
        return Derivacao("P5", "consignação assinada (manual)")

    # Ver comentário equivalente em `_derive_b2c`: desconsideração ativa
    # pula esta condição sem tocar no fato.
    if fatos.get("recusa_explicita") and not sinais.recusa_desconsiderada:
        return Derivacao(ESTAGIO_TERMINAL_B2B, "recusa_explicita")
    if sinais.followups_sem_retorno >= MAX_FOLLOWUPS:
        return Derivacao(
            ESTAGIO_TERMINAL_B2B, f"{MAX_FOLLOWUPS} follow-ups sem retorno"
        )

    if fatos.get("visita_aceita"):
        return Derivacao("P4", "visita_aceita")
    if sinais.proposta_apresentada:
        # Msg 2 entregue pela Camu, ainda sem resposta do lojista.
        return Derivacao(
            "P3", "msg 2 entregue após autorização", causada_por=CAUSADA_POR_CAMU
        )
    if fatos.get("autorizou_envio_material"):
        return Derivacao("P2", "autorizou_envio_material")
    if sinais.total_outbound >= 1:
        # Msg 1 é a Camu abordando — nenhuma resposta do lojista ainda.
        return Derivacao("P1", "msg 1 enviada", causada_por=CAUSADA_POR_CAMU)
    return Derivacao("P0", "na shortlist, não abordado")


def transicao(
    estagio_atual: str | None,
    derivacao: Derivacao,
    *,
    origem: str = ORIGEM_LIVE,
) -> Transicao | None:
    """A transição a gravar, ou `None` quando nada muda.

    É aqui que mora a regra de não-regressão (§3). Casos:

    - Sem estágio atual: entra no derivado.
    - Derivado igual ao atual: nada acontece — reprocessar não pode duplicar
      evento (§2, idempotência).
    - Derivado com rank menor: **ignorado**. É o caso de um fato antigo que
      deixou de ser afirmado por um bloco novo, e a resposta é não mexer.
    - Derivado terminal: aceito. Sair do funil não é regredir nele.
    - Atual terminal: só um marco manual reabre (ver `reabrir`). Silêncio
      posterior não move nada, e recusa explícita não se desfaz sozinha.
    """
    if origem not in ORIGENS:
        raise ValueError(f"origem inválida: {origem!r} (use {ORIGENS})")

    novo = derivacao.estagio
    if estagio_atual == novo:
        return None

    if estagio_atual is None:
        return Transicao(None, novo, derivacao.motivo, origem, derivacao.causada_por)

    # Ganho/consignação/reposição reabrem uma conversa dada como encerrada:
    # o cliente que voltou e pagou não fica "perdido" no histórico.
    if is_terminal(estagio_atual):
        if is_terminal(novo):
            return None
        if _e_marco_manual(novo):
            return Transicao(
                estagio_atual, novo, derivacao.motivo, origem, derivacao.causada_por
            )
        return None

    if is_terminal(novo):
        return Transicao(
            estagio_atual, novo, derivacao.motivo, origem, derivacao.causada_por
        )

    if rank_estagio(novo) <= rank_estagio(estagio_atual):
        return None

    return Transicao(
        estagio_atual, novo, derivacao.motivo, origem, derivacao.causada_por
    )


def _e_marco_manual(estagio: str) -> bool:
    from ..taxonomia import ESTAGIOS_MANUAIS

    return estagio in ESTAGIOS_MANUAIS


def reabrir(
    estagio_terminal: str,
    estagio_maximo_alcancado: str | None,
    *,
    recusa_explicita: bool = False,
    recusa_desconsiderada: bool = False,
) -> Transicao | None:
    """Reabre uma conversa que fechou por silêncio (ou por recusa
    desconsiderada) e voltou a falar.

    Chamada explicitamente quando chega um inbound numa conversa terminal.
    Volta ao **maior estágio já alcançado**, não a S1: o cliente que mandou a
    foto, sumiu 14 dias e voltou continua sendo alguém que mandou a foto —
    tratá-lo como lead novo apagaria o compromisso que ele já tinha assumido.

    Vale para fechamento por timeout (`recusa_explicita=False`, o padrão) OU
    para uma recusa explícita que um operador já desconsiderou
    (`recusa_explicita=True, recusa_desconsiderada=True`) — change
    `estagio-reabertura-manual-e-relogio`, exceção explícita e auditada ao
    invariante #2/§3 do CLAUDE.md ("estágio nunca regride"), nunca uma
    reabertura automática.

    A checagem é feita AQUI, não só pelo chamador (design.md: "reabrir()
    valida a checagem sozinha, não confia no chamador") — uma recusa
    explícita sem desconsideração ativa nunca reabre, mesmo que quem chamou
    esta função tenha esquecido de filtrar isso antes.
    """
    if not is_terminal(estagio_terminal):
        return None
    if recusa_explicita and not recusa_desconsiderada:
        return None
    if not estagio_maximo_alcancado or is_terminal(estagio_maximo_alcancado):
        return None
    return Transicao(
        estagio_terminal,
        estagio_maximo_alcancado,
        "cliente voltou a responder após timeout"
        if not recusa_explicita
        else "recusa_explicita desconsiderada — cliente voltou a responder",
        ORIGEM_LIVE,
        CAUSADA_POR_CLIENTE,
    )


def trilha(fatos: Mapping[str, bool], sinais: SinaisConversa) -> list[Derivacao]:
    """Todos os estágios que os fatos sustentam, em ordem — não só o final.

    Existe para o backfill (§8). `derive` devolve onde a conversa *está*, e
    isso basta ao vivo, onde cada avanço foi observado no momento em que
    aconteceu. No histórico não: uma conversa de julho que recebeu a foto e
    depois esfriou deriva direto para `SX`, e gravar só isso apagaria o fato de
    ela ter chegado em S2 — destruindo exatamente a métrica de conversão que
    §8 diz que o backfill deve produzir ("quantos chegaram a cada estágio").

    O que esta função **não** faz é inventar estágio intermediário: um estágio
    só entra na trilha se o gatilho dele foi de fato satisfeito. Se a Camu
    mandou a prévia sem que houvesse foto registrada, a trilha tem S3 e não S2
    — o dado fica esquisito porque a conversa foi esquisita, e é assim que se
    descobre isso olhando os números depois.

    Os timestamps continuam sem valor: por isso tudo isto é gravado com
    `origem='backfill'` e excluído de qualquer métrica de tempo.
    """
    final = derive(fatos, sinais)
    gatilhos = (
        _gatilhos_b2b(fatos, sinais)
        if sinais.funil == B2B
        else _gatilhos_b2c(fatos, sinais)
    )
    if is_terminal(final.estagio):
        return [*gatilhos, final]
    limite = rank_estagio(final.estagio)
    percurso = [g for g in gatilhos if rank_estagio(g.estagio) <= limite]
    if not percurso or percurso[-1].estagio != final.estagio:
        percurso.append(final)
    return percurso


def _gatilhos_b2c(fatos: Mapping[str, bool], sinais: SinaisConversa) -> list[Derivacao]:
    marcos: list[Derivacao] = []
    if sinais.tem_inbound:
        marcos.append(Derivacao("S1", "mensagem espontânea do cliente"))
    if fatos.get("foto_pet_recebida"):
        marcos.append(Derivacao("S2", "foto_pet_recebida"))
    if fatos.get("previa_enviada"):
        marcos.append(Derivacao("S3", "previa_enviada", causada_por=CAUSADA_POR_CAMU))
    if fatos.get("preco_apresentado"):
        marcos.append(
            Derivacao("S4", "preco_apresentado", causada_por=CAUSADA_POR_CAMU)
        )
    if fatos.get("preco_apresentado") and sinais.inbound_apos_preco:
        marcos.append(Derivacao("S5", "respondeu ao preço sem recusar"))
    if sinais.ganho:
        marcos.append(Derivacao("S6", "ganho manual (pagamento confirmado)"))
    return marcos


def _gatilhos_b2b(fatos: Mapping[str, bool], sinais: SinaisConversa) -> list[Derivacao]:
    marcos: list[Derivacao] = []
    if sinais.total_outbound >= 1:
        marcos.append(Derivacao("P1", "msg 1 enviada", causada_por=CAUSADA_POR_CAMU))
    if fatos.get("autorizou_envio_material"):
        marcos.append(Derivacao("P2", "autorizou_envio_material"))
    if sinais.proposta_apresentada:
        marcos.append(
            Derivacao(
                "P3", "msg 2 entregue após autorização", causada_por=CAUSADA_POR_CAMU
            )
        )
    if fatos.get("visita_aceita"):
        marcos.append(Derivacao("P4", "visita_aceita"))
    if sinais.consignacao_assinada:
        marcos.append(Derivacao("P5", "consignação assinada (manual)"))
    if sinais.primeira_reposicao:
        marcos.append(Derivacao("P6", "primeira reposição (manual)"))
    return marcos


def mudar_funil(
    estagio_atual: str,
    fatos: Mapping[str, bool],
    sinais: SinaisConversa,
) -> Transicao | None:
    """Reclassificação de funil: o único caso em que o estágio troca de escada.

    `sinais.funil` já é o funil NOVO. A regra de não-regressão (§3) fala de
    avanço dentro de um funil — `S2` e `P1` não são comparáveis, e recusar a
    troca por rank deixaria a conversa presa numa escada que não é a dela.

    O estágio novo é o que os mesmos fatos sustentam no funil de destino, e
    normalmente é mais baixo: um petshop que "mandou a foto" (S2) não ganha
    nada por isso no funil B2B, onde o que conta é ter autorizado o envio de
    material. Isso não é perda de informação — os fatos continuam gravados e o
    evento registra de onde veio, então o histórico segue reconstituível.

    Devolve `None` quando o estágio derivado é igual ao atual, o que acontece
    ao reclassificar uma conversa que ainda não avançou.
    """
    derivado = derive(fatos, sinais)
    if derivado.estagio == estagio_atual:
        return None
    origem_funil = funil_do_estagio(estagio_atual)
    return Transicao(
        estagio_atual,
        derivado.estagio,
        f"reclassificado de {origem_funil.upper()} para {sinais.funil.upper()}",
        ORIGEM_LIVE,
        derivado.causada_por,
    )


# Fatos que só fazem sentido no funil B2B: quem responde "pode mandar" a uma
# oferta de material, ou aceita uma visita presencial, está se comportando
# como lojista, não como consumidor final. Ver `sugere_b2b`.
FATOS_B2B = ("autorizou_envio_material", "visita_aceita")


def sugere_b2b(funil: str, fatos: Mapping[str, bool]) -> bool:
    """Se uma conversa classificada como B2C mostra comportamento de petshop.

    É **sugestão para um humano**, nunca reclassificação automática — §1 tira
    inferência de decisão de negócio, e classificar errado joga a conversa no
    funil errado, onde ela sai da fila pela regra errada e ninguém descobre
    por quê. O LLM extraiu um fato; quem decide o que ele significa é quem
    conhece o cliente.
    """
    if funil == B2B:
        return False
    return any(fatos.get(chave) for chave in FATOS_B2B)


def estagio_inicial(funil: str) -> str:
    """Estágio de entrada do funil."""
    return "P0" if funil == B2B else "S0"


__all__ = [
    "B2B",
    "B2C",
    "CAUSADA_POR_CAMU",
    "CAUSADA_POR_CLIENTE",
    "Derivacao",
    "ORIGEM_BACKFILL",
    "ORIGEM_LIVE",
    "Transicao",
    "derive",
    "estagio_inicial",
    "mudar_funil",
    "reabrir",
    "sugere_b2b",
    "transicao",
    "trilha",
]
