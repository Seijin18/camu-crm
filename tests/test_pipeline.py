"""`pipeline.py::_trilha_de_backfill` (§8): trilha considera origem e destino.

Change `backfill-seguro-para-reexecucao`: pular uma transição só porque o
`para` já tem QUALQUER evento gravado (comportamento antigo) descarta uma
trilha legítima que chega ao mesmo destino por uma origem (`de`) diferente da
já registrada — canto raro (funil trocado + backfill reexecutado), mas real o
bastante para não silenciar (ver `openspec/changes/backfill-seguro-para-
reexecucao/specs/backfill-seguro-para-reexecucao/spec.md`).
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm.pipeline import _trilha_de_backfill  # noqa: E402
from camucrm.rules.sinais import Mensagem, construir_sinais  # noqa: E402

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _sinais(msgs=(), **kwargs):
    return construir_sinais(msgs, agora=AGORA, **kwargs)


class TesteTrilhaDeBackfillConsideraOrigemEDestino(unittest.TestCase):
    def test_nao_pula_transicao_legitima_com_de_diferente_do_ja_registrado(self):
        db = FakeDatabase()
        contato = db.upsert_contato("5511999991111", nome="Carla", tipo="b2c")
        conversa = db.get_or_create_conversa(contato.id, funil="b2c")

        # Uma trilha anterior (funil trocado + backfill reexecutado, ou
        # qualquer reprocessamento passado) já registrou uma transição que
        # chega em S2 vinda de "S9" — um `de` que a trilha ATUAL não
        # sustenta para chegar em S2.
        db.gravar_evento_estagio(conversa.id, "S9", "S2", origem="backfill")

        # Fatos e sinais desta rodada sustentam a trilha normal S0->S1->S2:
        # cliente mandou mensagem espontânea e depois a foto do pet.
        fatos = {"foto_pet_recebida": True}
        sinais = _sinais([Mensagem("in", AGORA - timedelta(hours=1))])

        estagio_final, transicoes = _trilha_de_backfill(db, conversa, fatos, sinais)

        pares = {(t.de, t.para) for t in transicoes}
        # (S1, S2) é uma transição DIFERENTE de (S9, S2) — pular por `para`
        # sozinho (comportamento antigo, `estagios_registrados`) descartaria
        # esta trilha achando que S2 "já estava registrado". Considerar o
        # par inteiro preserva o registro da trilha legítima desta rodada.
        self.assertIn(("S1", "S2"), pares)
        self.assertEqual(estagio_final, "S2")

    def test_pula_transicao_com_mesmo_par_ja_registrado(self):
        """Continua pulando (idempotência, §2) quando o PAR já bate certinho
        — não é regressão do comportamento antigo, só refinamento da
        checagem."""
        db = FakeDatabase()
        contato = db.upsert_contato("5511999990000", nome="Bea", tipo="b2c")
        conversa = db.get_or_create_conversa(contato.id, funil="b2c")

        db.gravar_evento_estagio(conversa.id, "S0", "S1", origem="backfill")
        db.gravar_evento_estagio(conversa.id, "S1", "S2", origem="backfill")

        fatos = {"foto_pet_recebida": True}
        sinais = _sinais([Mensagem("in", AGORA - timedelta(hours=1))])

        _, transicoes = _trilha_de_backfill(db, conversa, fatos, sinais)

        self.assertEqual(transicoes, [])


if __name__ == "__main__":
    unittest.main()
