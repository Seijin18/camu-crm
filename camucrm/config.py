"""Configuração por ambiente. Sem segredo no repositório."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _rodando_em_teste() -> bool:
    """Se o processo atual é uma execução de testes.

    A suíte NÃO pode ler o `.env` do desenvolvedor. Um teste cujo resultado
    depende da configuração local passa numa máquina e falha noutra, e o modo
    de falha pior é silencioso: `CAMU_DB_DSN` do arquivo apontaria a suíte
    para o banco de produção. Já aconteceu aqui — dois testes de webhook
    começaram a receber 401 porque o `.env` definia `CAMU_WEBHOOK_TOKEN`.
    """
    principal = getattr(sys.modules.get("__main__"), "__file__", "") or ""
    return "unittest" in principal or "pytest" in sys.modules


def _carregar_env() -> None:
    """Carrega `.env` da raiz do projeto, sem sobrescrever o ambiente.

    Variável já exportada no shell vence o arquivo — é o que permite apontar
    um comando para outro banco (`CAMU_DB_DSN=... camucrm fila`) sem editar
    nada. `python-dotenv` ausente não é erro: em produção as variáveis vêm do
    ambiente do container, não de arquivo.
    """
    if _rodando_em_teste():
        return
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
ENV_EVAL_DATASET = "CAMU_EVAL_DATASET"
# Change `prospeccao-b2b-shortlist`: template da mensagem de prospecção B2B,
# path configurável — mesmo padrão de `ENV_PLAYBOOK`.
ENV_MENSAGEM_PROSPECCAO = "CAMU_MENSAGEM_PROSPECCAO"
# Change `ingestao-restrita-por-instancia`: CSV de nomes de instância da
# Evolution API (ex.: número pessoal, número do Felipe) cuja ingestão só
# acompanha telefone já `contato` ou já em `prospeccoes` — ver
# `instancias_restritas()`.
ENV_INSTANCIAS_RESTRITAS = "CAMU_INSTANCIAS_RESTRITAS"

DSN_PADRAO = "postgresql://camu:camu@localhost:5433/camucrm"

# §10: o rascunho usa `06-playbooks/petshops-b2b.md` como referência de tom.
# O caminho é configurável porque o playbook vive no repositório de operação,
# não neste.
PLAYBOOK_PADRAO = "docs/playbook-tom.md"

# Change `prospeccao-b2b-shortlist`: DIFERENTE de `PLAYBOOK_PADRAO` (que é
# referência para o LLM, nunca enviado verbatim), este arquivo é enviado
# literal ao petshop via link de WhatsApp — por isso não tem cabeçalho/prosa
# de documentação como `playbook-tom.md`, só o texto da mensagem com o
# placeholder `{nome}` (`camucrm/prospeccao.py::montar_mensagem`).
MENSAGEM_PROSPECCAO_PADRAO = "docs/mensagem-prospeccao.md"

# §7: as 30 conversas de ground truth. Change `ground-truth-no-painel` —
# permite a suíte de testes apontar para um arquivo temporário sem tocar o
# dataset real, mesmo padrão de `CAMU_PLAYBOOK`.
EVAL_DATASET_PADRAO = "data/eval/conversas.jsonl"


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


def mensagem_prospeccao() -> str | None:
    """Template de mensagem de prospecção B2B (change
    `prospeccao-b2b-shortlist`), se existir. Mesmo padrão de leitura de
    `playbook()` (arquivo + env var), mas o conteúdo aqui é enviado literal
    ao petshop — só `{nome}` é substituído
    (`camucrm/prospeccao.py::montar_mensagem`), nunca injetado num prompt de
    LLM. `None` quando o arquivo não existe: a tela de prospecção mostra que
    não há template configurado, em vez de quebrar.
    """
    caminho = Path(os.getenv(ENV_MENSAGEM_PROSPECCAO, MENSAGEM_PROSPECCAO_PADRAO))
    if caminho.exists():
        return caminho.read_text(encoding="utf-8").strip()
    return None


def instancias_restritas() -> frozenset[str]:
    """Nomes de instância da Evolution API cuja ingestão (`ingest.ingerir`)
    só acompanha telefone já `contato` conhecido ou já presente em
    `prospeccoes` — change `ingestao-restrita-por-instancia`.

    Vazio por padrão: nenhuma instância é restrita, exatamente o
    comportamento de antes deste change. A instância única de hoje (a da
    Camu) nunca precisa entrar aqui — ela é a porta de entrada do funil B2C
    (§12, "cliente iniciou o contato"), e restringi-la pararia de capturar
    lead novo por DM. Esta variável é para as instâncias NOVAS (número
    pessoal, número do Felipe), que só devem acompanhar contato comercial
    já identificado, não qualquer mensagem que chegar.

    Comparação exata, sem normalizar maiúscula/minúscula — nome de
    instância é definido pelo operador no cadastro da Evolution API, não
    texto de usuário sujeito a variação de digitação.
    """
    bruto = os.getenv(ENV_INSTANCIAS_RESTRITAS, "")
    return frozenset(nome.strip() for nome in bruto.split(",") if nome.strip())


def eval_dataset_caminho() -> Path:
    """Caminho do dataset de ground truth (§7).

    O cache de `POST /eval/rodar` (`ultimo_resultado.json`, change
    `ground-truth-no-painel`) vive no mesmo diretório deste arquivo — assim
    um teste que aponta `CAMU_EVAL_DATASET` para um arquivo temporário nunca
    grava o cache junto do dataset real por acidente.
    """
    return Path(os.getenv(ENV_EVAL_DATASET, EVAL_DATASET_PADRAO))
