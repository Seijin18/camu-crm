"""Parser do `.txt` de "Exportar conversa" do WhatsApp (change
`importacao-conversas-whatsapp`).

Existe porque parte do contato com clientes/petshops deixou de acontecer
exclusivamente pelo número da Camu ligado à Evolution API (`camucrm/
webhook.py`) — conversa por número pessoal ou outro número comercial não
tem webhook nenhum gerando evento. O WhatsApp já resolve a exportação
("Exportar conversa" → `.txt`); este módulo resolve o lado de importação,
convertendo esse texto para a MESMA forma de `registro` que
`camucrm/backfill.py::importar_conversas` já consome — nenhuma duplicação
de lógica de persistência/idempotência.

Módulo puro (§1/CLAUDE.md, mesma categoria de `camucrm/prospeccao.py`):
sem I/O, sem SQL, sem chamada a `camucrm.llm`/`camucrm.transport`. Só texto
entra, estrutura sai.

Duas exceções deliberadas, nunca importação parcial silenciosa:

- `ExportacaoDeGrupoError`: exportação de grupo (mais de dois remetentes
  distintos nas linhas reconhecidas, ou linha de sistema de
  entrada/saída de participante) não mapeia para `contato` (uma
  pessoa/empresa por conversa, §9) — arquivo inteiro recusado.
- `NomeOperadorNaoEncontradoError`: o `.txt` não marca direção (`in`/
  `out`), só nome do remetente por linha. Se `nosso_nome` não aparece em
  nenhuma linha reconhecida, a importação falha em vez de assumir uma
  direção padrão — a auditoria de 2026-08 já registrou "evidência não
  distingue lado (cliente vs. Camu)" como achado crítico em outro ponto do
  pipeline (`literalidade-e-idempotencia-da-extracao`); este caminho novo
  não pode reintroduzir o mesmo modo de falha.

Limitação aceita e registrada (`design.md` do change): o `.txt` exportado
não carrega fuso horário — cada timestamp é interpretado como UTC. A ORDEM
relativa entre mensagens continua correta (é o que `rules/sinais.py`
precisa), só a hora do relógio pode estar deslocada pelo fuso real do
aparelho que exportou. Diferente do backfill de dump genérico (§8), que
descarta timestamp por completo, aqui o timestamp é real — só
potencialmente deslocado em horas, nunca inventado no momento do
processamento.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .rules.sinais import ENTRADA, SAIDA

# Caracteres invisíveis que o WhatsApp costuma intercalar no texto exportado
# (marca de direção LTR antes da data, espaços não separáveis antes de
# AM/PM) — removidos antes de qualquer regex, nunca usados para decidir
# formato.
_CARACTERES_INVISIVEIS = ("‎", "﻿", " ", " ")

# Duas variantes de prefixo — Android (sem colchete) e iOS (com colchete).
# Vírgula entre data e hora é opcional (varia por versão/locale); hora
# aceita segundos e AM/PM opcionais.
_HORA = r"\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp][Mm])?"
_PREFIXO_SEM_COLCHETE = re.compile(
    rf"^(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}),?\s+({_HORA})\s+[-–]\s(.*)$"
)
_PREFIXO_COM_COLCHETE = re.compile(
    rf"^\[(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}),\s+({_HORA})\]\s?(.*)$"
)
_HORA_PARTES = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?$")

# "Nome: texto" — nome sem `:` (o `:` é o delimitador do formato), até 64
# caracteres. Linha de sistema (aviso de criptografia, mensagem apagada,
# entrada/saída de grupo) nunca tem esse prefixo — é frase corrida.
_REMETENTE_TEXTO = re.compile(r"^([^:\n]{1,64}?):\s?(.*)$")

#: Placeholders de mídia reconhecidos (pt-BR e inglês, versões antiga e
#: nova do app) → marcador textual fixo. Direção-neutro (`[áudio]`, não
#: `[áudio recebido]` como em `transport/evolution.py`): aqui a mídia pode
#: ser de qualquer direção, diferente do webhook que só vê inbound. Nunca é
#: evidência literal de fato nenhum (§2) — mesma garantia de
#: `_MARCADORES` em `evolution.py`.
_PLACEHOLDERS_MIDIA: tuple[tuple[str, str], ...] = (
    ("imagem", "[imagem]"),
    ("image", "[imagem]"),
    ("vídeo", "[vídeo]"),
    ("video", "[vídeo]"),
    ("áudio", "[áudio]"),
    ("audio", "[áudio]"),
    ("figurinha", "[figurinha]"),
    ("sticker", "[figurinha]"),
    ("gif", "[gif]"),
    ("documento", "[documento]"),
    ("document", "[documento]"),
    ("contato", "[contato]"),
    ("contact", "[contato]"),
    ("localização", "[localização]"),
    ("location", "[localização]"),
)
_MARCADOR_MIDIA_GENERICO = "[mídia]"

#: Frases (substring, minúsculo) que identificam linha de sistema comum —
#: ignorada e contada, nunca vira mensagem nem erro.
_FRASES_SISTEMA = (
    "criptografia de ponta a ponta",
    "mensagens e as chamadas são protegidas",
    "end-to-end encrypted",
    "esta mensagem foi apagada",
    "você apagou esta mensagem",
    "this message was deleted",
    "you deleted this message",
    "mudou para um novo número",
    "changed to a new number",
    "número de telefone mudou",
)

#: Frases que identificam gerência de grupo — além de ignoradas/contadas,
#: disparam `ExportacaoDeGrupoError` (Decisão 5, `design.md`).
_FRASES_SISTEMA_GRUPO = (
    "adicionou",
    "added",
    " saiu",
    " left",
    "removeu",
    "removed",
    "criou o grupo",
    "created group",
    "alterou o nome do grupo",
    "changed the subject",
    "alterou a descrição do grupo",
    "changed this group's description",
    "alterou o ícone do grupo",
    "changed this group's icon",
    "entrou usando o link de convite",
    "joined using this group's invite link",
    "é agora um admin",
    "is now an admin",
)


class ExportacaoDeGrupoError(ValueError):
    """Arquivo identificado como exportação de grupo — recusado por inteiro."""


class NomeOperadorNaoEncontradoError(ValueError):
    """`nosso_nome` não aparece em nenhuma linha reconhecida do arquivo."""


@dataclass
class ParseResultado:
    """Saída do parser — `mensagens` já na forma que `backfill.
    importar_conversas` consome (`direcao`/`texto`/`enviada_em`)."""

    mensagens: list[dict[str, Any]]
    nome_contato: str | None
    midia_preservada: int = 0
    ignoradas: list[str] = field(default_factory=list)


def parse(texto: str, *, nosso_nome: str) -> ParseResultado:
    """Converte o `.txt` exportado em `ParseResultado`.

    `nosso_nome` é o nome que aparece no export do lado de quem está
    respondendo pela Camu (o nome salvo no WhatsApp de quem exportou) —
    obrigatório, decide direção por correspondência exata (após normalizar
    espaço e maiúscula/minúscula). Ver `NomeOperadorNaoEncontradoError` e
    `ExportacaoDeGrupoError` acima para as duas falhas explícitas.

    Linha em branco é a única omissão silenciosa deliberada: carrega zero
    informação, e o WhatsApp não produz linha em branco dentro do texto de
    uma mensagem real (linha de continuação de mensagem multi-linha nunca
    vem vazia). Qualquer outra linha não reconhecida entra em `ignoradas`.
    """
    nosso_nome_norm = _normalizar_nome(nosso_nome)
    if not nosso_nome_norm:
        raise ValueError("nosso_nome não pode ser vazio")

    mensagens: list[dict[str, Any]] = []
    ignoradas: list[str] = []
    midia_preservada = 0
    remetentes_norm: set[str] = set()
    nome_contato: str | None = None
    grupo_detectado = False
    indice_mensagem_aberta: int | None = None

    for linha_bruta in texto.splitlines():
        linha = _sem_invisiveis(linha_bruta).rstrip()
        if not linha.strip():
            continue

        casamento = _PREFIXO_COM_COLCHETE.match(linha) or _PREFIXO_SEM_COLCHETE.match(
            linha
        )
        if casamento is None:
            # Linha de continuação de uma mensagem multi-linha — só se
            # houver uma mensagem real aberta para anexar; senão é linha
            # órfã, reportada, nunca engolida.
            if indice_mensagem_aberta is not None:
                mensagens[indice_mensagem_aberta]["texto"] += "\n" + linha
            else:
                ignoradas.append(linha_bruta)
            continue

        data, hora, resto = casamento.groups()
        try:
            momento = _parse_momento(data, hora)
        except ValueError:
            ignoradas.append(linha_bruta)
            indice_mensagem_aberta = None
            continue

        remetente_m = _REMETENTE_TEXTO.match(resto)
        if remetente_m is None:
            resto_norm = resto.strip().lower()
            if any(frase in resto_norm for frase in _FRASES_SISTEMA_GRUPO):
                grupo_detectado = True
            elif not any(frase in resto_norm for frase in _FRASES_SISTEMA):
                ignoradas.append(linha_bruta)
            indice_mensagem_aberta = None
            continue

        remetente, conteudo = remetente_m.groups()
        remetente_norm = _normalizar_nome(remetente)
        remetentes_norm.add(remetente_norm)
        if remetente_norm != nosso_nome_norm and nome_contato is None:
            nome_contato = remetente.strip()

        marcador = _marcador_midia(conteudo)
        if marcador is not None:
            midia_preservada += 1
        texto_final = marcador if marcador is not None else conteudo

        direcao = SAIDA if remetente_norm == nosso_nome_norm else ENTRADA
        mensagens.append(
            {"direcao": direcao, "texto": texto_final, "enviada_em": momento}
        )
        indice_mensagem_aberta = len(mensagens) - 1

    outros_remetentes = remetentes_norm - {nosso_nome_norm}
    if grupo_detectado or len(outros_remetentes) > 1:
        raise ExportacaoDeGrupoError(
            "arquivo parece ser exportação de um grupo do WhatsApp — este "
            "importador só aceita conversa 1:1"
        )
    if nosso_nome_norm not in remetentes_norm:
        raise NomeOperadorNaoEncontradoError(
            f"nome_operador {nosso_nome!r} não aparece como remetente de "
            "nenhuma linha reconhecida do arquivo"
        )

    return ParseResultado(
        mensagens=mensagens,
        nome_contato=nome_contato,
        midia_preservada=midia_preservada,
        ignoradas=ignoradas,
    )


def _sem_invisiveis(linha: str) -> str:
    for caractere in _CARACTERES_INVISIVEIS:
        linha = linha.replace(caractere, " " if caractere in (" ", " ") else "")
    return linha


def _normalizar_nome(nome: str) -> str:
    return " ".join(nome.split()).casefold()


def _marcador_midia(conteudo: str) -> str | None:
    normalizado = conteudo.strip().lower()
    if "oculta" not in normalizado and "omitted" not in normalizado:
        return None
    for chave, marcador in _PLACEHOLDERS_MIDIA:
        if chave in normalizado:
            return marcador
    return _MARCADOR_MIDIA_GENERICO


def _parse_momento(data: str, hora: str) -> datetime:
    """`data` em `DD/MM/AA` ou `DD/MM/AAAA`; `hora` em `HH:MM[:SS][ AM/PM]`.

    Sem fuso no `.txt` exportado — interpretado como UTC (ver docstring do
    módulo). `ValueError` propaga para o chamador tratar como linha
    ilegível, nunca inventa um momento (`datetime.now()`), diferente do
    fallback de `backfill._momento` — aqui a mensagem tem timestamp
    explícito no arquivo; se não dá para lê-lo, a linha é reportada, não
    silenciosamente redatada com "agora".
    """
    dia_str, mes_str, ano_str = data.split("/")
    dia, mes, ano = int(dia_str), int(mes_str), int(ano_str)
    if ano < 100:
        ano += 2000

    partes = _HORA_PARTES.match(hora.strip())
    if partes is None:
        raise ValueError(f"hora ilegível: {hora!r}")
    h_str, mi_str, s_str, ampm = partes.groups()
    h, mi, s = int(h_str), int(mi_str), int(s_str) if s_str else 0
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0

    return datetime(ano, mes, dia, h, mi, s, tzinfo=timezone.utc)
