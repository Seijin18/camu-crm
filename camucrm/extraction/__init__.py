"""Extração de fatos: a única coisa que o LLM decide (§1, §2)."""

from .contract import (
    FATOS_BOOLEANOS,
    ContratoInvalidoError,
    Corpus,
    Democao,
    Extracao,
    build_corpus,
    extracao_vazia,
    merge,
    validar,
)
from .extractor import Extrator, ResultadoExtracao

__all__ = [
    "ContratoInvalidoError",
    "Corpus",
    "Democao",
    "Extracao",
    "Extrator",
    "FATOS_BOOLEANOS",
    "ResultadoExtracao",
    "build_corpus",
    "extracao_vazia",
    "merge",
    "validar",
]
