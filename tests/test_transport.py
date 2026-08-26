"""Transporte (§11): fronteira única, e envio sempre com humano nomeado."""

import unittest
from datetime import datetime, timezone

from camucrm.transport import (
    ConsoleTransporte,
    Destinatario,
    EnvioNaoAutorizadoError,
    Transporte,
)
from camucrm.transport.evolution import EvolutionTransporte


def evento(**kwargs):
    dados = {
        "key": {"remoteJid": "5511999998888@s.whatsapp.net", "id": "MSG1", "fromMe": False},
        "message": {"conversation": "oi"},
        "messageTimestamp": 1756200000,
        "pushName": "Ana",
    }
    dados.update(kwargs)
    return {"event": "messages.upsert", "data": dados}


class TesteEnvioExigeHumano(unittest.TestCase):
    def test_sem_aprovado_por_recusa(self):
        with self.assertRaises(EnvioNaoAutorizadoError):
            ConsoleTransporte(silencioso=True).enviar(
                Destinatario("5511999"), "oi", aprovado_por=""
            )

    def test_espaco_em_branco_nao_conta_como_nome(self):
        with self.assertRaises(EnvioNaoAutorizadoError):
            ConsoleTransporte(silencioso=True).enviar(
                Destinatario("5511999"), "oi", aprovado_por="   "
            )

    def test_com_nome_registra(self):
        t = ConsoleTransporte(silencioso=True)
        t.enviar(Destinatario("5511999", "Ana"), "oi", aprovado_por="Marcos")
        self.assertEqual(t.enviados[0][2], "Marcos")


class TesteContrato(unittest.TestCase):
    def test_adaptadores_satisfazem_o_protocolo(self):
        self.assertIsInstance(ConsoleTransporte(), Transporte)
        self.assertIsInstance(EvolutionTransporte("http://x", "k", "i"), Transporte)


class TesteRecebimentoEvolution(unittest.TestCase):
    def setUp(self):
        self.t = EvolutionTransporte("http://x", "k", "i")

    def test_mensagem_simples(self):
        recebido = self.t.receber(evento())
        self.assertEqual(recebido.telefone, "5511999998888")
        self.assertEqual(recebido.texto, "oi")
        self.assertTrue(recebido.is_inbound)
        self.assertEqual(recebido.externa_id, "MSG1")

    def test_texto_estendido(self):
        recebido = self.t.receber(
            evento(message={"extendedTextMessage": {"text": "quanto custa?"}})
        )
        self.assertEqual(recebido.texto, "quanto custa?")

    def test_eco_da_propria_mensagem_e_outbound(self):
        """O eco importa: é o `ultimo_outbound` que decide a bola (§5)."""
        recebido = self.t.receber(
            evento(key={"remoteJid": "5511999@s.whatsapp.net", "id": "M2", "fromMe": True})
        )
        self.assertEqual(recebido.direcao, "out")

    def test_grupo_e_ignorado(self):
        self.assertIsNone(
            self.t.receber(evento(key={"remoteJid": "12345@g.us", "id": "M3"}))
        )

    def test_evento_sem_texto_e_ignorado(self):
        self.assertIsNone(self.t.receber(evento(message={"reactionMessage": {}})))

    def test_legenda_de_imagem_conta_como_texto(self):
        recebido = self.t.receber(
            evento(message={"imageMessage": {"caption": "olha ele aqui"}})
        )
        self.assertEqual(recebido.texto, "olha ele aqui")

    def test_payload_desconhecido_nao_explode(self):
        for ruim in ({}, {"data": None}, {"data": {"key": None}}, "texto solto"):
            with self.subTest(ruim=ruim):
                self.assertIsNone(self.t.receber(ruim))

    def test_timestamp_ilegivel_usa_agora(self):
        recebido = self.t.receber(evento(messageTimestamp="ontem"))
        self.assertLess(
            abs((datetime.now(timezone.utc) - recebido.enviada_em).total_seconds()), 5
        )


class TesteConsoleNaoEnvia(unittest.TestCase):
    def test_dry_run_nao_marca_como_entregue(self):
        resultado = ConsoleTransporte(silencioso=True).enviar(
            Destinatario("5511999"), "oi", aprovado_por="Marcos"
        )
        self.assertFalse(resultado.entregue)


if __name__ == "__main__":
    unittest.main()


class TesteRecepcaoNaoPrecisaDeCredencial(unittest.TestCase):
    """Receber é parsing puro; só enviar precisa de chave.

    A propriedade que isso compra (§10): o processo do webhook nunca carrega
    credencial de envio, então não envia por bug nem se for comprometido.
    """

    def test_parseia_sem_nenhuma_credencial(self):
        recebido = EvolutionTransporte().receber(evento())
        self.assertEqual(recebido.texto, "oi")

    def test_enviar_sem_credencial_recusa(self):
        from camucrm.transport.base import TransporteError

        with self.assertRaises(TransporteError) as ctx:
            EvolutionTransporte().enviar(
                Destinatario("5511999"), "oi", aprovado_por="Marcos"
            )
        self.assertIn("apenas para recepção", str(ctx.exception))

    def test_falta_de_aprovacao_vem_antes_da_falta_de_credencial(self):
        """Envio não autorizado é recusado mesmo com credencial completa."""
        with self.assertRaises(EnvioNaoAutorizadoError):
            EvolutionTransporte("http://x", "k", "i").enviar(
                Destinatario("5511999"), "oi", aprovado_por=""
            )

    def test_fabrica_em_modo_recepcao_nao_exige_ambiente(self):
        from camucrm.transport import criar_transporte

        transporte = criar_transporte("evolution", para_envio=False)
        self.assertEqual(transporte.nome, "evolution")
