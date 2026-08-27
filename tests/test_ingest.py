"""Ingestão (`camucrm/ingest.py`): o caminho único de entrada."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm.ingest import ingerir  # noqa: E402
from camucrm.transport.base import EventoRecebido  # noqa: E402
from camucrm.transport.evolution import EvolutionTransporte  # noqa: E402

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def payload(texto="oi", ident="M1", from_me=False, telefone="5511999998888"):
    return {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": f"{telefone}@s.whatsapp.net",
                "id": ident,
                "fromMe": from_me,
            },
            "message": {"conversation": texto},
            "messageTimestamp": int((AGORA - timedelta(hours=1)).timestamp()),
            "pushName": "Ana",
        },
    }


class TesteIngestao(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        self.transporte = EvolutionTransporte("http://x", "k", "i")

    def _ingerir(self, bruto):
        return ingerir(self.db, self.transporte.receber(bruto), agora=AGORA)

    def test_cria_contato_conversa_e_mensagem(self):
        resultado = self._ingerir(payload())
        self.assertFalse(resultado.ignorada)
        self.assertEqual(resultado.contato, "Ana")
        self.assertEqual(resultado.estado.estagio, "S1")

    def test_evento_que_nao_e_mensagem_e_ignorado_sem_erro(self):
        resultado = ingerir(self.db, self.transporte.receber({"event": "connection"}))
        self.assertTrue(resultado.ignorada)
        self.assertEqual(self.db.contatos, {})

    def test_reentrega_nao_duplica_nem_move_o_relogio(self):
        """Webhook reentregue não pode fazer a temperatura oscilar."""
        primeiro = self._ingerir(payload())
        cid = primeiro.conversa_id
        antes = self.db.conversas[cid].ultimo_inbound

        repetido = self._ingerir(payload())

        self.assertTrue(repetido.duplicada)
        self.assertEqual(len(self.db.mensagens[cid]), 1)
        self.assertEqual(self.db.conversas[cid].ultimo_inbound, antes)

    def test_mensagem_seguinte_do_mesmo_numero_reusa_a_conversa(self):
        primeiro = self._ingerir(payload(ident="M1"))
        segundo = self._ingerir(payload(texto="ainda ta ai?", ident="M2"))
        self.assertEqual(primeiro.conversa_id, segundo.conversa_id)

    def test_eco_da_propria_mensagem_vira_outbound(self):
        """O eco decide a bola, e a bola é o sinal de maior peso (§5)."""
        self._ingerir(payload(ident="M1"))
        resultado = self._ingerir(
            payload(texto="Oi! Manda uma foto?", ident="M2", from_me=True)
        )
        self.assertEqual(resultado.estado.sinais.bola_com, "cliente")

    def test_contato_nasce_b2c(self):
        """Classificar petshop por heurística seria inferência (§1)."""
        self._ingerir(payload())
        self.assertEqual(next(iter(self.db.contatos.values())).tipo, "b2c")


class TesteMidiaSemLegendaNaIngestao(unittest.TestCase):
    """Change `mensagem-sem-texto-preservada`: o marcador percorre o mesmo
    caminho de texto normal — grava mensagem, atualiza `bola_com`, e nunca
    vira evidência de fato na extração (fora de escopo deste change: o
    marcador não é literal de nenhum fato do §2)."""

    def setUp(self):
        self.db = FakeDatabase()
        self.transporte = EvolutionTransporte("http://x", "k", "i")

    def _payload_audio(self, ident="A1", telefone="5511999998888"):
        return {
            "event": "messages.upsert",
            "data": {
                "key": {
                    "remoteJid": f"{telefone}@s.whatsapp.net",
                    "id": ident,
                    "fromMe": False,
                },
                "message": {"audioMessage": {}},
                "messageTimestamp": int((AGORA - timedelta(hours=1)).timestamp()),
                "pushName": "Ana",
            },
        }

    def test_audio_grava_mensagem_e_atualiza_bola_com(self):
        resultado = ingerir(
            self.db, self.transporte.receber(self._payload_audio()), agora=AGORA
        )
        self.assertFalse(resultado.ignorada)
        cid = resultado.conversa_id
        self.assertEqual(self.db.mensagens[cid][0][2], "[áudio recebido]")
        # `bola_com == "camu"`: o cliente falou por último, resposta é dívida
        # nossa (§5, `rules/sinais.py::Sinais.bola_com`) — não é o mesmo
        # sentido de "a bola está do lado do cliente".
        self.assertEqual(resultado.estado.sinais.bola_com, "camu")
        self.assertEqual(self.db.conversas[cid].ultimo_inbound, AGORA - timedelta(hours=1))

    def test_audio_nao_produz_fato_nenhum_na_extracao(self):
        from camucrm.extraction.extractor import Extrator
        from camucrm.llm import FakeLlm

        resultado = ingerir(
            self.db, self.transporte.receber(self._payload_audio()), agora=AGORA
        )
        cid = resultado.conversa_id

        extraido = Extrator(self.db, FakeLlm(['{"objecao": null, "evidencias": {}}'])).processar_conversa(
            cid, agora=AGORA
        )
        self.assertEqual(extraido.mensagens_processadas, 1)
        fatos = self.db.fatos_da_conversa(cid)
        self.assertFalse(any(fatos.values()))


class TesteDedupeSemExternaId(unittest.TestCase):
    """Change `ingestao-a-prova-de-falha`, spec.md "Evento sem externa_id
    ainda é protegido contra duplicação" — hash estável do payload cru como
    `externa_id` sintético (design.md)."""

    def setUp(self):
        self.db = FakeDatabase()
        self.transporte = EvolutionTransporte("http://x", "k", "i")

    def _payload_sem_key_id(self, texto="oi", telefone="5511999998888"):
        return {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": f"{telefone}@s.whatsapp.net", "fromMe": False},
                "message": {"conversation": texto},
                "messageTimestamp": int((AGORA - timedelta(hours=1)).timestamp()),
                "pushName": "Ana",
            },
        }

    def test_reentrega_de_evento_sem_key_id_nao_duplica(self):
        bruto = self._payload_sem_key_id()
        primeiro = ingerir(self.db, self.transporte.receber(bruto), agora=AGORA)
        segundo = ingerir(self.db, self.transporte.receber(bruto), agora=AGORA)

        self.assertFalse(primeiro.duplicada)
        self.assertTrue(segundo.duplicada)
        self.assertEqual(len(self.db.mensagens[primeiro.conversa_id]), 1)

    def test_payloads_diferentes_sem_key_id_nao_colidem(self):
        primeiro = ingerir(
            self.db, self.transporte.receber(self._payload_sem_key_id("oi")), agora=AGORA
        )
        segundo = ingerir(
            self.db,
            self.transporte.receber(self._payload_sem_key_id("outra mensagem")),
            agora=AGORA,
        )
        self.assertFalse(segundo.duplicada)
        self.assertEqual(len(self.db.mensagens[primeiro.conversa_id]), 2)

    def test_evento_com_key_id_continua_usando_o_id_real_nao_o_hash(self):
        """Evento normal (com `key.id`) não muda de comportamento."""
        from camucrm.ingest import _externa_id_efetivo

        evento = self.transporte.receber(payload(ident="M1"))
        self.assertEqual(_externa_id_efetivo(evento), "M1")

    def test_evento_sem_bruto_nem_externa_id_no_hash_devolve_none(self):
        """Evento construído direto (sem payload cru) não tem o que
        hashear — mesmo comportamento de antes deste change."""
        from camucrm.ingest import _externa_id_efetivo

        evento = EventoRecebido(
            telefone="5511900000000", texto="oi", enviada_em=AGORA
        )
        self.assertIsNone(_externa_id_efetivo(evento))


class TesteTransacaoUnica(unittest.TestCase):
    """Change `ingestao-a-prova-de-falha`, spec.md "Cadeia de ingestão é
    transacional" — `ingerir()` encadeia os três dentro de
    `db.transacao()` (o rollback de verdade é provado contra Postgres real
    em `tests/integration/`; aqui só confere que o caminho usa o
    contextmanager em vez de três transações soltas)."""

    def test_upsert_conversa_e_mensagem_rodam_dentro_de_transacao(self):
        db = FakeDatabase()
        chamadas = []
        original = db.transacao

        from contextlib import contextmanager

        @contextmanager
        def espiao():
            chamadas.append("entrou")
            with original() as conn:
                yield conn
            chamadas.append("saiu")

        db.transacao = espiao
        ingerir(db, EvolutionTransporte().receber(payload()), agora=AGORA)
        self.assertEqual(chamadas, ["entrou", "saiu"])


class TesteEventoDireto(unittest.TestCase):
    def test_aceita_evento_ja_normalizado(self):
        db = FakeDatabase()
        resultado = ingerir(
            db,
            EventoRecebido(
                telefone="5511911112222",
                texto="oi",
                enviada_em=AGORA - timedelta(hours=2),
                nome="Bruno",
                externa_id="X1",
            ),
            agora=AGORA,
        )
        self.assertEqual(resultado.contato, "Bruno")


if __name__ == "__main__":
    unittest.main()


class TesteNomeDoContato(unittest.TestCase):
    def test_resposta_da_camu_nao_renomeia_o_contato(self):
        """Senão a fila do dia lista "Camu" no lugar de quem está esperando."""
        db = FakeDatabase()
        transporte = EvolutionTransporte()
        ingerir(db, transporte.receber(payload(ident="N1")), agora=AGORA)
        ingerir(
            db,
            transporte.receber(
                {
                    "data": {
                        "key": {
                            "remoteJid": "5511999998888@s.whatsapp.net",
                            "id": "N2",
                            "fromMe": True,
                        },
                        "message": {"conversation": "Boa tarde, tudo bem?"},
                        "messageTimestamp": int(AGORA.timestamp()),
                        "pushName": "Camu",
                    }
                }
            ),
            agora=AGORA,
        )
        self.assertEqual(next(iter(db.contatos.values())).nome, "Ana")
