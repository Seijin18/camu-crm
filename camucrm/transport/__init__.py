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
]


def criar_transporte(nome: str | None = None, *, para_envio: bool = True):
    """Fábrica por nome, lendo credenciais do ambiente.

    O padrão é `console` (dry-run) e isso é deliberado: um deploy sem
    `CAMU_TRANSPORTE=evolution` explícito não manda mensagem nenhuma.

    `para_envio=False` monta um transporte **só de recepção**: nenhuma
    credencial é lida nem exigida, porque `receber` é parsing puro. É o modo
    do webhook — um processo que nunca teve a chave não envia por acidente
    nem sob ataque, o que é mais forte que prometer que não vai enviar.
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
        instancia = os.getenv("EVOLUTION_INSTANCE", "").strip()
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
