"""Ground truth e eval (§7) — a parte que quase sempre falta."""

from .dataset import ConversaRotulada, DatasetInvalidoError, carregar
from .runner import META_FATOS, META_OBJECAO, RelatorioEval, rodar

__all__ = [
    "ConversaRotulada",
    "DatasetInvalidoError",
    "META_FATOS",
    "META_OBJECAO",
    "RelatorioEval",
    "carregar",
    "rodar",
]
