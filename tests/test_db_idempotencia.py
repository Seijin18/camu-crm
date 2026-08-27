"""Idempotência de `db.py` (§2, change `literalidade-e-idempotencia-da-extracao`).

Dois invariantes que sustentam #1 e #2 do CLAUDE.md, cobertos aqui sem
Postgres (via `FakeDatabase`, que espelha o `GREATEST` e o `ON CONFLICT DO
NOTHING` do banco real — ver docstring de `tests/fakes.py`):

1. O watermark `ultima_mensagem_processada_id` nunca regride.
2. `gravar_objecao` nunca duplica a mesma (conversa, categoria, estágio,
   trecho).

A garantia real — que o banco recusa mesmo sob concorrência de verdade, não
só que o fake concorda consigo mesmo — está em
`tests/integration/test_idempotencia_extracao_postgres.py`.
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402


class TesteWatermarkNuncaRegride(unittest.TestCase):
    def test_escrita_com_valor_menor_nao_regride(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        db.atualizar_estado_conversa(conversa.id, ultima_mensagem_processada_id=10)
        self.assertEqual(conversa.ultima_mensagem_processada_id, 10)

        # Processamento fora de ordem (webhook e `camucrm extrair` juntos, ou
        # dois webhooks quase simultâneos) tenta gravar um id menor — o
        # watermark tem que continuar no maior já visto.
        db.atualizar_estado_conversa(conversa.id, ultima_mensagem_processada_id=5)
        self.assertEqual(conversa.ultima_mensagem_processada_id, 10)

    def test_escrita_com_valor_maior_avanca(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        db.atualizar_estado_conversa(conversa.id, ultima_mensagem_processada_id=10)
        db.atualizar_estado_conversa(conversa.id, ultima_mensagem_processada_id=15)
        self.assertEqual(conversa.ultima_mensagem_processada_id, 15)

    def test_primeira_escrita_a_partir_de_none_vale(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        self.assertIsNone(conversa.ultima_mensagem_processada_id)
        db.atualizar_estado_conversa(conversa.id, ultima_mensagem_processada_id=3)
        self.assertEqual(conversa.ultima_mensagem_processada_id, 3)

    def test_outros_campos_nao_sao_afetados_pelo_greatest(self):
        """O `GREATEST` é só do watermark — `estagio`/`temperatura` continuam
        gravando o valor passado, sem comparação."""
        db = FakeDatabase()
        conversa = db.criar_conversa(estagio="S0")
        db.atualizar_estado_conversa(conversa.id, estagio="S2", temperatura="quente")
        self.assertEqual(conversa.estagio, "S2")
        self.assertEqual(conversa.temperatura, "quente")


class TesteObjecaoIdempotente(unittest.TestCase):
    def test_mesma_objecao_duas_vezes_nao_duplica(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        em = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

        db.gravar_objecao(
            conversa.id, "preco", estagio="S2", trecho="achei caro", em=em
        )
        db.gravar_objecao(
            conversa.id, "preco", estagio="S2", trecho="achei caro", em=em
        )
        self.assertEqual(len(db.objecoes), 1)

    def test_objecao_sem_trecho_duas_vezes_tambem_deduplica(self):
        """`trecho IS NULL` não pode escapar do dedupe — por isso o índice
        real usa `md5(coalesce(trecho, ''))`."""
        db = FakeDatabase()
        conversa = db.criar_conversa()
        db.gravar_objecao(conversa.id, "sem_resposta", estagio="S1")
        db.gravar_objecao(conversa.id, "sem_resposta", estagio="S1")
        self.assertEqual(len(db.objecoes), 1)

    def test_categoria_diferente_nao_deduplica(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        db.gravar_objecao(conversa.id, "preco", estagio="S2", trecho="achei caro")
        db.gravar_objecao(conversa.id, "frete", estagio="S2", trecho="achei caro")
        self.assertEqual(len(db.objecoes), 2)

    def test_estagio_diferente_nao_deduplica(self):
        """A mesma objeção repetida em outro estágio é informação nova para
        a §4 (frete reclamado de novo em S4 é distinto de S2)."""
        db = FakeDatabase()
        conversa = db.criar_conversa()
        db.gravar_objecao(conversa.id, "preco", estagio="S2", trecho="achei caro")
        db.gravar_objecao(conversa.id, "preco", estagio="S4", trecho="achei caro")
        self.assertEqual(len(db.objecoes), 2)

    def test_conversa_diferente_nao_deduplica(self):
        db = FakeDatabase()
        c1 = db.criar_conversa()
        c2 = db.criar_conversa()
        db.gravar_objecao(c1.id, "preco", estagio="S2", trecho="achei caro")
        db.gravar_objecao(c2.id, "preco", estagio="S2", trecho="achei caro")
        self.assertEqual(len(db.objecoes), 2)


if __name__ == "__main__":
    unittest.main()
