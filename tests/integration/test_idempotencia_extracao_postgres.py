"""Prova, contra Postgres real, os dois invariantes de idempotência do change
`literalidade-e-idempotencia-da-extracao` (§2):

1. `ultima_mensagem_processada_id` nunca regride (`GREATEST`).
2. `gravar_objecao` nunca duplica a mesma (conversa, categoria, estágio,
   trecho) — `objecoes_dedupe_idx` + `ON CONFLICT DO NOTHING`.

Mesmo espírito de `test_teto_followup.py`: um fake que "garante" uma
constraint prova só que o fake concorda consigo mesmo — a garantia real
precisa ser cobrada do banco.

    make db-up
    CAMU_TEST_DSN=postgresql://camu:camu@localhost:5433/camucrm make test-db
"""

from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg

from camucrm.db import Database

DSN = os.getenv("CAMU_TEST_DSN", "").strip()


class CasoIntegracao(unittest.TestCase):
    """Mesma base de `test_teto_followup.py`: limpa o que criou."""

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
class TesteWatermarkNuncaRegride(CasoIntegracao):
    rotulo = "teste-watermark"

    def setUp(self):
        super().setUp()
        self.conversa = self.db.get_or_create_conversa(self._novo_contato().id)

    def _watermark(self) -> int | None:
        return self.db.get_conversa(self.conversa.id).ultima_mensagem_processada_id

    def test_escrita_com_valor_menor_nao_regride(self):
        self.db.atualizar_estado_conversa(
            self.conversa.id, ultima_mensagem_processada_id=10
        )
        self.assertEqual(self._watermark(), 10)

        self.db.atualizar_estado_conversa(
            self.conversa.id, ultima_mensagem_processada_id=3
        )
        self.assertEqual(self._watermark(), 10)

    def test_escritas_concorrentes_terminam_no_maior_valor(self):
        """Webhook e `camucrm extrair` rodando ao mesmo tempo, ou dois
        webhooks quase simultâneos: não importa em que ordem as escritas
        cheguem ao banco, o watermark final é o maior de todos."""
        valores = [3, 15, 7, 20, 1, 12]
        with ThreadPoolExecutor(max_workers=len(valores)) as executor:
            for f in [
                executor.submit(
                    self.db.atualizar_estado_conversa,
                    self.conversa.id,
                    ultima_mensagem_processada_id=v,
                )
                for v in valores
            ]:
                f.result()
        self.assertEqual(self._watermark(), max(valores))

    def test_greatest_nao_afeta_outros_campos_da_mesma_chamada(self):
        self.db.atualizar_estado_conversa(
            self.conversa.id, estagio="S2", ultima_mensagem_processada_id=5
        )
        conversa = self.db.get_conversa(self.conversa.id)
        self.assertEqual(conversa.estagio, "S2")
        self.assertEqual(conversa.ultima_mensagem_processada_id, 5)


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteObjecaoIdempotente(CasoIntegracao):
    rotulo = "teste-objecao"

    def setUp(self):
        super().setUp()
        self.conversa = self.db.get_or_create_conversa(self._novo_contato().id)

    def _objecoes(self):
        return self.db.objecoes_da_conversa(self.conversa.id)

    def test_mesma_objecao_duas_vezes_nao_duplica(self):
        em = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        self.db.gravar_objecao(
            self.conversa.id, "preco", estagio="S2", trecho="achei caro", em=em
        )
        self.db.gravar_objecao(
            self.conversa.id, "preco", estagio="S2", trecho="achei caro", em=em
        )
        self.assertEqual(len(self._objecoes()), 1)

    def test_gravacoes_concorrentes_da_mesma_objecao_produzem_uma_linha(self):
        """O cenário real do achado: reprocessamento concorrente (ou
        `forcar=True` rodando em paralelo) tentando gravar a mesma objeção."""
        with ThreadPoolExecutor(max_workers=6) as executor:
            for f in [
                executor.submit(
                    self.db.gravar_objecao,
                    self.conversa.id,
                    "frete",
                    estagio="S4",
                    trecho="o frete ficou caro",
                )
                for _ in range(6)
            ]:
                f.result()
        self.assertEqual(len(self._objecoes()), 1)

    def test_objecao_sem_trecho_duas_vezes_tambem_deduplica(self):
        self.db.gravar_objecao(self.conversa.id, "sem_resposta", estagio="S1")
        self.db.gravar_objecao(self.conversa.id, "sem_resposta", estagio="S1")
        self.assertEqual(len(self._objecoes()), 1)

    def test_estagio_diferente_produz_linha_separada(self):
        self.db.gravar_objecao(self.conversa.id, "preco", estagio="S2", trecho="caro")
        self.db.gravar_objecao(self.conversa.id, "preco", estagio="S4", trecho="caro")
        self.assertEqual(len(self._objecoes()), 2)

    def test_insercao_crua_duplicada_e_recusada_pelo_indice(self):
        """Mesmo um INSERT cru esbarra no índice único — é o ponto deste
        change: a garantia é do banco, não da aplicação."""
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO objecoes (conversa_id, categoria, estagio, trecho) "
                    "VALUES (%s, 'preco', 'S2', 'achei caro')",
                    (self.conversa.id,),
                )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO objecoes (conversa_id, categoria, estagio, trecho) "
                        "VALUES (%s, 'preco', 'S2', 'achei caro')",
                        (self.conversa.id,),
                    )


if __name__ == "__main__":
    unittest.main()
