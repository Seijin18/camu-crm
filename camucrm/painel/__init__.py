"""Painel web de leitura do camu-crm (§13, antecipado — change `painel-leitura`).

Só leitura neste change: mostra o que `pipeline.recalcular(persistir=False)`
já produz, sem gravar nada. Bind `127.0.0.1` por padrão, processo próprio, sem
credencial de transporte — a mesma propriedade que `webhook._processar` já
tem para ingestão: um serviço que só olha não pode, por construção, disparar
um envio.

SSE (atualização automática) é o change 2; marcos, correções, rascunho e
resumo (as rotas de escrita) são os changes 3, 4 e 5.
"""

from .server import PORTA_PADRAO, app, servir

__all__ = ["app", "servir", "PORTA_PADRAO"]
