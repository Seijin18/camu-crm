"""Ações humanas compartilhadas entre CLI e painel (`camucrm/acoes.py`).

§3: estágio nunca regride, e marco incompatível com o funil é recusado antes
de tocar no banco. §7: toda mudança de funil grava correção, sem exceção.
"""

from __future__ import annotations

import unittest

from camucrm.acoes import (
    AcaoInvalidaError,
    MarcoNaoPermitidoError,
    desconsiderar_recusa,
    marcar_marco,
    marco_permitido,
    mudar_funil_conversa,
)
from tests.fakes import FakeDatabase


class TesteMarcoPermitido(unittest.TestCase):
    """Função pura — todas as combinações funil × marco (§3)."""

    def test_ganho_permitido_em_b2c(self):
        self.assertIsNone(marco_permitido("b2c", "ganho"))

    def test_ganho_permitido_em_b2b(self):
        self.assertIsNone(marco_permitido("b2b", "ganho"))

    def test_perdido_permitido_em_b2c(self):
        self.assertIsNone(marco_permitido("b2c", "perdido"))

    def test_perdido_permitido_em_b2b(self):
        self.assertIsNone(marco_permitido("b2b", "perdido"))

    def test_consignacao_assinada_permitida_em_b2b(self):
        self.assertIsNone(marco_permitido("b2b", "consignacao_assinada"))

    def test_consignacao_assinada_recusada_em_b2c(self):
        motivo = marco_permitido("b2c", "consignacao_assinada")
        self.assertIsNotNone(motivo)
        self.assertIn("§3", motivo)

    def test_primeira_reposicao_permitida_em_b2b(self):
        self.assertIsNone(marco_permitido("b2b", "primeira_reposicao"))

    def test_primeira_reposicao_recusada_em_b2c(self):
        motivo = marco_permitido("b2c", "primeira_reposicao")
        self.assertIsNotNone(motivo)
        self.assertIn("§3", motivo)

    def test_marco_desconhecido_e_recusado(self):
        motivo = marco_permitido("b2c", "nao existe")
        self.assertIsNotNone(motivo)


class TesteMarcarMarco(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()

    def test_ganho_registra_marco_resultado_e_recalcula(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="S4")
        resultado = marcar_marco(self.db, conversa.id, "ganho", por="marcos")
        self.assertEqual(resultado.marco, "ganho")
        self.assertIn("ganho", self.db.marcos_da_conversa(conversa.id))
        self.assertEqual(self.db.get_conversa(conversa.id).resultado, "ganho")
        self.assertEqual(resultado.estado.estagio, "S6")

    def test_perdido_registra_resultado_perdido(self):
        conversa = self.db.criar_conversa(funil="b2b", estagio="P2")
        marcar_marco(self.db, conversa.id, "perdido", por="marcos")
        self.assertIn("perdido", self.db.marcos_da_conversa(conversa.id))
        self.assertEqual(self.db.get_conversa(conversa.id).resultado, "perdido")

    def test_consignacao_assinada_em_b2c_e_recusada_e_nada_e_gravado(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="S4")
        with self.assertRaises(MarcoNaoPermitidoError) as ctx:
            marcar_marco(self.db, conversa.id, "consignacao_assinada", por="marcos")
        self.assertIn("§3", str(ctx.exception))
        self.assertEqual(ctx.exception.regra, "§3")
        self.assertEqual(self.db.marcos_da_conversa(conversa.id), set())
        # Nada avançou nem foi marcado como resultado.
        self.assertIsNone(self.db.get_conversa(conversa.id).resultado)

    def test_primeira_reposicao_em_b2c_e_recusada(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="S4")
        with self.assertRaises(MarcoNaoPermitidoError):
            marcar_marco(self.db, conversa.id, "primeira_reposicao", por="marcos")

    def test_consignacao_assinada_em_b2b_e_aceita(self):
        conversa = self.db.criar_conversa(funil="b2b", estagio="P4")
        resultado = marcar_marco(self.db, conversa.id, "consignacao_assinada", por="marcos")
        self.assertEqual(resultado.estado.estagio, "P5")

    def test_conversa_inexistente_levanta_sem_gravar(self):
        with self.assertRaises(AcaoInvalidaError):
            marcar_marco(self.db, 999, "ganho", por="marcos")


class TesteMudarFunilConversa(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()

    def test_mudanca_de_funil_grava_correcao_sempre(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="S2")
        self.assertEqual(self.db.correcoes, [])
        mudar_funil_conversa(self.db, conversa.id, "b2b", por="marcos")
        self.assertEqual(len(self.db.correcoes), 1)
        correcao = self.db.correcoes[0]
        self.assertEqual(correcao["campo"], "funil")
        self.assertEqual(correcao["antes"], "b2c")
        self.assertEqual(correcao["depois"], "b2b")
        self.assertEqual(correcao["por"], "marcos")

    def test_mudanca_de_funil_atualiza_contato_e_conversa(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="S0")
        mudar_funil_conversa(self.db, conversa.id, "b2b", por="marcos")
        atualizada = self.db.get_conversa(conversa.id)
        self.assertEqual(atualizada.funil, "b2b")
        self.assertEqual(self.db.contatos[conversa.contato_id].tipo, "b2b")

    def test_mesmo_funil_nao_grava_correcao(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="S0")
        resultado = mudar_funil_conversa(self.db, conversa.id, "b2c", por="marcos")
        self.assertIsNone(resultado.movimento)
        self.assertEqual(self.db.correcoes, [])

    def test_conversa_inexistente_levanta(self):
        with self.assertRaises(AcaoInvalidaError):
            mudar_funil_conversa(self.db, 999, "b2b", por="marcos")

    def test_funil_invalido_levanta(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="S0")
        with self.assertRaises(AcaoInvalidaError):
            mudar_funil_conversa(self.db, conversa.id, "xis", por="marcos")

    def test_estagio_divergente_do_cache_usa_o_reconciliado(self):
        """Requirement "mudar_funil_conversa reconcilia contra o histórico":
        `conversas.estagio` (cache) fica em S4, mas o histórico
        (`eventos_estagio`) só registra S2 — o evento gravado por esta ação
        precisa usar S2 (reconciliado), não S4 (cache cru)."""
        conversa = self.db.criar_conversa(funil="b2c", estagio="S4")
        # Nenhum evento em `eventos_estagio` aponta para S4 — só para S2,
        # simulando um cache inflado/desalinhado em relação ao histórico
        # real (ex. a regressão de watermark que `literalidade-e-
        # idempotencia-da-extracao` corrigiu).
        self.db.gravar_evento_estagio(conversa.id, "S1", "S2", motivo="foto_pet_recebida")

        resultado = mudar_funil_conversa(self.db, conversa.id, "b2b", por="marcos")

        self.assertIsNotNone(resultado.movimento)
        self.assertEqual(resultado.movimento.de, "S2")
        self.assertNotEqual(resultado.movimento.de, "S4")


class TesteDesconsiderarRecusa(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()

    def test_exige_por(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="SX")
        self.db.fatos.append((conversa.id, "recusa_explicita", None, None, None))
        with self.assertRaises(AcaoInvalidaError):
            desconsiderar_recusa(self.db, conversa.id, por=None)
        with self.assertRaises(AcaoInvalidaError):
            desconsiderar_recusa(self.db, conversa.id, por="   ")

    def test_conversa_inexistente_levanta(self):
        with self.assertRaises(AcaoInvalidaError):
            desconsiderar_recusa(self.db, 999, por="marcos")

    def test_sem_recusa_explicita_registrada_levanta(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="S2")
        with self.assertRaises(AcaoInvalidaError):
            desconsiderar_recusa(self.db, conversa.id, por="marcos")

    def test_desconsiderar_grava_correcao_sem_apagar_o_fato(self):
        conversa = self.db.criar_conversa(funil="b2c", estagio="SX")
        self.db.fatos.append((conversa.id, "recusa_explicita", "não quero mais", None, None))
        self.db.gravar_evento_estagio(conversa.id, "S2", "SX", motivo="recusa_explicita")

        desconsiderar_recusa(self.db, conversa.id, por="marcos")

        # O fato original continua íntegro.
        self.assertTrue(self.db.fatos_da_conversa(conversa.id).get("recusa_explicita"))
        # E a correção foi registrada (§7), não uma reescrita do fato.
        correcao = [c for c in self.db.correcoes if c["conversa_id"] == conversa.id][0]
        self.assertEqual(correcao["campo"], "recusa_explicita")
        self.assertEqual(correcao["depois"], "desconsiderado")
        self.assertEqual(correcao["por"], "marcos")
        self.assertTrue(self.db.recusa_desconsiderada(conversa.id))

    def test_desconsiderar_reabre_conversa_presa_em_sx(self):
        """Requirement "Recusa desconsiderada permite avanço de novo": a
        conversa estava presa em SX por `recusa_explicita`, tinha chegado a
        S2 antes disso, e o cliente já respondeu de novo (bola com a Camu) —
        desconsiderar a recusa deve reabrir para o maior estágio já
        alcançado, sem esperar mensagem nova nenhuma."""
        conversa = self.db.criar_conversa(funil="b2c", estagio="SX")
        self.db.registrar_mensagem(conversa.id, "in", "oi de novo, mudei de ideia")
        self.db.fatos.append((conversa.id, "foto_pet_recebida", "manda foto", None, None))
        self.db.fatos.append((conversa.id, "recusa_explicita", "não quero mais", None, None))
        self.db.gravar_evento_estagio(conversa.id, "S1", "S2", motivo="foto_pet_recebida")
        self.db.gravar_evento_estagio(conversa.id, "S2", "SX", motivo="recusa_explicita")

        estado = desconsiderar_recusa(self.db, conversa.id, por="marcos")

        self.assertEqual(estado.estagio, "S2")
        self.assertNotEqual(estado.estagio, "S1")

    def test_sem_desconsideracao_conversa_continua_presa(self):
        """Regressão: sem a ação nova, `recalcular` sozinho continua
        respeitando "recusa é fechamento duro e não reabre"."""
        from camucrm.pipeline import recalcular

        conversa = self.db.criar_conversa(funil="b2c", estagio="SX")
        self.db.registrar_mensagem(conversa.id, "in", "oi de novo")
        self.db.fatos.append((conversa.id, "foto_pet_recebida", "manda foto", None, None))
        self.db.fatos.append((conversa.id, "recusa_explicita", "não quero mais", None, None))
        self.db.gravar_evento_estagio(conversa.id, "S1", "S2", motivo="foto_pet_recebida")
        self.db.gravar_evento_estagio(conversa.id, "S2", "SX", motivo="recusa_explicita")

        estado = recalcular(self.db, self.db.get_conversa(conversa.id))

        self.assertEqual(estado.estagio, "SX")


if __name__ == "__main__":
    unittest.main()
