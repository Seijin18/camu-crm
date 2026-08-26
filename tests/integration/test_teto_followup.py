"""Prova, contra Postgres real, que o teto de 2 follow-ups é do banco (§6).

§6: "O sistema deve tornar isso impossível de furar — não é preferência, é
preservação de chip e de marca. Implementar como constraint no banco, não como
validação de aplicação."

Este teste é o único do repositório que fala com Postgres, e fica fora de
`make test` de propósito: a suíte unitária não pode depender de infraestrutura.
Mas ele precisa existir — um fake que "garante" uma constraint prova apenas que
o fake concorda consigo mesmo, e é exatamente esta garantia que não pode ser
apenas acreditada.

    make db-up
    CAMU_TEST_DSN=postgresql://camu:camu@localhost:5433/camucrm make test-db
"""

from __future__ import annotations

import os
import unittest

import psycopg

from camucrm.db import Database, TetoFollowupError

DSN = os.getenv("CAMU_TEST_DSN", "").strip()


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteTetoNoBanco(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Database(DSN)
        cls.db.ensure_schema()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        contato = self.db.upsert_contato(
            f"5511{os.urandom(4).hex()}"[:15], nome="Teste", tipo="b2c"
        )
        self.conversa = self.db.get_or_create_conversa(contato.id)

    def test_dois_followups_sao_aceitos(self):
        self.assertEqual(self.db.registrar_followup(self.conversa.id, "toque 1"), 1)
        self.assertEqual(self.db.registrar_followup(self.conversa.id, "toque 2"), 2)

    def test_o_terceiro_e_recusado_pelo_banco(self):
        self.db.registrar_followup(self.conversa.id, "toque 1")
        self.db.registrar_followup(self.conversa.id, "toque 2")
        with self.assertRaises(TetoFollowupError):
            self.db.registrar_followup(self.conversa.id, "toque 3")

    def test_recusa_nao_deixa_estado_parcial(self):
        """A linha e o contador sobem na mesma transação, ou nenhum sobe."""
        self.db.registrar_followup(self.conversa.id, "toque 1")
        self.db.registrar_followup(self.conversa.id, "toque 2")
        with self.assertRaises(TetoFollowupError):
            self.db.registrar_followup(self.conversa.id, "toque 3")
        conversa = self.db.get_conversa(self.conversa.id)
        self.assertEqual(conversa.followups_enviados, 2)

    def test_contador_nao_pode_ser_furado_por_update_direto(self):
        """Mesmo um UPDATE cru esbarra no CHECK — é o ponto de §6."""
        with self.db._conn() as conn:  # noqa: SLF001
            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE conversas SET followups_enviados = 3 WHERE id = %s",
                        (self.conversa.id,),
                    )

    def test_insercao_crua_de_terceiro_followup_e_impossivel(self):
        """`numero` só admite 1 ou 2: um terceiro não é representável."""
        with self.db._conn() as conn:  # noqa: SLF001
            with self.assertRaises(psycopg.errors.CheckViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO followups (conversa_id, numero) VALUES (%s, 3)",
                        (self.conversa.id,),
                    )


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteSchema(unittest.TestCase):
    """O schema aplica duas vezes sem erro — `init` é seguro de repetir."""

    def test_ensure_schema_e_idempotente(self):
        db = Database(DSN)
        db.ensure_schema()
        db.ensure_schema()
        db.close()


if __name__ == "__main__":
    unittest.main()
