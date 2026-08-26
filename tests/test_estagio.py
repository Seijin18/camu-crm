"""Derivação de estágio (§3) e a garantia de não-regressão."""

import unittest
from datetime import datetime, timedelta, timezone

from camucrm.rules.estagio import (
    ORIGEM_BACKFILL,
    Derivacao,
    derive,
    reabrir,
    transicao,
)
from camucrm.rules.sinais import Mensagem, construir_sinais

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def sinais(msgs=(), **kwargs):
    return construir_sinais(msgs, agora=AGORA, **kwargs)


class TesteFunilB2C(unittest.TestCase):
    def test_s0_conversa_sem_inbound(self):
        self.assertEqual(derive({}, sinais()).estagio, "S0")

    def test_s1_mensagem_espontanea(self):
        s = sinais([Mensagem("in", AGORA - timedelta(hours=1))])
        self.assertEqual(derive({}, s).estagio, "S1")

    def test_s2_e_o_estagio_chave(self):
        s = sinais([Mensagem("in", AGORA - timedelta(hours=1))])
        self.assertEqual(derive({"foto_pet_recebida": True}, s).estagio, "S2")

    def test_s4_preco_apresentado(self):
        s = sinais([Mensagem("out", AGORA - timedelta(hours=1))])
        d = derive({"foto_pet_recebida": True, "previa_enviada": True,
                    "preco_apresentado": True}, s)
        self.assertEqual(d.estagio, "S4")

    def test_s5_respondeu_ao_preco_sem_recusar(self):
        preco_em = AGORA - timedelta(hours=3)
        s = sinais(
            [Mensagem("out", preco_em), Mensagem("in", AGORA - timedelta(hours=1))],
            preco_apresentado_em=preco_em,
        )
        d = derive({"preco_apresentado": True}, s)
        self.assertEqual(d.estagio, "S5")

    def test_recusa_explicita_leva_a_sx(self):
        s = sinais([Mensagem("in", AGORA - timedelta(hours=1))])
        self.assertEqual(derive({"recusa_explicita": True}, s).estagio, "SX")

    def test_quatorze_dias_sem_resposta_leva_a_sx(self):
        s = sinais([Mensagem("in", AGORA - timedelta(days=15))])
        self.assertEqual(derive({"foto_pet_recebida": True}, s).estagio, "SX")

    def test_ganho_vence_o_silencio(self):
        """Quem pagou e sumiu não é lead perdido."""
        s = sinais([Mensagem("in", AGORA - timedelta(days=30))], ganho=True)
        self.assertEqual(derive({}, s).estagio, "S6")


class TesteFunilB2B(unittest.TestCase):
    def test_p0_shortlist(self):
        self.assertEqual(derive({}, sinais(funil="b2b")).estagio, "P0")

    def test_p1_msg1_enviada(self):
        s = sinais([Mensagem("out", AGORA - timedelta(days=1))], funil="b2b")
        self.assertEqual(derive({}, s).estagio, "P1")

    def test_p2_autorizou(self):
        s = sinais([Mensagem("out", AGORA - timedelta(days=1)),
                    Mensagem("in", AGORA - timedelta(hours=20))], funil="b2b")
        self.assertEqual(derive({"autorizou_envio_material": True}, s).estagio, "P2")

    def test_p3_msg2_apos_autorizacao(self):
        autorizou = AGORA - timedelta(hours=20)
        s = sinais(
            [Mensagem("out", AGORA - timedelta(days=1)), Mensagem("in", autorizou),
             Mensagem("out", AGORA - timedelta(hours=19))],
            funil="b2b", autorizou_em=autorizou,
        )
        self.assertEqual(derive({"autorizou_envio_material": True}, s).estagio, "P3")

    def test_p6_e_a_unica_validacao_real(self):
        s = sinais(funil="b2b", consignacao_assinada=True, primeira_reposicao=True)
        self.assertEqual(derive({}, s).estagio, "P6")

    def test_dois_followups_sem_retorno_descartam(self):
        s = sinais([Mensagem("out", AGORA - timedelta(days=10))],
                   funil="b2b", followups_enviados=2,
                   ultimo_followup_em=AGORA - timedelta(days=3))
        self.assertEqual(derive({}, s).estagio, "PX")


class TesteNaoRegressao(unittest.TestCase):
    def test_estagio_menor_e_ignorado(self):
        self.assertIsNone(transicao("S4", Derivacao("S2", "foto_pet_recebida")))

    def test_estagio_igual_nao_gera_evento(self):
        """Idempotência (§2): reprocessar não pode duplicar evento."""
        self.assertIsNone(transicao("S3", Derivacao("S3", "previa_enviada")))

    def test_avanco_gera_evento(self):
        t = transicao("S1", Derivacao("S2", "foto_pet_recebida"))
        self.assertEqual((t.de, t.para), ("S1", "S2"))

    def test_terminal_e_saida_nao_regressao(self):
        t = transicao("S4", Derivacao("SX", "recusa_explicita"))
        self.assertEqual(t.para, "SX")

    def test_silencio_nao_reabre_conversa_terminal(self):
        self.assertIsNone(transicao("SX", Derivacao("S2", "foto_pet_recebida")))

    def test_marco_manual_reabre_terminal(self):
        t = transicao("SX", Derivacao("S6", "ganho manual"))
        self.assertEqual(t.para, "S6")

    def test_origem_invalida_levanta(self):
        with self.assertRaises(ValueError):
            transicao("S1", Derivacao("S2", "x"), origem="inventada")

    def test_origem_backfill_e_preservada(self):
        t = transicao("S1", Derivacao("S2", "foto"), origem=ORIGEM_BACKFILL)
        self.assertEqual(t.origem, ORIGEM_BACKFILL)


class TesteReabertura(unittest.TestCase):
    def test_volta_ao_maior_estagio_alcancado(self):
        t = reabrir("SX", "S3")
        self.assertEqual((t.de, t.para), ("SX", "S3"))

    def test_sem_historico_nao_reabre(self):
        self.assertIsNone(reabrir("SX", None))

    def test_conversa_nao_terminal_nao_reabre(self):
        self.assertIsNone(reabrir("S2", "S3"))


if __name__ == "__main__":
    unittest.main()
