"""Taxonomias fechadas do CRM: funis, estágios, objeções e temperaturas.

Single source of truth para tudo que o documento de definições
(`docs/04-crm-conversas-definicoes.md`) trata como fechado. Nenhum outro
módulo deve redefinir estas listas — §0 do documento é explícito sobre o
custo: "taxonomia mal desenhada contamina todo o histórico".

Deliberadamente sem I/O e sem dependências: importável de qualquer lugar
(db, regras, extração, eval) sem ciclo de import.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Funis (§3)
# --------------------------------------------------------------------------

B2C = "b2c"
B2B = "b2b"
FUNIS = (B2C, B2B)

FUNIL_LABELS = {B2C: "B2C (DM)", B2B: "B2B (petshop)"}


# --------------------------------------------------------------------------
# Estágios (§3)
# --------------------------------------------------------------------------
#
# A ordem das tuplas É a ordem do funil: `rank_estagio` deriva a precedência
# da posição, então inserir um estágio no meio muda a semântica do histórico.
# Estágio terminal fica fora da sequência — não é "mais avançado", é saída.

ESTAGIOS_B2C = ("S0", "S1", "S2", "S3", "S4", "S5", "S6")
ESTAGIO_TERMINAL_B2C = "SX"

ESTAGIOS_B2B = ("P0", "P1", "P2", "P3", "P4", "P5", "P6")
ESTAGIO_TERMINAL_B2B = "PX"

ESTAGIOS_POR_FUNIL = {B2C: ESTAGIOS_B2C, B2B: ESTAGIOS_B2B}
TERMINAL_POR_FUNIL = {B2C: ESTAGIO_TERMINAL_B2C, B2B: ESTAGIO_TERMINAL_B2B}

ESTAGIO_LABELS = {
    "S0": "Lead",
    "S1": "Respondeu",
    "S2": "Foto recebida",
    "S3": "Prévia enviada",
    "S4": "Preço apresentado",
    "S5": "Negociação",
    "S6": "Ganho",
    "SX": "Perdido",
    "P0": "Não abordado",
    "P1": "Msg 1 enviada",
    "P2": "Autorizou",
    "P3": "Proposta apresentada",
    "P4": "Visita agendada",
    "P5": "Consignação assinada",
    "P6": "Primeira reposição",
    "PX": "Descartado",
}

# Estágios que só um humano marca (§3: "manual"). A regra determinística nunca
# os deriva de fatos — ela só os preserva quando já foram marcados.
ESTAGIOS_MANUAIS = frozenset({"S6", "P5", "P6"})

TODOS_ESTAGIOS = frozenset(
    ESTAGIOS_B2C + ESTAGIOS_B2B + (ESTAGIO_TERMINAL_B2C, ESTAGIO_TERMINAL_B2B)
)


def funil_do_estagio(estagio: str) -> str:
    """Funil ao qual um estágio pertence, derivado do prefixo."""
    if estagio in ESTAGIOS_B2C or estagio == ESTAGIO_TERMINAL_B2C:
        return B2C
    if estagio in ESTAGIOS_B2B or estagio == ESTAGIO_TERMINAL_B2B:
        return B2B
    raise ValueError(f"estágio desconhecido: {estagio!r}")


def rank_estagio(estagio: str) -> int:
    """Posição do estágio no funil, para comparar avanço.

    Terminal (`SX`/`PX`) devolve `-1`: sair do funil não é avançar nele. Quem
    precisa saber se a conversa terminou pergunta `is_terminal`, não compara
    rank — comparar rank de terminal levaria "perdido" a parecer regressão a
    partir de qualquer estágio, e §3 proíbe regressão.
    """
    if estagio in (ESTAGIO_TERMINAL_B2C, ESTAGIO_TERMINAL_B2B):
        return -1
    for funil, estagios in ESTAGIOS_POR_FUNIL.items():
        if estagio in estagios:
            return estagios.index(estagio)
    raise ValueError(f"estágio desconhecido: {estagio!r}")


def is_terminal(estagio: str) -> bool:
    return estagio in (ESTAGIO_TERMINAL_B2C, ESTAGIO_TERMINAL_B2B)


def estagio_label(estagio: str) -> str:
    return ESTAGIO_LABELS.get(estagio, estagio)


# Estágios que a §6 chama de "lead mais caro de perder" — o cliente já pagou
# um custo (mandou a foto) ou o lojista já autorizou. A fila os prioriza.
ESTAGIOS_CAROS_B2C = frozenset({"S2", "S3"})
ESTAGIOS_CAROS_B2B = frozenset({"P2", "P3"})


# --------------------------------------------------------------------------
# Objeções (§4) — lista fechada
# --------------------------------------------------------------------------

OBJECAO_PRECO = "preco"
OBJECAO_FRETE = "frete"
OBJECAO_PRAZO = "prazo"
OBJECAO_CONFIANCA = "confianca"
OBJECAO_MOMENTO = "momento"
OBJECAO_ALTERNATIVA = "alternativa"
OBJECAO_SEM_RESPOSTA = "sem_resposta"
OBJECAO_OUTRO = "outro"

OBJECOES = (
    OBJECAO_PRECO,
    OBJECAO_FRETE,
    OBJECAO_PRAZO,
    OBJECAO_CONFIANCA,
    OBJECAO_MOMENTO,
    OBJECAO_ALTERNATIVA,
    OBJECAO_SEM_RESPOSTA,
    OBJECAO_OUTRO,
)

OBJECAO_LABELS = {
    OBJECAO_PRECO: "Valor da peça alto",
    OBJECAO_FRETE: "Custo ou prazo do envio",
    OBJECAO_PRAZO: "Tempo de produção",
    OBJECAO_CONFIANCA: "Dúvida sobre o resultado",
    OBJECAO_MOMENTO: "Depois / mês que vem",
    OBJECAO_ALTERNATIVA: "Comparou com outra opção",
    OBJECAO_SEM_RESPOSTA: "Sumiu (default no timeout)",
    OBJECAO_OUTRO: "Fora da lista",
}

# `preco` e `frete` separados de propósito (§4): somá-los apagaria exatamente
# a pergunta em aberto sobre o choque de frete. Qualquer relatório que os
# agregue junto precisa ser considerado bug, não conveniência.

# §4: revisão mensal da taxonomia. Fora desta faixa, a taxonomia — não o
# modelo — é o que está errado.
OUTRO_LIMITE_SUPERIOR = 0.15  # acima disso: falta categoria
OUTRO_LIMITE_INFERIOR = 0.03  # abaixo disso: modelo forçando encaixe


def validate_objecao(categoria: str | None) -> str | None:
    """Normaliza uma objeção, rejeitando o que está fora da lista fechada."""
    if categoria is None:
        return None
    normalizada = categoria.strip().lower()
    if not normalizada:
        return None
    if normalizada not in OBJECOES:
        raise ValueError(
            f"objeção fora da taxonomia: {categoria!r} "
            f"(use uma de {', '.join(OBJECOES)})"
        )
    return normalizada


# --------------------------------------------------------------------------
# Temperaturas (§5)
# --------------------------------------------------------------------------

QUENTE = "quente"
MORNO = "morno"
ESFRIANDO = "esfriando"
FRIO = "frio"
ENCERRADO = "encerrado"

TEMPERATURAS = (QUENTE, MORNO, ESFRIANDO, FRIO, ENCERRADO)

TEMPERATURA_LABELS = {
    QUENTE: "QUENTE",
    MORNO: "MORNO",
    ESFRIANDO: "ESFRIANDO",
    FRIO: "FRIO",
    ENCERRADO: "ENCERRADO",
}


# --------------------------------------------------------------------------
# Bola (§5) — quem falou por último, o sinal de maior peso
# --------------------------------------------------------------------------

BOLA_CAMU = "camu"      # o cliente falou por último; a resposta é dívida nossa
BOLA_CLIENTE = "cliente"  # a Camu falou por último; esperamos o cliente
BOLAS = (BOLA_CAMU, BOLA_CLIENTE)


# --------------------------------------------------------------------------
# Limites operacionais
# --------------------------------------------------------------------------

# §6: teto rígido, replicado como CHECK constraint em `db.ensure_schema` —
# aqui é só a constante compartilhada, não a garantia. A garantia é o banco.
MAX_FOLLOWUPS = 2

# §6: "uma lista de no máximo 10 nomes por dia".
FILA_TAMANHO_MAXIMO = 10

# §3: timeout do B2C.
DIAS_ATE_PERDIDO_B2C = 14

# §5: fronteiras da classificação de temperatura.
HORAS_QUENTE = 6
HORAS_MORNO = 48
DIAS_ESFRIANDO = 5
