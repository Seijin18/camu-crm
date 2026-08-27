"""Temperatura (§5): regra sobre tempo e reciprocidade, não sentimento."""

import unittest
from datetime import datetime, timedelta, timezone

from camucrm.rules.sinais import Mensagem, construir_sinais
from camucrm.rules.temperatura import classificar
from camucrm.taxonomia import (
    CAUSADA_POR_CAMU,
    CAUSADA_POR_CLIENTE,
    ENCERRADO,
    ESFRIANDO,
    FRIO,
    MORNO,
    QUENTE,
)

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def sinais(msgs=(), **kwargs):
    return construir_sinais(msgs, agora=AGORA, **kwargs)


class TesteClassificacao(unittest.TestCase):
    def test_quente_bola_com_a_camu(self):
        s = sinais([Mensagem("out", AGORA - timedelta(days=3)),
                    Mensagem("in", AGORA - timedelta(days=2))])
        c = classificar(s)
        self.assertEqual(c.temperatura, QUENTE)
        self.assertIn("bola", c.sinal)

    def test_quente_resposta_recente(self):
        s = sinais([Mensagem("in", AGORA - timedelta(hours=4)),
                    Mensagem("out", AGORA - timedelta(hours=1))])
        self.assertEqual(classificar(s).temperatura, QUENTE)

    def test_quente_avancou_hoje(self):
        s = sinais([Mensagem("in", AGORA - timedelta(hours=30)),
                    Mensagem("out", AGORA - timedelta(hours=20))],
                   avancou_estagio_em=AGORA - timedelta(hours=2))
        self.assertEqual(classificar(s).temperatura, QUENTE)

    def test_morno_bola_com_o_cliente_recente(self):
        s = sinais([Mensagem("in", AGORA - timedelta(hours=30)),
                    Mensagem("out", AGORA - timedelta(hours=20))])
        self.assertEqual(classificar(s).temperatura, MORNO)

    def test_esfriando_entre_48h_e_5_dias(self):
        s = sinais([Mensagem("in", AGORA - timedelta(days=3)),
                    Mensagem("out", AGORA - timedelta(days=3))])
        self.assertEqual(classificar(s).temperatura, ESFRIANDO)

    def test_frio_acima_de_5_dias(self):
        s = sinais([Mensagem("in", AGORA - timedelta(days=8)),
                    Mensagem("out", AGORA - timedelta(days=8))])
        self.assertEqual(classificar(s).temperatura, FRIO)

    def test_frio_com_um_followup_sem_retorno(self):
        s = sinais([Mensagem("in", AGORA - timedelta(days=6)),
                    Mensagem("out", AGORA - timedelta(days=3))],
                   followups_enviados=1, ultimo_followup_em=AGORA - timedelta(days=3))
        self.assertEqual(classificar(s).temperatura, FRIO)

    def test_encerrado_dois_followups(self):
        s = sinais([Mensagem("out", AGORA - timedelta(days=10))],
                   followups_enviados=2, ultimo_followup_em=AGORA - timedelta(days=2))
        self.assertEqual(classificar(s).temperatura, ENCERRADO)

    def test_encerrado_recusa_explicita(self):
        s = sinais([Mensagem("in", AGORA - timedelta(hours=1))])
        self.assertEqual(
            classificar(s, {"recusa_explicita": True}).temperatura, ENCERRADO
        )


class TesteAuditabilidade(unittest.TestCase):
    def test_toda_classificacao_traz_o_sinal_que_disparou(self):
        """§5: "quando você discordar, dá para ver qual sinal disparou"."""
        casos = [
            sinais([Mensagem("in", AGORA - timedelta(hours=1))]),
            sinais([Mensagem("in", AGORA - timedelta(days=3)),
                    Mensagem("out", AGORA - timedelta(days=3))]),
            sinais([Mensagem("out", AGORA - timedelta(days=10))], followups_enviados=2,
                   ultimo_followup_em=AGORA - timedelta(days=2)),
        ]
        for s in casos:
            with self.subTest(s=s):
                self.assertTrue(classificar(s).sinal)


class TesteReciprocidadeAcimaDeSimpatia(unittest.TestCase):
    def test_cliente_educado_e_sumido_e_frio(self):
        s = sinais([Mensagem("in", AGORA - timedelta(days=9),
                             "Muito obrigada, adorei tudo!"),
                    Mensagem("out", AGORA - timedelta(days=9))])
        self.assertEqual(classificar(s).temperatura, FRIO)

    def test_cliente_seco_e_rapido_e_quente(self):
        s = sinais([Mensagem("out", AGORA - timedelta(hours=3)),
                    Mensagem("in", AGORA - timedelta(minutes=2), "qnt")])
        self.assertEqual(classificar(s).temperatura, QUENTE)


class TesteAvancoCausadaPor(unittest.TestCase):
    """Change `estagio-reabertura-manual-e-relogio`: "avançou hoje" só
    esquenta quando o gatilho foi do cliente (§5)."""

    def test_avanco_causado_pela_camu_nao_vira_quente(self):
        s = sinais(
            [Mensagem("in", AGORA - timedelta(hours=30)),
             Mensagem("out", AGORA - timedelta(hours=20))],
            avancou_estagio_em=AGORA - timedelta(hours=2),
            avancou_causada_por=CAUSADA_POR_CAMU,
        )
        self.assertNotEqual(classificar(s).temperatura, QUENTE)

    def test_avanco_causado_pelo_cliente_continua_quente(self):
        s = sinais(
            [Mensagem("in", AGORA - timedelta(hours=30)),
             Mensagem("out", AGORA - timedelta(hours=20))],
            avancou_estagio_em=AGORA - timedelta(hours=2),
            avancou_causada_por=CAUSADA_POR_CLIENTE,
        )
        self.assertEqual(classificar(s).temperatura, QUENTE)

    def test_avanco_sem_causada_por_informada_continua_quente(self):
        """Regressão: chamadores antigos que não sabem de `causada_por`
        (`avancou_causada_por=None`, o default) preservam o comportamento
        anterior a este change."""
        s = sinais(
            [Mensagem("in", AGORA - timedelta(hours=30)),
             Mensagem("out", AGORA - timedelta(hours=20))],
            avancou_estagio_em=AGORA - timedelta(hours=2),
        )
        self.assertEqual(classificar(s).temperatura, QUENTE)


class TesteConversaSemInbound(unittest.TestCase):
    def test_petshop_abordado_ontem_e_morno(self):
        """B2B em P1 não tem "última mensagem dele" — mas tem silêncio a medir."""
        s = sinais([Mensagem("out", AGORA - timedelta(hours=20))], funil="b2b")
        self.assertEqual(classificar(s).temperatura, MORNO)

    def test_petshop_abordado_ha_uma_semana_e_frio(self):
        s = sinais([Mensagem("out", AGORA - timedelta(days=7))], funil="b2b")
        self.assertEqual(classificar(s).temperatura, FRIO)


if __name__ == "__main__":
    unittest.main()
