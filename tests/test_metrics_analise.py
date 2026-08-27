"""Change `analise-desempenho`: consultas novas de `camucrm.metrics` e a
constante `AMOSTRA_MINIMA` — contagens conferidas à mão contra `FakeDatabase`
(a corretude do SQL cru fica para `tests/integration/`, contra Postgres real).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from camucrm import metrics
from tests.fakes import FakeDatabase

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class TesteAmostraMinima(unittest.TestCase):
    def test_amostra_suficiente_no_limiar(self):
        self.assertTrue(metrics.amostra_suficiente(metrics.AMOSTRA_MINIMA))

    def test_amostra_insuficiente_abaixo_do_limiar(self):
        self.assertFalse(metrics.amostra_suficiente(metrics.AMOSTRA_MINIMA - 1))

    def test_calculo_nunca_esconde_o_dado_mesmo_com_n_baixo(self):
        """Requirement "Porcentagem some abaixo da amostra mínima": quem
        esconde é a apresentação, não o cálculo — uma função de métrica
        continua devolvendo o valor com n=3, junto de amostra_suficiente."""
        db = FakeDatabase()
        c1 = db.criar_conversa(funil="b2c", estagio="S0")
        c2 = db.criar_conversa(funil="b2c", estagio="S0")
        c3 = db.criar_conversa(funil="b2c", estagio="S0")
        for c in (c1, c2, c3):
            db.gravar_evento_estagio(c.id, None, "S1")
        # só c1 avança para S2 — taxa de 1/3, n=3, abaixo de AMOSTRA_MINIMA
        db.gravar_evento_estagio(c1.id, "S1", "S2")

        resultado = metrics.conversao(db, "S1", "S2")
        self.assertEqual(resultado.alcancaram_de, 3)
        self.assertEqual(resultado.alcancaram_para, 1)
        self.assertAlmostEqual(resultado.taxa, 1 / 3)
        self.assertFalse(metrics.amostra_suficiente(resultado.alcancaram_de))


class TesteConversaoAdjacente(unittest.TestCase):
    def test_um_par_por_estagio_adjacente_b2c(self):
        db = FakeDatabase()
        resultado = metrics.conversao_adjacente(db, "b2c")
        pares = [(c.de, c.para) for c in resultado]
        self.assertEqual(
            pares,
            [("S0", "S1"), ("S1", "S2"), ("S2", "S3"), ("S3", "S4"), ("S4", "S5"), ("S5", "S6")],
        )

    def test_funil_invalido_recusa(self):
        db = FakeDatabase()
        with self.assertRaises(ValueError):
            metrics.conversao_adjacente(db, "xis")


class TesteOndeMorrem(unittest.TestCase):
    def test_agrupa_pelo_maior_estagio_alcancado_so_encerradas(self):
        db = FakeDatabase()
        c1 = db.criar_conversa(funil="b2c", estagio="S0")
        db.gravar_evento_estagio(c1.id, None, "S1")
        db.gravar_evento_estagio(c1.id, "S1", "S2")
        db.conversas[c1.id].resultado = "perdido"

        c2 = db.criar_conversa(funil="b2c", estagio="S0")
        db.gravar_evento_estagio(c2.id, None, "S1")
        db.conversas[c2.id].resultado = "perdido"

        # conversa aberta — não conta, mesmo tendo avançado mais que as duas
        c3 = db.criar_conversa(funil="b2c", estagio="S0")
        db.gravar_evento_estagio(c3.id, None, "S1")
        db.gravar_evento_estagio(c3.id, "S1", "S2")
        db.gravar_evento_estagio(c3.id, "S2", "S3")

        resultado = metrics.onde_morrem(db)
        self.assertEqual(resultado.distribuicao, {"S2": 1, "S1": 1})
        self.assertEqual(resultado.n, 2)

    def test_sem_conversas_encerradas_devolve_n_zero(self):
        db = FakeDatabase()
        resultado = metrics.onde_morrem(db)
        self.assertEqual(resultado.n, 0)
        self.assertEqual(resultado.distribuicao, {})


class TesteObjecaoPorEstagio(unittest.TestCase):
    def test_preserva_o_cruzamento_estagio_categoria(self):
        db = FakeDatabase()
        c = db.criar_conversa(funil="b2c", estagio="S4")
        # Trechos diferentes: duas ocorrências REAIS e distintas de "frete"
        # no mesmo estágio. Idênticas (mesma categoria/estágio/trecho) é
        # exatamente o que `objecoes_dedupe_idx` (change
        # `literalidade-e-idempotencia-da-extracao`) agora colapsa em uma
        # linha só — ver `tests/test_db_idempotencia.py`.
        db.gravar_objecao(c.id, "frete", estagio="S4", trecho="o frete ficou caro")
        db.gravar_objecao(c.id, "frete", estagio="S4", trecho="demora demais pra chegar")
        db.gravar_objecao(c.id, "preco", estagio="S2")

        resultado = metrics.objecao_por_estagio(db)
        self.assertEqual(resultado.contagem[("S4", "frete")], 2)
        self.assertEqual(resultado.contagem[("S2", "preco")], 1)
        self.assertEqual(resultado.n, 3)


class TestePadraoCorrecoes(unittest.TestCase):
    def test_conta_por_campo_e_par_antes_depois(self):
        db = FakeDatabase()
        c = db.criar_conversa(funil="b2c", estagio="S0")
        for _ in range(3):
            db.registrar_correcao(c.id, "funil", "b2c", "b2b")
        db.registrar_correcao(c.id, "funil", "b2b", "b2c")

        resultado = metrics.padrao_correcoes(db)
        linha_maior = resultado[0]
        self.assertEqual((linha_maior.campo, linha_maior.antes, linha_maior.depois), ("funil", "b2c", "b2b"))
        self.assertEqual(linha_maior.n, 3)
        self.assertEqual(sum(l.n for l in resultado), 4)


class TesteRetornoPorFollowup(unittest.TestCase):
    def test_conta_retorno_por_numero(self):
        # Timestamps explícitos (não `registrar_followup`, que usa
        # `datetime.now()`) — o teste precisa de ordem determinística entre
        # o envio do follow-up e a resposta.
        db = FakeDatabase()
        c1 = db.criar_conversa(funil="b2c", estagio="S4")
        db.registrar_mensagem(c1.id, "out", "oi", AGORA)
        db.followups[c1.id] = [(1, "toque 1", AGORA)]
        db.registrar_mensagem(c1.id, "in", "respondeu", AGORA + timedelta(hours=1))

        c2 = db.criar_conversa(funil="b2c", estagio="S4")
        db.registrar_mensagem(c2.id, "out", "oi", AGORA)
        db.followups[c2.id] = [(1, "toque 1", AGORA)]
        # sem resposta depois

        resultado = {r.numero: r for r in metrics.retorno_por_followup(db)}
        self.assertEqual(resultado[1].n, 2)
        self.assertEqual(resultado[1].com_retorno, 1)
        self.assertAlmostEqual(resultado[1].taxa, 0.5)


class TesteAbRascunhos(unittest.TestCase):
    def _rascunho_vinculado(self, db, conversa_id, *, escolhida=None, texto_final=None,
                            estagio_no_envio="S4"):
        rid = db.gravar_rascunho(
            conversa_id, estagio="S4", temperatura="quente", funil="b2c",
            opcoes=("opção 1", "opção 2"),
        )
        if escolhida is not None or texto_final is not None:
            db.registrar_escolha_rascunho(rid, escolhida=escolhida, texto_final=texto_final)
        mid = db.registrar_mensagem(conversa_id, "out", "texto enviado", AGORA)
        db.vincular_rascunho(rid, mid, estagio_no_envio=estagio_no_envio)
        return rid

    def test_bloqueado_abaixo_do_limiar(self):
        db = FakeDatabase()
        c = db.criar_conversa(funil="b2c", estagio="S4")
        self._rascunho_vinculado(db, c.id, escolhida=1)

        resultado = metrics.ab_rascunhos(db)
        self.assertEqual(resultado.n_vinculados, 1)
        self.assertFalse(resultado.amostra_suficiente)
        self.assertLess(resultado.n_vinculados, metrics.LIMIAR_RASCUNHOS_VINCULADOS)

    def test_conta_opcao_edicao_e_avanco(self):
        db = FakeDatabase()

        # opção 1, sem edição, avança de S4 para S5 em 72h
        c1 = db.criar_conversa(funil="b2c", estagio="S4")
        r1 = self._rascunho_vinculado(db, c1.id, escolhida=1)
        db.gravar_evento_estagio(c1.id, "S4", "S5", em=AGORA + timedelta(hours=10))

        # opção 2, editada, não avança
        c2 = db.criar_conversa(funil="b2c", estagio="S4")
        self._rascunho_vinculado(db, c2.id, escolhida=2, texto_final="texto editado")

        # escreveu do zero
        c3 = db.criar_conversa(funil="b2c", estagio="S4")
        self._rascunho_vinculado(db, c3.id, texto_final="do zero")

        resultado = metrics.ab_rascunhos(db)
        self.assertEqual(resultado.n_vinculados, 3)
        self.assertEqual(resultado.escolha_1, 1)
        self.assertEqual(resultado.escolha_2, 1)
        self.assertEqual(resultado.escreveu_do_zero, 1)
        self.assertEqual(resultado.editado, 1)
        self.assertEqual(resultado.sem_edicao, 1)
        self.assertEqual(resultado.avancou_72h, 1)
        self.assertEqual(resultado.n_avaliavel_avanco, 3)
        self.assertAlmostEqual(resultado.proporcao_opcao_1, 0.5)
        self.assertAlmostEqual(resultado.proporcao_editado, 0.5)
        self.assertAlmostEqual(resultado.taxa_avanco_72h, 1 / 3)

    def test_avanco_fora_da_janela_de_72h_nao_conta(self):
        db = FakeDatabase()
        c = db.criar_conversa(funil="b2c", estagio="S4")
        self._rascunho_vinculado(db, c.id, escolhida=1)
        db.gravar_evento_estagio(c.id, "S4", "S5", em=AGORA + timedelta(hours=100))

        resultado = metrics.ab_rascunhos(db)
        self.assertEqual(resultado.avancou_72h, 0)


if __name__ == "__main__":
    unittest.main()
