"""Configuração por ambiente. Sem segredo no repositório."""

from __future__ import annotations

import os
from pathlib import Path


def _carregar_env() -> None:
    """Carrega `.env` da raiz do projeto, sem sobrescrever o ambiente.

    Variável já exportada no shell vence o arquivo — é o que permite apontar
    um comando para outro banco (`CAMU_DB_DSN=... camucrm fila`) sem editar
    nada. `python-dotenv` ausente não é erro: em produção as variáveis vêm do
    ambiente do container, não de arquivo.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - depende do ambiente
        return
    raiz = Path(__file__).resolve().parent.parent / ".env"
    if raiz.exists():
        load_dotenv(raiz, override=False)


_carregar_env()

ENV_DSN = "CAMU_DB_DSN"
ENV_PLAYBOOK = "CAMU_PLAYBOOK"
ENV_OPERADOR = "CAMU_OPERADOR"

DSN_PADRAO = "postgresql://camu:camu@localhost:5433/camucrm"

# §10: o rascunho usa `06-playbooks/petshops-b2b.md` como referência de tom.
# O caminho é configurável porque o playbook vive no repositório de operação,
# não neste.
PLAYBOOK_PADRAO = "docs/playbook-tom.md"


def dsn() -> str:
    return os.getenv(ENV_DSN, DSN_PADRAO)


def operador() -> str:
    """Quem está operando — vai para `correcoes.por` e para `aprovado_por`.

    Sem valor padrão de propósito: §1 diz que quem envia é humano, sempre, e
    um padrão do tipo "sistema" transformaria a auditoria em ficção.
    """
    return os.getenv(ENV_OPERADOR, "").strip()


def playbook() -> str | None:
    """Texto do playbook de tom, se existir."""
    caminho = Path(os.getenv(ENV_PLAYBOOK, PLAYBOOK_PADRAO))
    if caminho.exists():
        return caminho.read_text(encoding="utf-8")
    return None
