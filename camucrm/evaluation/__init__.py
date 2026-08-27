"""Ground truth e eval (§7) — a parte que quase sempre falta."""

from .dataset import (
    TAMANHO_MINIMO,
    ConversaRotulada,
    DatasetInvalidoError,
    avisos_de_tamanho,
    carregar,
    validar_entrada,
)
from .runner import META_FALSOS_POSITIVOS, META_FATOS, META_OBJECAO, RelatorioEval, rodar

__all__ = [
    "TAMANHO_MINIMO",
    "ConversaRotulada",
    "DatasetInvalidoError",
    "META_FALSOS_POSITIVOS",
    "META_FATOS",
    "META_OBJECAO",
    "RelatorioEval",
    "avisos_de_tamanho",
    "carregar",
    "rodar",
    "validar_entrada",
]
