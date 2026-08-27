"""Camada pura da prospecção B2B (change `prospeccao-b2b-shortlist`):
normalização de telefone, nome curto e o link de WhatsApp por clique.

Sem I/O, sem SQL, sem chamada a `camucrm.llm`/`camucrm.transport` — é por
isso que este módulo é testável sem banco e sem rede. `camucrm/db.py` chama
`normalizar_telefone_br` na importação (para casar o hash com
`contatos.telefone_hash`, ver `design.md` do change); `camucrm/painel/
views.py` chama `montar_mensagem`/`link_whatsapp` para montar a resposta de
`GET /api/prospeccao`.

Decisão 2 do `design.md`: o "disparo" é o link `api.whatsapp.com/send`,
nunca uma chamada de servidor — o clique do operador abre o link com a
mensagem pré-preenchida, e o envio de fato acontece dentro do WhatsApp, um
ato humano. Este módulo só monta texto e URL; nunca chama nada, nunca
importa `camucrm.transport`.
"""

from __future__ import annotations

from urllib.parse import quote


def normalizar_telefone_br(telefone: str | None) -> str | None:
    """Dígitos do telefone, com código do país (55) — o MESMO formato que
    `contatos.telefone` carrega quando o número responde de verdade pelo
    WhatsApp (`transport.evolution._so_digitos` do `remoteJid`, que já vem
    com código do país).

    A planilha do usuário traz telefone em formato local, sem código do
    país (`"(12) 98157-5051"` -> 11 dígitos). Sem esta normalização,
    `db.hash_telefone` produziria um hash diferente do que
    `contatos.telefone_hash` grava para o mesmo número real — e a detecção
    de conversão (`LEFT JOIN` por `telefone_hash`, design.md) nunca casaria
    nada.

    Devolve `None` quando não há dígitos suficientes para um telefone
    brasileiro válido — nunca lança: quem chama (`db.importar_prospeccoes`)
    precisa reportar a linha como inválida, não abortar a planilha inteira
    (requirement "Importação nunca descarta linha em silêncio").
    """
    digitos = "".join(c for c in (telefone or "") if c.isdigit())
    if len(digitos) in (10, 11):
        return "55" + digitos
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        return digitos
    return None


def nome_curto(nome: str | None) -> str:
    """Nome curto do petshop para a mensagem: corta no primeiro `|` ou
    ` - ` (nomes da planilha costumam carregar slogan depois de um desses
    separadores, ex. `"NetCão Pet Shop | Desde 1997..."`) e normaliza para
    minúsculas — mesma lógica do pseudocódigo original do usuário
    (`design.md`).
    """
    base = (nome or "").strip()
    corte = len(base)
    for separador in ("|", " - "):
        indice = base.find(separador)
        if indice != -1:
            corte = min(corte, indice)
    return base[:corte].strip().lower()


def montar_mensagem(template: str, nome: str) -> str:
    """Substitui `{nome}` pelo nome curto do petshop. Template fixo, sem
    LLM (requirement "Mensagem é template fixo, não geração por LLM") —
    `camucrm/painel/api.py` nunca importa `camucrm.llm` neste caminho.
    """
    return template.replace("{nome}", nome_curto(nome))


def link_whatsapp(telefone_normalizado: str, mensagem: str) -> str:
    """Link `api.whatsapp.com/send` com o texto já url-encoded
    (`urllib.parse.quote`, que percent-encoda acento/emoji e escapa o
    resto) — decisão 2 do `design.md`: o clique abre o link, o envio de
    fato é um ato humano dentro do WhatsApp, nunca uma chamada de servidor
    deste sistema.
    """
    return f"https://api.whatsapp.com/send/?phone={telefone_normalizado}&text={quote(mensagem)}"
