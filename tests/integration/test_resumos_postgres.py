"""Prova, contra Postgres real, as garantias de `resumos_conversa` (change
`resumo-conversa`) que um fake não pode provar sozinho — mesma razão de
existir de `tests/integration/test_teto_followup.py` e
`test_rascunhos_postgres.py`:

- Índice único `(conversa_id, coalesce(ultima_mensagem_id, 0),
  prompt_versao)`: gerar duas vezes na mesma fronteira não duplica linha —
  vira `ON CONFLICT ... DO UPDATE` em `Database.gravar_resumo`.
- A extensão da purga (§12): apaga `resumo`/`proximo_passo` de uma linha
  vinculada a uma mensagem purgada, preservando a linha (contexto, estágio,
  temperatura, timestamps).

Fora de `make test` de propósito. Apaga o que cria (padrão do commit
`982ff31`).

    make db-up
    CAMU_TEST_DSN=postgresql://camu:camu@localhost:5433/camucrm make test-db
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from camucrm.db import Database

DSN = os.getenv("CAMU_TEST_DSN", "").strip()


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class CasoIntegracaoResumo(unittest.TestCase):
    """Mesma base de `test_teto_followup.CasoIntegracao` — apaga o que cria
    via `ON DELETE CASCADE` a partir do contato."""

    rotulo = "teste-resumo"

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
                cur.execute("DELETE FROM contatos WHERE id = ANY(%s)", (self._criados,))


class TesteIndiceUnicoDeCursor(CasoIntegracaoResumo):
    def test_gerar_duas_vezes_na_mesma_fronteira_nao_duplica_linha(self):
        mensagem_id = self.db.registrar_mensagem(self.conversa.id, "in", "oi")
        primeiro_id = self.db.gravar_resumo(
            self.conversa.id, resumo="a", proximo_passo="b",
            ultima_mensagem_id=mensagem_id, estagio="S1", temperatura="quente",
            prompt_versao="1",
        )
        segundo_id = self.db.gravar_resumo(
            self.conversa.id, resumo="a2", proximo_passo="b2",
            ultima_mensagem_id=mensagem_id, estagio="S1", temperatura="quente",
            prompt_versao="1",
        )
        self.assertEqual(primeiro_id, segundo_id)
        registro = self.db.resumo_vigente(self.conversa.id, "1")
        self.assertEqual(registro.resumo, "a2")  # substituído, não duplicado

    def test_mensagem_nova_fronteira_nova_insere_linha_separada(self):
        mensagem_1 = self.db.registrar_mensagem(self.conversa.id, "in", "oi")
        primeiro_id = self.db.gravar_resumo(
            self.conversa.id, resumo="a", proximo_passo="b",
            ultima_mensagem_id=mensagem_1, estagio="S1", temperatura="quente",
            prompt_versao="1",
        )
        mensagem_2 = self.db.registrar_mensagem(self.conversa.id, "in", "mais uma")
        segundo_id = self.db.gravar_resumo(
            self.conversa.id, resumo="c", proximo_passo="d",
            ultima_mensagem_id=mensagem_2, estagio="S1", temperatura="quente",
            prompt_versao="1",
        )
        self.assertNotEqual(primeiro_id, segundo_id)

    def test_versao_de_prompt_diferente_nao_conflita(self):
        mensagem_id = self.db.registrar_mensagem(self.conversa.id, "in", "oi")
        id_v1 = self.db.gravar_resumo(
            self.conversa.id, resumo="a", proximo_passo="b",
            ultima_mensagem_id=mensagem_id, estagio="S1", temperatura="quente",
            prompt_versao="1",
        )
        id_v2 = self.db.gravar_resumo(
            self.conversa.id, resumo="a", proximo_passo="b",
            ultima_mensagem_id=mensagem_id, estagio="S1", temperatura="quente",
            prompt_versao="2",
        )
        self.assertNotEqual(id_v1, id_v2)


class TestePurgaApagaProseDoResumo(CasoIntegracaoResumo):
    def test_purga_apaga_resumo_mas_preserva_a_linha(self):
        mensagem_id = self.db.registrar_mensagem(
            self.conversa.id, "out", "texto pessoal do cliente",
            datetime.now(timezone.utc) - timedelta(days=400),
        )
        resumo_id = self.db.gravar_resumo(
            self.conversa.id, resumo="resumo pessoal", proximo_passo="passo pessoal",
            ultima_mensagem_id=mensagem_id, estagio="S1", temperatura="quente",
            prompt_versao="1",
        )
        self.db.atualizar_estado_conversa(self.conversa.id, resultado="ganho")
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversas SET atualizado_em = now() - interval '400 days' "
                    "WHERE id = %s",
                    (self.conversa.id,),
                )

        self.db.purgar_mensagens_antigas(meses=12)

        registro = self.db.resumo_vigente(self.conversa.id, "1")
        self.assertIsNone(registro.resumo)
        self.assertIsNone(registro.proximo_passo)
        # A linha em si — contexto, estágio, temperatura — não é removida.
        self.assertEqual(registro.id, resumo_id)
        self.assertEqual(registro.estagio, "S1")
        # O FK (`ON DELETE SET NULL`) perde o vínculo com a mensagem apagada.
        self.assertIsNone(registro.ultima_mensagem_id)

    def test_resumo_sem_mensagem_purgada_nao_e_tocado(self):
        resumo_id = self.db.gravar_resumo(
            self.conversa.id, resumo="intacto", proximo_passo="também intacto",
            ultima_mensagem_id=None, estagio="S1", temperatura="quente",
            prompt_versao="1",
        )
        self.db.atualizar_estado_conversa(self.conversa.id, resultado="ganho")
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversas SET atualizado_em = now() - interval '400 days' "
                    "WHERE id = %s",
                    (self.conversa.id,),
                )
        self.db.purgar_mensagens_antigas(meses=12)
        registro = self.db.resumo_vigente(self.conversa.id, "1")
        self.assertEqual(registro.id, resumo_id)
        self.assertEqual(registro.resumo, "intacto")
        self.assertEqual(registro.proximo_passo, "também intacto")


if __name__ == "__main__":
    unittest.main()
