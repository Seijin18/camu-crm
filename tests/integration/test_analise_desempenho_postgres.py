"""Prova, contra Postgres real, as consultas agregadas do change
`analise-desempenho` — corretude de SQL cru não é provável por
`FakeDatabase`, mesma razão de existir de `test_teto_followup.py`.

Cobre: `estagios_de_conversas_encerradas` (onde as conversas morrem),
`distribuicao_objecoes_por_estagio`, `padrao_correcoes`,
`retorno_por_numero_followup` e `rascunhos_vinculados_para_analise` (janela
de 72h no SQL). Fora de `make test` de propósito; apaga o que cria.

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
class CasoIntegracaoAnalise(unittest.TestCase):
    """Mesma base de `test_teto_followup.CasoIntegracao` — apaga o que cria
    via `ON DELETE CASCADE` a partir do contato."""

    rotulo = "teste-analise"

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

    def _limpar(self):
        if not self._criados:
            return
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute("DELETE FROM contatos WHERE id = ANY(%s)", (self._criados,))

    def _nova_conversa(self, *, funil="b2c"):
        contato = self.db.upsert_contato(
            f"5511{os.urandom(4).hex()}"[:15], nome=self.rotulo, tipo=funil
        )
        self._criados.append(contato.id)
        return self.db.get_or_create_conversa(contato.id, funil=funil)


class TesteOndeConversasMorrem(CasoIntegracaoAnalise):
    def test_so_conta_conversas_encerradas(self):
        c1 = self._nova_conversa()
        self.db.gravar_evento_estagio(c1.id, None, "S1")
        self.db.gravar_evento_estagio(c1.id, "S1", "S2")
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute("UPDATE conversas SET resultado = 'perdido' WHERE id = %s", (c1.id,))

        c2 = self._nova_conversa()
        self.db.gravar_evento_estagio(c2.id, None, "S1")
        # c2 continua aberta (sem `resultado`) — não deve aparecer

        resultado = self.db.estagios_de_conversas_encerradas()
        self.assertIn(c1.id, resultado)
        self.assertNotIn(c2.id, resultado)
        self.assertEqual(set(resultado[c1.id]), {"S1", "S2"})


class TesteObjecaoPorEstagio(CasoIntegracaoAnalise):
    def test_agrupa_por_estagio_e_categoria(self):
        c = self._nova_conversa()
        # Trechos diferentes: duas ocorrências REAIS e distintas de "frete"
        # no mesmo estágio. Idênticas (mesma categoria/estágio/trecho) é
        # exatamente o que `objecoes_dedupe_idx` (change
        # `literalidade-e-idempotencia-da-extracao`) colapsa numa linha só —
        # ver `test_idempotencia_extracao_postgres.py`.
        self.db.gravar_objecao(c.id, "frete", estagio="S4", trecho="o frete ficou caro")
        self.db.gravar_objecao(c.id, "frete", estagio="S4", trecho="demora demais")
        self.db.gravar_objecao(c.id, "preco", estagio="S2")

        resultado = self.db.distribuicao_objecoes_por_estagio()
        self.assertEqual(resultado[("S4", "frete")], 2)
        self.assertEqual(resultado[("S2", "preco")], 1)

    def test_filtra_por_desde(self):
        c = self._nova_conversa()
        antigo = datetime.now(timezone.utc) - timedelta(days=10)
        self.db.gravar_objecao(c.id, "frete", estagio="S4", em=antigo)
        self.db.gravar_objecao(c.id, "preco", estagio="S2")

        corte = datetime.now(timezone.utc) - timedelta(days=1)
        resultado = self.db.distribuicao_objecoes_por_estagio(desde=corte)
        self.assertNotIn(("S4", "frete"), resultado)
        self.assertEqual(resultado[("S2", "preco")], 1)


class TestePadraoCorrecoes(CasoIntegracaoAnalise):
    def test_conta_por_campo_e_par(self):
        c = self._nova_conversa()
        for _ in range(3):
            self.db.registrar_correcao(c.id, "funil", "b2c", "b2b")
        self.db.registrar_correcao(c.id, "funil", "b2b", "b2c")

        resultado = self.db.padrao_correcoes()
        mapa = {(campo, antes, depois): n for campo, antes, depois, n in resultado}
        self.assertEqual(mapa[("funil", "b2c", "b2b")], 3)
        self.assertEqual(mapa[("funil", "b2b", "b2c")], 1)
        # ORDER BY COUNT(*) DESC: o par mais frequente vem primeiro
        self.assertEqual(resultado[0][:3], ("funil", "b2c", "b2b"))


class TesteRetornoPorFollowup(CasoIntegracaoAnalise):
    def test_conta_retorno_por_numero(self):
        c1 = self._nova_conversa()
        self.db.registrar_mensagem(c1.id, "out", "oi", datetime.now(timezone.utc))
        self.db.registrar_followup(c1.id, "toque 1")
        self.db.registrar_mensagem(
            c1.id, "in", "respondeu", datetime.now(timezone.utc) + timedelta(hours=1)
        )

        c2 = self._nova_conversa()
        self.db.registrar_mensagem(c2.id, "out", "oi", datetime.now(timezone.utc))
        self.db.registrar_followup(c2.id, "toque 1")
        # sem resposta depois

        resultado = self.db.retorno_por_numero_followup()
        total, com_retorno = resultado[1]
        self.assertEqual(total, 2)
        self.assertEqual(com_retorno, 1)


class TesteRascunhosVinculadosParaAnalise(CasoIntegracaoAnalise):
    def test_janela_de_72h_filtra_no_sql(self):
        c = self._nova_conversa()
        rascunho_id = self.db.gravar_rascunho(
            c.id, estagio="S4", temperatura="quente", funil="b2c",
            opcoes=("opção 1", "opção 2"),
        )
        self.db.registrar_escolha_rascunho(rascunho_id, escolhida=1)
        enviada_em = datetime.now(timezone.utc)
        mensagem_id = self.db.registrar_mensagem(c.id, "out", "opção 1", enviada_em)
        self.db.vincular_rascunho(rascunho_id, mensagem_id, estagio_no_envio="S4")

        # dentro da janela — deve aparecer
        self.db.gravar_evento_estagio(c.id, "S4", "S5", em=enviada_em + timedelta(hours=10))
        # fora da janela — não deve aparecer na lista de `estagios_72h`
        self.db.gravar_evento_estagio(c.id, "S5", "S6", em=enviada_em + timedelta(hours=100))

        resultado = self.db.rascunhos_vinculados_para_analise()
        linha = next(r for r in resultado if r.rascunho_id == rascunho_id)
        self.assertEqual(linha.escolhida, 1)
        self.assertFalse(linha.editado)
        self.assertEqual(linha.estagio_no_envio, "S4")
        self.assertEqual(linha.estagios_72h, ["S5"])

    def test_sem_mensagem_id_nao_aparece(self):
        c = self._nova_conversa()
        rascunho_id = self.db.gravar_rascunho(
            c.id, estagio="S4", temperatura="quente", funil="b2c",
            opcoes=("opção 1", "opção 2"),
        )
        resultado = self.db.rascunhos_vinculados_para_analise()
        ids = {r.rascunho_id for r in resultado}
        self.assertNotIn(rascunho_id, ids)


if __name__ == "__main__":
    unittest.main()
