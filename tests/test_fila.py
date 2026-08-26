"""Fila de follow-up (§6): a saída do sistema."""

import unittest
from datetime import datetime, timedelta, timezone

from camucrm.rules.fila import Candidato, montar_fila, prioridade
from camucrm.rules.sinais import Mensagem, construir_sinais
from camucrm.rules.temperatura import classificar

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def candidato(cid, estagio, msgs, funil="b2c", nome=None, **kwargs):
    s = construir_sinais(msgs, agora=AGORA, funil=funil, **kwargs)
    return Candidato(cid, nome or f"c{cid}", funil, estagio, classificar(s), s)


class TestePrioridades(unittest.TestCase):
    def test_p1_quente_com_bola_na_camu(self):
        c = candidato(1, "S4", [Mensagem("out", AGORA - timedelta(hours=5)),
                                Mensagem("in", AGORA - timedelta(hours=1))])
        self.assertEqual(prioridade(c)[0], 1)

    def test_p2_esfriando_em_s2(self):
        c = candidato(2, "S2", [Mensagem("in", AGORA - timedelta(days=3)),
                                Mensagem("out", AGORA - timedelta(days=3))])
        self.assertEqual(prioridade(c)[0], 2)

    def test_p3_esfriando_em_p2(self):
        c = candidato(3, "P2", [Mensagem("in", AGORA - timedelta(days=4)),
                                Mensagem("out", AGORA - timedelta(days=4))], funil="b2b")
        self.assertEqual(prioridade(c)[0], 3)

    def test_p4_frio_sem_followup(self):
        c = candidato(4, "S4", [Mensagem("in", AGORA - timedelta(days=8)),
                                Mensagem("out", AGORA - timedelta(days=8))])
        self.assertEqual(prioridade(c)[0], 4)


class TesteTetoDeFollowups(unittest.TestCase):
    def test_frio_com_um_followup_nao_aparece(self):
        """§6, linha "—": encerrado. É o teto de 2 sendo respeitado na fila."""
        c = candidato(5, "S3", [Mensagem("in", AGORA - timedelta(days=9)),
                                Mensagem("out", AGORA - timedelta(days=6))],
                      followups_enviados=1, ultimo_followup_em=AGORA - timedelta(days=6))
        self.assertIsNone(prioridade(c))

    def test_encerrado_nunca_aparece(self):
        c = candidato(6, "S3", [Mensagem("out", AGORA - timedelta(days=12))],
                      followups_enviados=2, ultimo_followup_em=AGORA - timedelta(days=4))
        self.assertIsNone(prioridade(c))


class TesteForaDaTabela(unittest.TestCase):
    def test_esfriando_em_estagio_barato_nao_entra(self):
        """S1 não está na tabela de §6 — e o que não está, não aparece."""
        c = candidato(7, "S1", [Mensagem("in", AGORA - timedelta(days=3)),
                                Mensagem("out", AGORA - timedelta(days=3))])
        self.assertIsNone(prioridade(c))

    def test_morno_nao_entra(self):
        c = candidato(8, "S2", [Mensagem("in", AGORA - timedelta(hours=30)),
                                Mensagem("out", AGORA - timedelta(hours=20))])
        self.assertIsNone(prioridade(c))


class TesteMontagem(unittest.TestCase):
    def test_teto_de_dez_nomes(self):
        candidatos = [
            candidato(i, "S4", [Mensagem("out", AGORA - timedelta(days=2)),
                                Mensagem("in", AGORA - timedelta(hours=i + 1))])
            for i in range(25)
        ]
        self.assertEqual(len(montar_fila(candidatos)), 10)

    def test_ordena_por_prioridade_depois_por_espera(self):
        candidatos = [
            candidato(1, "S2", [Mensagem("in", AGORA - timedelta(days=3)),
                                Mensagem("out", AGORA - timedelta(days=3))]),
            candidato(2, "S4", [Mensagem("out", AGORA - timedelta(hours=9)),
                                Mensagem("in", AGORA - timedelta(hours=2))]),
            candidato(3, "S4", [Mensagem("out", AGORA - timedelta(hours=9)),
                                Mensagem("in", AGORA - timedelta(hours=5))]),
        ]
        fila = montar_fila(candidatos)
        self.assertEqual([i.conversa_id for i in fila], [3, 2, 1])

    def test_fila_vazia_e_valida(self):
        self.assertEqual(montar_fila([]), [])


if __name__ == "__main__":
    unittest.main()
