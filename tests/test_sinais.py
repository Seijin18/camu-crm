"""Sinais de conversa (`rules/sinais.py`) — clamp de `enviada_em` (change
`estagio-reabertura-manual-e-relogio`).

Requirement "Timestamp futuro não congela bola_com": um relógio de celular
adiantado não pode fazer uma mensagem virar "a última" para sempre, imune a
qualquer mensagem real que venha depois — mesma política de clamp
(`min(timestamp, agora())`) já aplicada na recepção pelo change
`identificacao-e-relogio-confiaveis`, agora na camada de regras.
"""

import unittest
from datetime import datetime, timedelta, timezone

from camucrm.rules.sinais import Mensagem, construir_sinais
from camucrm.taxonomia import BOLA_CAMU, BOLA_CLIENTE

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


class TesteClampDeTimestampFuturo(unittest.TestCase):
    def test_timestamp_futuro_nao_trava_ultimo_inbound(self):
        """Sem o clamp, a mensagem corrompida (relógio adiantado) venceria a
        ordenação para sempre — nenhuma mensagem real subsequente a
        superaria, porque o timestamp bruto dela é maior que qualquer
        `agora()` alcançável em qualquer recálculo futuro razoável."""
        corrompida = AGORA + timedelta(days=400)
        s = construir_sinais(
            [
                Mensagem("in", corrompida),
                Mensagem("in", AGORA),  # mensagem real, chegou depois
            ],
            agora=AGORA,
        )
        self.assertEqual(s.ultimo_inbound, AGORA)

    def test_timestamp_futuro_nao_congela_bola_com(self):
        """O bug descrito na proposta: mensagem com timestamp futuro vira "a
        última" e não é superada por mensagens reais subsequentes,
        congelando `bola_com` errado — aqui, travaria em BOLA_CAMU mesmo
        depois de a Camu ter respondido de verdade."""
        corrompida = AGORA + timedelta(days=400)
        s = construir_sinais(
            [
                Mensagem("in", corrompida),  # relógio do cliente adiantado
                Mensagem("out", AGORA),      # Camu responde de verdade, agora
            ],
            agora=AGORA,
        )
        self.assertEqual(s.bola_com, BOLA_CLIENTE)

    def test_sem_timestamp_futuro_comportamento_e_o_mesmo_de_sempre(self):
        """Regressão: nenhuma mensagem com timestamp normal (<= agora) é
        afetada pelo clamp."""
        s = construir_sinais(
            [
                Mensagem("out", AGORA - timedelta(hours=3)),
                Mensagem("in", AGORA - timedelta(hours=1)),
            ],
            agora=AGORA,
        )
        self.assertEqual(s.ultimo_inbound, AGORA - timedelta(hours=1))
        self.assertEqual(s.ultimo_outbound, AGORA - timedelta(hours=3))
        self.assertEqual(s.bola_com, BOLA_CAMU)


if __name__ == "__main__":
    unittest.main()
