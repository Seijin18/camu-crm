"""Prova, contra Postgres real, as garantias de `rascunhos` (§10, change
`rascunho-registrado`) que um fake não pode provar sozinho — a mesma razão
de existir de `tests/integration/test_teto_followup.py`:

- `rascunhos_forma`: nunca meia geração (duas opções OU recusa com motivo).
- `rascunhos_escolha`: escolha registrada sempre tem `escolhido_em`.
- Índice único parcial em `mensagem_id`: nenhuma mensagem reivindicada por
  dois rascunhos.
- A extensão da purga (§12): apaga o texto do rascunho vinculado a uma
  mensagem purgada, sem apagar a linha.

Fora de `make test` de propósito. Apaga o que cria (padrão do commit
`982ff31`).

    make db-up
    CAMU_TEST_DSN=postgresql://camu:camu@localhost:5433/camucrm make test-db
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

import psycopg

from camucrm.db import TEXTO_RASCUNHO_PURGADO, Database

DSN = os.getenv("CAMU_TEST_DSN", "").strip()


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class CasoIntegracaoRascunho(unittest.TestCase):
    """Mesma base de `test_teto_followup.CasoIntegracao` — apaga o que cria
    via `ON DELETE CASCADE` a partir do contato."""

    rotulo = "teste-rascunho"

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

    def _insert_rascunho_cru(self, **campos) -> None:
        colunas = ", ".join(campos)
        marcadores = ", ".join(["%s"] * len(campos))
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO rascunhos (conversa_id, {colunas}) "
                    f"VALUES (%s, {marcadores})",
                    (self.conversa.id, *campos.values()),
                )


class TesteRascunhosForma(CasoIntegracaoRascunho):
    def test_duas_opcoes_sem_encerrar_e_aceito(self):
        self._insert_rascunho_cru(
            estagio="S1", temperatura="quente", funil="b2c",
            opcao_1="a", opcao_2="b", encerrar=False,
        )

    def test_recusa_com_motivo_sem_opcoes_e_aceita(self):
        self._insert_rascunho_cru(
            estagio="S1", temperatura="frio", funil="b2c",
            encerrar=True, motivo="teto atingido",
        )

    def test_uma_opcao_so_e_recusada(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._insert_rascunho_cru(
                estagio="S1", temperatura="quente", funil="b2c",
                opcao_1="a", encerrar=False,
            )

    def test_encerrar_sem_motivo_e_recusado(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._insert_rascunho_cru(
                estagio="S1", temperatura="frio", funil="b2c", encerrar=True,
            )

    def test_encerrar_com_opcoes_e_recusado(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._insert_rascunho_cru(
                estagio="S1", temperatura="frio", funil="b2c",
                encerrar=True, motivo="x", opcao_1="a", opcao_2="b",
            )

    def test_nenhuma_opcao_nem_recusa_e_recusado(self):
        with self.assertRaises(psycopg.errors.CheckViolation):
            self._insert_rascunho_cru(estagio="S1", temperatura="quente", funil="b2c")


class TesteRascunhosEscolha(CasoIntegracaoRascunho):
    def setUp(self):
        super().setUp()
        self.rascunho_id = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("a", "b"),
        )

    def test_sem_escolha_ainda_e_o_estado_inicial(self):
        registro = self.db.rascunho(self.rascunho_id)
        self.assertIsNone(registro.escolhida)
        self.assertIsNone(registro.escolhido_em)

    def test_escolher_opcao_grava_escolhido_em(self):
        self.db.registrar_escolha_rascunho(self.rascunho_id, escolhida=1, por="Marcos")
        registro = self.db.rascunho(self.rascunho_id)
        self.assertEqual(registro.escolhida, 1)
        self.assertIsNotNone(registro.escolhido_em)

    def test_escrever_do_zero_sem_escolhida_e_aceito(self):
        self.db.registrar_escolha_rascunho(
            self.rascunho_id, texto_final="do zero", por="Marcos"
        )
        registro = self.db.rascunho(self.rascunho_id)
        self.assertIsNone(registro.escolhida)
        self.assertEqual(registro.texto_final, "do zero")

    def test_escolhido_em_sem_escolhida_nem_texto_final_e_recusado(self):
        """Insere cru para provar a constraint em si, não a validação de
        `Database.registrar_escolha_rascunho` (que já recusa isso em Python)."""
        with self.assertRaises(psycopg.errors.CheckViolation):
            with self.db._conn() as conn:  # noqa: SLF001
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE rascunhos SET escolhido_em = now() WHERE id = %s",
                        (self.rascunho_id,),
                    )


class TesteMensagemIdUnico(CasoIntegracaoRascunho):
    def setUp(self):
        super().setUp()
        self.mensagem_id = self.db.registrar_mensagem(self.conversa.id, "out", "oi")
        self.rascunho_1 = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("a", "b"),
        )
        self.rascunho_2 = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("c", "d"),
        )

    def test_primeiro_vinculo_e_aceito(self):
        self.assertTrue(self.db.vincular_rascunho(self.rascunho_1, self.mensagem_id))

    def test_segundo_rascunho_nao_reivindica_a_mesma_mensagem(self):
        self.db.vincular_rascunho(self.rascunho_1, self.mensagem_id)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.db.vincular_rascunho(self.rascunho_2, self.mensagem_id)

    def test_mensagem_id_nulo_nao_conflita(self):
        """O índice é parcial — `mensagem_id IS NULL` não conta."""
        # As duas linhas nascem com `mensagem_id IS NULL` (ver setUp) e
        # convivem sem erro; só a segunda ATRIBUIÇÃO ao mesmo valor conflita.
        self.assertIsNone(self.db.rascunho(self.rascunho_1).mensagem_id)
        self.assertIsNone(self.db.rascunho(self.rascunho_2).mensagem_id)


class TestePurgaApagaTextoDoRascunho(CasoIntegracaoRascunho):
    def test_purga_anonimiza_texto_mas_preserva_a_linha(self):
        mensagem_id = self.db.registrar_mensagem(
            self.conversa.id, "out", "texto pessoal do cliente",
            datetime.now(timezone.utc) - timedelta(days=400),
        )
        rascunho_id = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("opção 1 pessoal", "opção 2 pessoal"),
        )
        self.db.registrar_escolha_rascunho(
            rascunho_id, texto_final="texto final pessoal", por="Marcos"
        )
        self.db.vincular_rascunho(rascunho_id, mensagem_id)

        # Conversa precisa estar encerrada e velha o bastante para a purga
        # (mesmo critério de `purgar_mensagens_antigas`).
        self.db.atualizar_estado_conversa(self.conversa.id, resultado="ganho")
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversas SET atualizado_em = now() - interval '400 days' "
                    "WHERE id = %s",
                    (self.conversa.id,),
                )

        apagadas = self.db.purgar_mensagens_antigas(meses=12)
        self.assertGreaterEqual(apagadas, 1)

        registro = self.db.rascunho(rascunho_id)
        self.assertEqual(registro.opcao_1, TEXTO_RASCUNHO_PURGADO)
        self.assertEqual(registro.opcao_2, TEXTO_RASCUNHO_PURGADO)
        self.assertEqual(registro.texto_final, TEXTO_RASCUNHO_PURGADO)
        # A linha em si — contexto, escolha, timestamps — não é removida.
        self.assertEqual(registro.estagio, "S1")
        self.assertIsNotNone(registro.escolhido_em)
        # O FK (`ON DELETE SET NULL`) perde o vínculo com a mensagem apagada.
        self.assertIsNone(registro.mensagem_id)

    def test_rascunho_sem_mensagem_vinculada_nao_e_tocado(self):
        rascunho_id = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("a", "b"),
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
        registro = self.db.rascunho(rascunho_id)
        self.assertEqual(registro.opcao_1, "a")
        self.assertEqual(registro.opcao_2, "b")


if __name__ == "__main__":
    unittest.main()
