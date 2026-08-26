"""Configuração por ambiente. Sem segredo no repositório."""

from __future__ import annotations

import os
from pathlib import Path

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
