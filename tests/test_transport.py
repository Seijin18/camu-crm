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


class TesteMidiaSemLegendaGeraMarcador(unittest.TestCase):
    """Mudança de comportamento: mídia sem legenda deixa de ser `None`.

    Antes, `audioMessage`/`stickerMessage`/`contactMessage`/`locationMessage`/
    `liveLocationMessage` faziam `receber()` devolver `None` — evento
    descartado inteiro, sem gravar nada, `bola_com` congelado.
    """

    def setUp(self):
        self.t = EvolutionTransporte("http://x", "k", "i")

    def test_audio_vira_marcador(self):
        recebido = self.t.receber(evento(message={"audioMessage": {}}))
        self.assertEqual(recebido.texto, "[áudio recebido]")

    def test_figurinha_vira_marcador(self):
        recebido = self.t.receber(evento(message={"stickerMessage": {}}))
        self.assertEqual(recebido.texto, "[figurinha recebida]")

    def test_contato_vira_marcador(self):
        recebido = self.t.receber(evento(message={"contactMessage": {}}))
        self.assertEqual(recebido.texto, "[contato recebido]")

    def test_localizacao_vira_marcador(self):
        recebido = self.t.receber(evento(message={"locationMessage": {}}))
        self.assertEqual(recebido.texto, "[localização recebida]")

    def test_localizacao_ao_vivo_vira_marcador(self):
        recebido = self.t.receber(evento(message={"liveLocationMessage": {}}))
        self.assertEqual(recebido.texto, "[localização recebida]")

    def test_reacao_continua_none(self):
        """Regressão de `test_evento_sem_texto_e_ignorado`: nenhuma mudança."""
        self.assertIsNone(self.t.receber(evento(message={"reactionMessage": {}})))


class TesteEnvelopeEfemeroEViewOnce(unittest.TestCase):
    """Parte 2 (ampliação): `.message` interno é desembrulhado recursivamente."""

    def setUp(self):
        self.t = EvolutionTransporte("http://x", "k", "i")

    def test_texto_puro_dentro_de_ephemeral_e_preservado(self):
        recebido = self.t.receber(
            evento(message={"ephemeralMessage": {"message": {"conversation": "oi"}}})
        )
        self.assertEqual(recebido.texto, "oi")

    def test_texto_puro_dentro_de_view_once_e_preservado(self):
        recebido = self.t.receber(
            evento(
                message={
                    "viewOnceMessage": {
                        "message": {"extendedTextMessage": {"text": "quanto custa?"}}
                    }
                }
            )
        )
        self.assertEqual(recebido.texto, "quanto custa?")

    def test_texto_puro_dentro_de_view_once_v2_e_preservado(self):
        recebido = self.t.receber(
            evento(
                message={"viewOnceMessageV2": {"message": {"conversation": "oi de novo"}}}
            )
        )
        self.assertEqual(recebido.texto, "oi de novo")

    def test_midia_sem_legenda_dentro_de_envelope_gera_marcador(self):
        recebido = self.t.receber(
            evento(message={"ephemeralMessage": {"message": {"audioMessage": {}}}})
        )
        self.assertEqual(recebido.texto, "[áudio recebido]")

    def test_legenda_de_midia_dentro_de_envelope_e_preservada(self):
        recebido = self.t.receber(
            evento(
                message={
                    "viewOnceMessage": {
                        "message": {"imageMessage": {"caption": "olha ele aqui"}}
                    }
                }
            )
        )
        self.assertEqual(recebido.texto, "olha ele aqui")

    def test_conteudo_interno_nao_reconhecido_vira_none(self):
        """A recursão não inventa marcador para o que a chamada direta
        também não reconheceria."""
        self.assertIsNone(
            self.t.receber(
                evento(message={"ephemeralMessage": {"message": {"reactionMessage": {}}}})
            )
        )

    def test_envelope_sem_message_interno_vira_none(self):
        self.assertIsNone(self.t.receber(evento(message={"viewOnceMessage": {}})))


class TesteDeviceSentMessage(unittest.TestCase):
    """Eco de mensagem enviada por outro dispositivo linkado (§5, `bola_com`)."""

    def setUp(self):
        self.t = EvolutionTransporte("http://x", "k", "i")

    def test_texto_interno_conta_como_eco_de_saida(self):
        recebido = self.t.receber(
            evento(
                key={"remoteJid": "5511999@s.whatsapp.net", "id": "D1", "fromMe": True},
                message={
                    "deviceSentMessage": {
                        "destinationJid": "5511999@s.whatsapp.net",
                        "message": {"conversation": "Oi! Ja te respondo"},
                    }
                },
            )
        )
        self.assertEqual(recebido.texto, "Oi! Ja te respondo")
        self.assertEqual(recebido.direcao, "out")

    def test_conteudo_interno_nao_reconhecido_vira_none(self):
        self.assertIsNone(
            self.t.receber(
                evento(
                    key={"remoteJid": "5511999@s.whatsapp.net", "id": "D2", "fromMe": True},
                    message={
                        "deviceSentMessage": {"message": {"reactionMessage": {}}}
                    },
                )
            )
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


class TesteListarInstancias(unittest.TestCase):
    """Change `escolher-instancia-no-envio-prospeccao`: `listar_instancias`
    normaliza os dois formatos de `fetchInstances` da Evolution API."""

    def _resposta(self, corpo):
        from unittest import mock

        resp = mock.Mock()
        resp.content = b"x"
        resp.json.return_value = corpo
        resp.raise_for_status.return_value = None
        return resp

    def test_formato_v2_plano(self):
        from unittest import mock

        t = EvolutionTransporte("http://x", "k")
        corpo = [
            {"name": "camu_whatsapp", "connectionStatus": "open"},
            {"name": "pessoal-felipe", "connectionStatus": "close"},
        ]
        with mock.patch("camucrm.transport.evolution.requests.get", return_value=self._resposta(corpo)):
            instancias = t.listar_instancias()
        self.assertEqual([i.nome for i in instancias], ["camu_whatsapp", "pessoal-felipe"])
        self.assertEqual([i.conectada for i in instancias], [True, False])

    def test_formato_v1_aninhado(self):
        from unittest import mock

        t = EvolutionTransporte("http://x", "k")
        corpo = [{"instance": {"instanceName": "camu_whatsapp", "status": "open"}}]
        with mock.patch("camucrm.transport.evolution.requests.get", return_value=self._resposta(corpo)):
            instancias = t.listar_instancias()
        self.assertEqual(instancias[0].nome, "camu_whatsapp")
        self.assertTrue(instancias[0].conectada)

    def test_sem_credencial_recusa(self):
        from camucrm.transport.base import TransporteError

        with self.assertRaises(TransporteError):
            EvolutionTransporte().listar_instancias()

    def test_rede_fora_do_ar_vira_transporte_error(self):
        from unittest import mock

        import requests as requests_mod

        from camucrm.transport.base import TransporteError

        t = EvolutionTransporte("http://x", "k")
        with mock.patch(
            "camucrm.transport.evolution.requests.get",
            side_effect=requests_mod.ConnectionError("recusado"),
        ):
            with self.assertRaises(TransporteError):
                t.listar_instancias()


class TesteBroadcastEStatusIgnorados(unittest.TestCase):
    """Change `identificacao-e-relogio-confiaveis`: nunca é conversa 1:1."""

    def setUp(self):
        self.t = EvolutionTransporte("http://x", "k", "i")

    def test_status_broadcast_e_ignorado(self):
        self.assertIsNone(
            self.t.receber(
                evento(key={"remoteJid": "status@broadcast", "id": "S1", "fromMe": False})
            )
        )

    def test_lista_de_transmissao_e_ignorada(self):
        self.assertIsNone(
            self.t.receber(
                evento(
                    key={
                        "remoteJid": "120363012345678901@broadcast",
                        "id": "S2",
                        "fromMe": False,
                    }
                )
            )
        )


class TesteLidSemPnConfiavelERecusado(unittest.TestCase):
    """Sem campo de PN confiável, `@lid` nunca vira contato fantasma."""

    def setUp(self):
        self.t = EvolutionTransporte("http://x", "k", "i")

    def test_lid_sem_pn_alternativo_e_recusado(self):
        self.assertIsNone(
            self.t.receber(
                evento(key={"remoteJid": "987654321@lid", "id": "L1", "fromMe": False})
            )
        )

    def test_lid_com_pn_alternativo_funciona(self):
        recebido = self.t.receber(
            evento(
                key={
                    "remoteJid": "987654321@lid",
                    "remoteJidAlt": "5511999998888@s.whatsapp.net",
                    "id": "L2",
                    "fromMe": False,
                }
            )
        )
        self.assertIsNotNone(recebido)
        self.assertEqual(recebido.telefone, "5511999998888")


class TesteTimestampClampado(unittest.TestCase):
    """Change `identificacao-e-relogio-confiaveis`: relógio confiável."""

    def setUp(self):
        self.t = EvolutionTransporte("http://x", "k", "i")

    def test_timestamp_futuro_e_clampado_para_agora(self):
        futuro = int(datetime.now(timezone.utc).timestamp()) + 3600 * 24 * 365
        recebido = self.t.receber(evento(messageTimestamp=futuro))
        self.assertLessEqual(recebido.enviada_em, datetime.now(timezone.utc))
        self.assertLess(
            abs((datetime.now(timezone.utc) - recebido.enviada_em).total_seconds()), 5
        )

    def test_timestamp_implausivelmente_antigo_cai_em_agora(self):
        antigo = int(datetime(2015, 1, 1, tzinfo=timezone.utc).timestamp())
        recebido = self.t.receber(evento(messageTimestamp=antigo))
        self.assertLess(
            abs((datetime.now(timezone.utc) - recebido.enviada_em).total_seconds()), 5
        )

    def test_timestamp_plausivel_passa_sem_alteracao(self):
        recebido = self.t.receber(evento(messageTimestamp=1756200000))
        self.assertEqual(
            recebido.enviada_em, datetime.fromtimestamp(1756200000, tz=timezone.utc)
        )

    def test_timestamp_futuro_nao_trava_ultimo_inbound_apos_mensagem_real(self):
        """Reproduz o cenário do `GREATEST` (§ proposal): um timestamp
        corrompido no futuro distante (ano 2030) não deve "vencer para
        sempre" contra uma mensagem real do dia seguinte. Antes da correção,
        `GREATEST(2030, <timestamp real do dia seguinte>)` ficaria preso em
        2030 para sempre; depois da correção, o valor de 2030 é clampado ao
        `agora()` do momento em que chegou, e a mensagem real subsequente —
        cronologicamente depois — produz um `enviada_em` maior, permitindo
        que `ultimo_inbound` avance corretamente.
        """
        from unittest import mock

        from camucrm.transport import evolution as evolution_mod

        t0 = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 8, 28, 9, 0, 0, tzinfo=timezone.utc)  # dia seguinte
        timestamp_corrompido_2030 = int(datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp())
        timestamp_real_dia_seguinte = int(t1.timestamp())

        class DatetimeFalso(datetime):
            _agora = t0

            @classmethod
            def now(cls, tz=None):
                return cls._agora

        with mock.patch.object(evolution_mod, "datetime", DatetimeFalso):
            DatetimeFalso._agora = t0
            primeiro = self.t.receber(
                evento(
                    messageTimestamp=timestamp_corrompido_2030,
                    key={"remoteJid": "5511999998888@s.whatsapp.net", "id": "T1", "fromMe": False},
                )
            )
            DatetimeFalso._agora = t1
            segundo = self.t.receber(
                evento(
                    messageTimestamp=timestamp_real_dia_seguinte,
                    key={"remoteJid": "5511999998888@s.whatsapp.net", "id": "T2", "fromMe": False},
                )
            )

        self.assertEqual(primeiro.enviada_em, t0)
        self.assertEqual(segundo.enviada_em, t1)
        self.assertGreater(segundo.enviada_em, primeiro.enviada_em)


class TesteNomeNoEco(unittest.TestCase):
    """`pushName` no eco é o perfil da Camu, não o do cliente."""

    def setUp(self):
        self.t = EvolutionTransporte()

    def test_inbound_traz_o_nome_do_cliente(self):
        self.assertEqual(self.t.receber(evento()).nome, "Ana")

    def test_eco_nao_traz_nome(self):
        recebido = self.t.receber(
            evento(
                key={"remoteJid": "5511999@s.whatsapp.net", "id": "M9", "fromMe": True},
                pushName="Camu",
            )
        )
        self.assertEqual(recebido.direcao, "out")
        self.assertIsNone(recebido.nome)
