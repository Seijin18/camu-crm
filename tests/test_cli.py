"""CLI (`camucrm/cli.py`): comportamento novo/ajustado pelo change
`ingestao-a-prova-de-falha` — `cmd_ingerir` alinhado ao webhook e o comando
`reprocessar-falhas`.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm import cli  # noqa: E402

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _payload_evolution(texto="oi", ident="M1", telefone="5511999998888"):
    """Payload real de mensagem da Evolution API (`messages.upsert`)."""
    return {
        "event": "messages.upsert",
        "data": {
            "key": {
                "remoteJid": f"{telefone}@s.whatsapp.net",
                "id": ident,
                "fromMe": False,
            },
            "message": {"conversation": texto},
            "messageTimestamp": int((AGORA - timedelta(hours=1)).timestamp()),
            "pushName": "Ana",
        },
    }


def _rodar(argv: list[str], payload=None) -> int:
    parser = cli.build_parser()
    args = parser.parse_args(argv)
    if payload is None:
        return args.func(args)
    stdin_falso = io.StringIO(json.dumps(payload))
    with patch.object(sys, "stdin", stdin_falso):
        return args.func(args)


class TesteCmdIngerirNaoFingeSucessoSilencioso(unittest.TestCase):
    """Requirement (spec.md): "cmd_ingerir não finge sucesso silencioso"."""

    def setUp(self):
        self.db = FakeDatabase()
        contexto = patch.object(cli, "_db", return_value=self.db)
        contexto.start()
        self.addCleanup(contexto.stop)

    def _tem_contato_ingerido(self) -> bool:
        # `FakeDatabase.get_or_create_conversa` (via `criar_conversa`) cria
        # um contato "fantasma" auxiliar a cada conversa nova, além do
        # contato real de `upsert_contato` — quirk pré-existente do fake,
        # não deste change. Por isso a asserção é "existe o contato certo",
        # não "existe exatamente um".
        return any(c.telefone == "5511999998888" for c in self.db.contatos.values())

    def test_sem_transporte_usa_evolution_por_padrao(self):
        """Antes deste change, sem `--transporte`, caía em `console` (padrão
        de `criar_transporte`) e um payload real da Evolution era ignorado
        como se fosse benigno."""
        codigo = _rodar(["ingerir"], payload=_payload_evolution())
        self.assertEqual(codigo, 0)
        self.assertTrue(self._tem_contato_ingerido())

    def test_transporte_console_sobre_payload_evolution_avisa_configuracao(self):
        saida = io.StringIO()
        with patch("sys.stdout", saida):
            codigo = _rodar(
                ["ingerir", "--transporte", "console"], payload=_payload_evolution()
            )
        self.assertEqual(codigo, 1)
        self.assertIn("CONFIGURAÇÃO", saida.getvalue())
        self.assertFalse(self._tem_contato_ingerido(), "não deveria ter processado nada")

    def test_transporte_console_sobre_evento_realmente_benigno_nao_avisa(self):
        """Um evento benigno de verdade (não tem cara de payload Evolution)
        continua ignorado sem o aviso de configuração — não é o mesmo bug."""
        saida = io.StringIO()
        with patch("sys.stdout", saida):
            codigo = _rodar(
                ["ingerir", "--transporte", "console"], payload={"foo": "bar"}
            )
        self.assertEqual(codigo, 0)
        self.assertNotIn("CONFIGURAÇÃO", saida.getvalue())

    def test_transporte_evolution_explicito_continua_funcionando(self):
        codigo = _rodar(
            ["ingerir", "--transporte", "evolution"], payload=_payload_evolution()
        )
        self.assertEqual(codigo, 0)
        self.assertTrue(self._tem_contato_ingerido())


class TesteReprocessarFalhas(unittest.TestCase):
    """Requirement (spec.md): "Reprocessamento manual de falhas"."""

    def setUp(self):
        self.db = FakeDatabase()
        contexto = patch.object(cli, "_db", return_value=self.db)
        contexto.start()
        self.addCleanup(contexto.stop)

    def test_sem_pendentes_nao_faz_nada(self):
        codigo = _rodar(["reprocessar-falhas"])
        self.assertEqual(codigo, 0)

    def test_reprocessa_falha_registrada_com_sucesso(self):
        evento_id = self.db.registrar_evento_bruto(_payload_evolution())
        codigo = _rodar(["reprocessar-falhas"])
        self.assertEqual(codigo, 0)
        registro = self.db.eventos_brutos[evento_id]
        self.assertTrue(registro.processado)
        self.assertIsNotNone(registro.processado_em)
        self.assertTrue(
            any(c.telefone == "5511999998888" for c in self.db.contatos.values())
        )

    def test_nao_duplica_mensagem_ja_ingerida_com_sucesso(self):
        """Reprocessar um evento cujo `externa_id` já foi consumido (ex.: o
        webhook processou com sucesso antes de a falha ser corrigida, ou o
        operador roda o comando duas vezes) não duplica a mensagem — mesmo
        dedup por `externa_id` de sempre."""
        from camucrm.ingest import ingerir

        payload = _payload_evolution()
        transporte = cli.criar_transporte("evolution", para_envio=False)
        primeiro = ingerir(self.db, transporte.receber(payload), origem="whatsapp")
        evento_id = self.db.registrar_evento_bruto(payload)

        codigo = _rodar(["reprocessar-falhas"])

        self.assertEqual(codigo, 0)
        self.assertEqual(len(self.db.mensagens[primeiro.conversa_id]), 1)
        self.assertTrue(self.db.eventos_brutos[evento_id].processado)

    def test_falha_que_persiste_e_relatada_e_continua_pendente(self):
        evento_id = self.db.registrar_evento_bruto({"nao": "e-payload-valido"})
        with patch.object(cli, "ingerir", side_effect=RuntimeError("boom")):
            codigo = _rodar(["reprocessar-falhas"])
        self.assertEqual(codigo, 1)
        registro = self.db.eventos_brutos[evento_id]
        self.assertFalse(registro.processado)
        self.assertIn("boom", registro.erro)
        self.assertEqual(registro.tentativas, 1)


if __name__ == "__main__":
    unittest.main()
