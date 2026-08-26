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
from concurrent.futures import ThreadPoolExecutor

import psycopg

from camucrm.db import Database, TetoFollowupError

DSN = os.getenv("CAMU_TEST_DSN", "").strip()


class CasoIntegracao(unittest.TestCase):
    """Base que apaga o que criou.

    Estes testes escrevem no mesmo Postgres que a operação usa — não há um
    banco de teste separado. Sem limpeza, cada execução deixa contatos órfãos
    que aparecem na fila do dia e nas métricas, e o lixo é indistinguível de
    lead real. `ON DELETE CASCADE` leva conversas, mensagens, fatos e eventos
    junto do contato.
    """

    rotulo = "teste"

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

    def _novo_contato(self):
        contato = self.db.upsert_contato(
            f"5511{os.urandom(4).hex()}"[:15], nome=self.rotulo, tipo="b2c"
        )
        self._criados.append(contato.id)
        return contato

    def _limpar(self):
        if not self._criados:
            return
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM contatos WHERE id = ANY(%s)", (self._criados,)
                )


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteTetoNoBanco(CasoIntegracao):
    rotulo = "teste-teto"

    def setUp(self):
        super().setUp()
        self.conversa = self.db.get_or_create_conversa(self._novo_contato().id)

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
class TesteUmaConversaAbertaPorContato(CasoIntegracao):
    """A corrida que o webhook expôs: dois eventos simultâneos do mesmo número.

    `get_or_create_conversa` lê e só então insere. Sob paralelismo real — que é
    como a Evolution API entrega — as duas leituras viam "nenhuma conversa
    aberta" e criavam duas, dividindo o histórico entre elas sem que nada nos
    dados denunciasse o problema.
    """

    rotulo = "teste-corrida"

    def setUp(self):
        super().setUp()
        self.contato = self._novo_contato()

    def test_chamadas_concorrentes_devolvem_a_mesma_conversa(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = {
                f.result().id
                for f in [
                    executor.submit(self.db.get_or_create_conversa, self.contato.id)
                    for _ in range(8)
                ]
            }
        self.assertEqual(len(ids), 1, f"criou {len(ids)} conversas: {ids}")

    def test_insercao_crua_de_segunda_conversa_aberta_e_recusada(self):
        self.db.get_or_create_conversa(self.contato.id)
        with self.db._conn() as conn:  # noqa: SLF001
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO conversas (contato_id, funil, estagio) "
                        "VALUES (%s, 'b2c', 'S0')",
                        (self.contato.id,),
                    )

    def test_conversa_fechada_libera_uma_nova(self):
        """O índice é parcial: só vale para `resultado IS NULL`."""
        primeira = self.db.get_or_create_conversa(self.contato.id)
        self.db.atualizar_estado_conversa(primeira.id, resultado="ganho")
        segunda = self.db.get_or_create_conversa(self.contato.id)
        self.assertNotEqual(primeira.id, segunda.id)


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteEventoDeEstagioUnico(CasoIntegracao):
    """A segunda corrida que o webhook expôs: `S0 -> S1` gravado três vezes.

    Eventos concorrentes da mesma conversa leem todos o estágio antigo e
    gravam todos a mesma transição. `metrics.tempo_por_estagio` usa LEAD()
    sobre esses eventos, e as duplicatas viram intervalos de zero hora.
    """

    rotulo = "teste-evento"

    def setUp(self):
        super().setUp()
        self.conversa = self.db.get_or_create_conversa(self._novo_contato().id)

    def _eventos(self):
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT de, para FROM eventos_estagio WHERE conversa_id = %s",
                    (self.conversa.id,),
                )
                return cur.fetchall()

    def test_gravacoes_concorrentes_da_mesma_transicao_geram_um_evento(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            for f in [
                executor.submit(
                    self.db.gravar_evento_estagio, self.conversa.id, "S0", "S1"
                )
                for _ in range(6)
            ]:
                f.result()
        self.assertEqual(self._eventos(), [("S0", "S1")])

    def test_transicoes_diferentes_convivem(self):
        self.db.gravar_evento_estagio(self.conversa.id, "S0", "S1")
        self.db.gravar_evento_estagio(self.conversa.id, "S1", "S2")
        self.assertEqual(sorted(self._eventos()), [("S0", "S1"), ("S1", "S2")])

    def test_entrada_sem_estagio_anterior_tambem_deduplica(self):
        """`de IS NULL` precisa do coalesce no índice para conflitar."""
        self.db.gravar_evento_estagio(self.conversa.id, None, "S0")
        self.db.gravar_evento_estagio(self.conversa.id, None, "S0")
        self.assertEqual(self._eventos(), [(None, "S0")])


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


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteLimpeza(CasoIntegracao):
    """A própria limpeza precisa ser verificada — se ela falhar em silêncio,
    o lixo volta a se acumular sem ninguém notar."""

    rotulo = "teste-limpeza"

    def _contar(self, contato_id: int) -> int:
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM contatos WHERE id = %s", (contato_id,))
                return cur.fetchone()[0]

    def test_contato_criado_some_apos_o_teste(self):
        contato = self._novo_contato()
        self.db.get_or_create_conversa(contato.id)
        self.assertEqual(self._contar(contato.id), 1)
        self._limpar()
        self._criados = []
        self.assertEqual(self._contar(contato.id), 0)
