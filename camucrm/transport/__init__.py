"""Transporte: a fronteira única de leitura e envio (§11)."""

from .base import (
    Destinatario,
    EnvioNaoAutorizadoError,
    EventoRecebido,
    ResultadoEnvio,
    Transporte,
    TransporteError,
)
from .console import ConsoleTransporte

__all__ = [
    "ConsoleTransporte",
    "Destinatario",
    "EnvioNaoAutorizadoError",
    "EventoRecebido",
    "ResultadoEnvio",
    "Transporte",
    "TransporteError",
    "criar_transporte",
    "listar_instancias_evolution",
]


def criar_transporte(
    nome: str | None = None, *, para_envio: bool = True, instancia: str | None = None
):
    """Fábrica por nome, lendo credenciais do ambiente.

    O padrão é `console` (dry-run) e isso é deliberado: um deploy sem
    `CAMU_TRANSPORTE=evolution` explícito não manda mensagem nenhuma.

    `para_envio=False` monta um transporte **só de recepção**: nenhuma
    credencial é lida nem exigida, porque `receber` é parsing puro. É o modo
    do webhook — um processo que nunca teve a chave não envia por acidente
    nem sob ataque, o que é mais forte que prometer que não vai enviar.

    `instancia` (change `escolher-instancia-no-envio-prospeccao`): quando
    preenchida, sobrepõe `EVOLUTION_INSTANCE` do ambiente — é o número que o
    operador escolheu no popup de envio de prospecção. `None`/vazio mantém o
    comportamento de antes (a instância única do `.env`).
    """
    import os

    escolhido = (nome or os.getenv("CAMU_TRANSPORTE", "console")).strip().lower()
    if escolhido == "console":
        return ConsoleTransporte()
    if escolhido == "evolution":
        from .evolution import EvolutionTransporte

        if not para_envio:
            return EvolutionTransporte()

        base_url = os.getenv("EVOLUTION_API_BASE_URL", "").strip()
        api_key = os.getenv("EVOLUTION_API_KEY", "").strip()
        instancia = (instancia or "").strip() or os.getenv("EVOLUTION_INSTANCE", "").strip()
        faltando = [
            nome_var
            for nome_var, valor in (
                ("EVOLUTION_API_BASE_URL", base_url),
                ("EVOLUTION_API_KEY", api_key),
                ("EVOLUTION_INSTANCE", instancia),
            )
            if not valor
        ]
        if faltando:
            raise RuntimeError(
                f"CAMU_TRANSPORTE=evolution exige {', '.join(faltando)}"
            )
        return EvolutionTransporte(base_url, api_key, instancia)
    raise RuntimeError(f"transporte desconhecido: {escolhido!r} (use console ou evolution)")


def listar_instancias_evolution():
    """Números cadastrados na Evolution API — change
    `escolher-instancia-no-envio-prospeccao`.

    Lê `EVOLUTION_API_BASE_URL`/`_API_KEY` do ambiente (não exige
    `EVOLUTION_INSTANCE`: a pergunta é "quais existem"). Levanta
    `TransporteError` quando falta credencial ou a API não responde — quem
    chama (`camucrm/painel/envio.py`) traduz para 502.
    """
    import os

    from .evolution import EvolutionTransporte

    return EvolutionTransporte(
        os.getenv("EVOLUTION_API_BASE_URL", "").strip(),
        os.getenv("EVOLUTION_API_KEY", "").strip(),
    ).listar_instancias()
