"""Fábrica de provedor de LLM.

O modelo tem um trabalho só neste sistema (§1): responder perguntas factuais
fechadas sobre um texto, e rascunhar. Ele nunca decide estágio, temperatura,
prioridade ou envio. Por isso a interface é mínima — um `completar` — e trocar
de provedor não toca nenhuma regra.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Protocol

logger = logging.getLogger("camucrm.llm")

ENV_PROVIDER = "CAMU_LLM_PROVIDER"
ENV_GEMINI_API_KEY = "GEMINI_API_KEY"
ENV_GEMINI_MODEL = "CAMU_GEMINI_MODEL"

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class LlmIndisponivelError(RuntimeError):
    """Provedor fora do ar, sem cota, ou mal configurado.

    Quem chama trata isso como "nenhum fato novo" e não como erro fatal: uma
    extração que falhou deixa o estágio onde estava, que é sempre o lado
    seguro do erro (§7: falso positivo de avanço = 0).
    """


class LlmClient(Protocol):
    nome: str

    def completar(self, system: str, user: str, *, json_estrito: bool = False) -> str: ...


class FakeLlm:
    """Cliente determinístico para testes e para o eval offline.

    Devolve respostas de uma fila pré-carregada. Não é mock de biblioteca: é
    um cliente de verdade que satisfaz o protocolo, o que mantém os testes
    exercitando o caminho real de parsing e validação.
    """

    nome = "fake"

    def __init__(self, respostas: list[str] | None = None):
        self.respostas = list(respostas or [])
        self.chamadas: list[tuple[str, str]] = []

    def completar(self, system: str, user: str, *, json_estrito: bool = False) -> str:
        self.chamadas.append((system, user))
        if not self.respostas:
            return json.dumps({})
        return self.respostas.pop(0)


class GeminiLlm:
    """Adaptador Gemini via `google-genai`.

    `json_estrito` pede `response_mime_type=application/json`: o contrato da
    §2 é um objeto JSON, e deixar o modelo escolher o invólucro só cria
    trabalho de parsing que pode falhar de formas novas a cada versão.
    """

    nome = "gemini"

    def __init__(self, api_key: str, modelo: str | None = None):
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise LlmIndisponivelError(
                "pacote google-genai não instalado (pip install google-genai)"
            ) from exc
        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.modelo = modelo or os.getenv(ENV_GEMINI_MODEL, DEFAULT_GEMINI_MODEL)

    def completar(self, system: str, user: str, *, json_estrito: bool = False) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            # Extração é leitura, não criação: temperatura alta aqui vira
            # fato inventado, que é o modo de falha mais caro (§2).
            temperature=0.0,
            response_mime_type="application/json" if json_estrito else "text/plain",
        )
        try:
            resposta = self._client.models.generate_content(
                model=self.modelo, contents=user, config=config
            )
        except Exception as exc:  # noqa: BLE001 - o SDK levanta tipos variados
            raise LlmIndisponivelError(f"Gemini indisponível: {exc}") from exc
        texto = getattr(resposta, "text", None)
        if not texto:
            raise LlmIndisponivelError("Gemini devolveu resposta vazia")
        return texto


def criar_llm(provider: str | None = None) -> LlmClient:
    escolhido = (provider or os.getenv(ENV_PROVIDER, "gemini")).strip().lower()
    if escolhido == "fake":
        return FakeLlm()
    if escolhido == "gemini":
        chave = os.getenv(ENV_GEMINI_API_KEY, "").strip()
        if not chave:
            raise LlmIndisponivelError(
                f"{ENV_GEMINI_API_KEY} não configurado "
                f"(use {ENV_PROVIDER}=fake para rodar sem LLM)"
            )
        return GeminiLlm(chave)
    raise RuntimeError(f"provedor de LLM desconhecido: {escolhido!r}")
