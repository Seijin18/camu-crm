"""Prova, contra Postgres real, a trava de `acoes.marcar_marco`/
`acoes.mudar_funil_conversa` (change `painel-mensagens-recentes-e-acoes-
seguras`, requirement "Ações concorrentes no mesmo card não corrompem
marcos_manuais").

Mesma razão de existir de `tests/integration/test_teto_followup.py`
(`TesteEventoDeEstagioUnico`, `TesteUmaConversaAbertaPorContato`): um
`FakeDatabase` single-thread não tem como provar que duas transações
concorrentes de verdade serializam no `SELECT ... FOR UPDATE OF c` — só
Postgres real, com threads de verdade, prova isso.

Fora de `make test` de propósito. Apaga o que cria.

    make db-up
    CAMU_TEST_DSN=postgresql://camu:camu@localhost:5433/camucrm make test-db
"""

from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor

from camucrm.acoes import AcaoInvalidaError, marcar_marco, mudar_funil_conversa
from camucrm.db import Database

DSN = os.getenv("CAMU_TEST_DSN", "").strip()


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class CasoIntegracaoAcoes(unittest.TestCase):
    """Mesma base de `test_teto_followup.CasoIntegracao` — apaga o que cria
    via `ON DELETE CASCADE` a partir do contato."""

    rotulo = "teste-acoes-concorrentes"

    @classmethod
    def setUpClass(cls):
        cls.db = Database(DSN)
        cls.db.ensure_schema()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        self._criados: list[int] = []
        self.addCleanup(self._limpar)
        contato = self.db.upsert_contato(
            f"5511{os.urandom(4).hex()}"[:15], nome=self.rotulo, tipo="b2c"
        )
        self._criados.append(contato.id)
        self.conversa = self.db.get_or_create_conversa(contato.id)

    def _limpar(self):
        if not self._criados:
            return
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM contatos WHERE id = ANY(%s)", (self._criados,)
                )


class TesteMarcarMarcoConcorrente(CasoIntegracaoAcoes):
    """A corrida do Scenario "Duas marcações concorrentes não produzem
    estado contraditório": oito chamadas quase simultâneas, metade "ganho",
    metade "perdido", na mesma conversa."""

    def test_marcos_contraditorios_concorrentes_nao_gravam_os_dois(self):
        marcos = ["ganho", "perdido"] * 4

        def _marcar(marco: str):
            try:
                marcar_marco(self.db, self.conversa.id, marco, por="thread")
                return ("ok", marco)
            except AcaoInvalidaError:
                return ("recusado", marco)

        with ThreadPoolExecutor(max_workers=8) as executor:
            resultados = [f.result() for f in [executor.submit(_marcar, m) for m in marcos]]

        # Nenhuma outra exceção além de AcaoInvalidaError escapou: toda
        # tentativa terminou classificada.
        self.assertEqual(len(resultados), 8)

        marcos_gravados = self.db.marcos_da_conversa(self.conversa.id)
        # As duas nunca convivem — exatamente uma venceu a corrida.
        self.assertIn(marcos_gravados, ({"ganho"}, {"perdido"}))

        (vencedor,) = marcos_gravados
        # Toda tentativa do marco vencedor foi aceita (idempotente — não é
        # contraditória consigo mesma); toda tentativa do oposto foi
        # recusada.
        for status, marco in resultados:
            if marco == vencedor:
                self.assertEqual(status, "ok", resultados)
            else:
                self.assertEqual(status, "recusado", resultados)

        conversa = self.db.get_conversa(self.conversa.id)
        self.assertEqual(conversa.resultado, vencedor)


class TesteMudarFunilConcorrente(CasoIntegracaoAcoes):
    """A trava também protege `mudar_funil_conversa`: N chamadas
    concorrentes para o MESMO funil-alvo só produzem UMA correção — sem a
    trava, leituras intercaladas antes do primeiro commit veriam todas o
    funil antigo e gravariam correção duplicada."""

    def _correcoes(self) -> list[tuple]:
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT campo, antes, depois FROM correcoes WHERE conversa_id = %s",
                    (self.conversa.id,),
                )
                return cur.fetchall()

    def test_mudancas_concorrentes_para_o_mesmo_funil_gravam_uma_correcao(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            for f in [
                executor.submit(
                    mudar_funil_conversa, self.db, self.conversa.id, "b2b", por="thread"
                )
                for _ in range(8)
            ]:
                f.result()

        conversa = self.db.get_conversa(self.conversa.id)
        self.assertEqual(conversa.funil, "b2b")
        self.assertIsNotNone(conversa.temperatura)

        correcoes = self._correcoes()
        self.assertEqual(correcoes, [("funil", "b2c", "b2b")])


if __name__ == "__main__":
    unittest.main()
