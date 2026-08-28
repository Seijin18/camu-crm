"""Receptor de webhook: confirma rápido, processa depois, nunca envia."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from camucrm import webhook


class TesteRotas(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(webhook.app)
        # Explícito, e não herdado do ambiente: estes casos testam a rota sem
        # token configurado. `config` já impede o `.env` de vazar para a
        # suíte, mas um teste que depende de ausência precisa garanti-la.
        contexto = patch.dict("os.environ", {}, clear=False)
        contexto.start()
        self.addCleanup(contexto.stop)
        os.environ.pop(webhook.ENV_TOKEN, None)

    def test_health(self):
        resposta = self.cliente.get("/health")
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["ok"])

    def test_confirma_antes_de_processar(self):
        with patch.object(webhook, "_processar") as processar:
            resposta = self.cliente.post("/webhook/evolution", json={"event": "x"})
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["recebido"])
        processar.assert_called_once()

    def test_corpo_nao_json_nao_derruba(self):
        resposta = self.cliente.post(
            "/webhook/evolution",
            content=b"nao sou json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resposta.status_code, 200)

    def test_falha_no_processamento_nao_propaga(self):
        """Um evento malformado não pode derrubar o worker.

        `get_db()` precisa estar mockado agora (change
        `ingestao-a-prova-de-falha`): `_processar` grava o payload em
        `eventos_recebidos_bruto` antes de chegar em `criar_transporte`, e
        sem mock isto tentaria abrir um pool contra Postgres de verdade.
        """
        db_falso = Mock()
        db_falso.registrar_evento_bruto.return_value = 1
        with patch.object(webhook, "get_db", return_value=db_falso), patch.object(
            webhook, "criar_transporte", side_effect=RuntimeError("boom")
        ):
            webhook._processar({"event": "x"})  # não deve levantar
        db_falso.marcar_evento_bruto_falhou.assert_called_once()
        db_falso.marcar_evento_bruto_processado.assert_not_called()

    def test_nao_existe_rota_de_envio(self):
        """§10: um webhook que responde sozinho é o disparo automático proibido."""
        caminhos = {rota.path for rota in webhook.app.routes}
        self.assertNotIn("/enviar", caminhos)
        self.assertEqual(
            {c for c in caminhos if c.startswith("/webhook")},
            {"/webhook/evolution"},
        )


class TesteToken(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(webhook.app)

    def test_sem_token_configurado_aceita(self):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop(webhook.ENV_TOKEN, None)
            with patch.object(webhook, "_processar"):
                resposta = self.cliente.post("/webhook/evolution", json={})
        self.assertEqual(resposta.status_code, 200)

    def test_token_errado_recusa(self):
        with patch.dict("os.environ", {webhook.ENV_TOKEN: "segredo"}):
            resposta = self.cliente.post(
                "/webhook/evolution", json={}, headers={"x-camu-token": "errado"}
            )
        self.assertEqual(resposta.status_code, 401)

    def test_token_certo_aceita(self):
        with patch.dict("os.environ", {webhook.ENV_TOKEN: "segredo"}):
            with patch.object(webhook, "_processar"):
                resposta = self.cliente.post(
                    "/webhook/evolution", json={}, headers={"x-camu-token": "segredo"}
                )
        self.assertEqual(resposta.status_code, 200)

    def test_token_ausente_quando_exigido_recusa(self):
        with patch.dict("os.environ", {webhook.ENV_TOKEN: "segredo"}):
            resposta = self.cliente.post("/webhook/evolution", json={})
        self.assertEqual(resposta.status_code, 401)


if __name__ == "__main__":
    unittest.main()


class TesteExtracaoAoReceber(unittest.TestCase):
    """A extração roda junto da ingestão — a fila é o produto e não pode
    ficar atrasada em relação ao que já aconteceu."""

    def setUp(self):
        webhook._extrator = None
        self.addCleanup(setattr, webhook, "_extrator", None)

    def test_ligada_por_padrao(self):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop(webhook.ENV_EXTRAIR, None)
            self.assertTrue(webhook.extrair_ao_receber())

    def test_desligavel_por_ambiente(self):
        for valor in ("false", "0", "no", "off", "nao"):
            with self.subTest(valor=valor):
                with patch.dict("os.environ", {webhook.ENV_EXTRAIR: valor}):
                    self.assertFalse(webhook.extrair_ao_receber())

    def test_desligada_nao_monta_extrator(self):
        with patch.dict("os.environ", {webhook.ENV_EXTRAIR: "false"}):
            self.assertIsNone(webhook.get_extrator())

    def test_llm_mal_configurado_nao_impede_ingestao(self):
        """Falha de LLM nunca pode custar a mensagem."""
        from camucrm.llm import LlmIndisponivelError

        with patch.object(webhook, "get_db"), patch(
            "camucrm.llm.criar_llm", side_effect=LlmIndisponivelError("sem chave")
        ):
            self.assertIsNone(webhook.get_extrator())

    def test_falha_na_extracao_nao_propaga(self):
        """A mensagem já está gravada; extração que quebra não pode desfazer isso."""
        quebrado = Mock()
        quebrado.processar_conversa.side_effect = RuntimeError("boom")
        # Change `extracao-em-lote-por-janela`: `_extrair` agora consulta o
        # gatilho híbrido antes de chamar `processar_conversa` — o mock
        # precisa responder o bastante para o gatilho decidir "sim, agora"
        # (aqui, via `mensagens_desde` acima do limiar padrão), senão o
        # teste nunca chegaria a exercitar a falha que quer provar.
        quebrado.db.get_conversa.return_value = Mock(id=1, ultima_mensagem_processada_id=None)
        quebrado.db.mensagens_desde.return_value = 999
        with patch.object(webhook, "get_extrator", return_value=quebrado):
            webhook._extrair(1)  # não deve levantar

    def test_mensagem_duplicada_nao_dispara_extracao(self):
        """Reentrega não gasta chamada de LLM."""
        from camucrm.ingest import ResultadoIngestao

        with patch.object(webhook, "criar_transporte"), patch.object(
            webhook, "get_db"
        ), patch.object(
            webhook, "ingerir",
            return_value=ResultadoIngestao(1, "Ana", duplicada=True),
        ), patch.object(webhook, "_extrair") as extrair:
            webhook._processar({"event": "x"})
        extrair.assert_not_called()

    def test_evento_ignorado_nao_dispara_extracao(self):
        from camucrm.ingest import ResultadoIngestao

        with patch.object(webhook, "criar_transporte"), patch.object(
            webhook, "get_db"
        ), patch.object(
            webhook, "ingerir",
            return_value=ResultadoIngestao(None, None, ignorada=True),
        ), patch.object(webhook, "_extrair") as extrair:
            webhook._processar({"event": "connection.update"})
        extrair.assert_not_called()

    def test_mensagem_nova_dispara_extracao(self):
        from camucrm.ingest import ResultadoIngestao

        with patch.object(webhook, "criar_transporte"), patch.object(
            webhook, "get_db"
        ), patch.object(
            webhook, "ingerir", return_value=ResultadoIngestao(7, "Ana")
        ), patch.object(webhook, "_extrair") as extrair:
            webhook._processar({"event": "messages.upsert"})
        extrair.assert_called_once_with(7)


class TesteGatilhoHibrido(unittest.TestCase):
    """Change `extracao-em-lote-por-janela`: `_deve_extrair_agora` e o uso
    dele em `_extrair` — contagem OU espera, o que vier primeiro; abaixo
    dos dois, a extração fica pendente para `camucrm extrair`."""

    def setUp(self):
        contexto = patch.dict("os.environ", {}, clear=False)
        contexto.start()
        self.addCleanup(contexto.stop)
        os.environ.pop(webhook.ENV_LIMIAR_MENSAGENS, None)
        os.environ.pop(webhook.ENV_TETO_ESPERA_MINUTOS, None)

    def _conversa(self, ultima_mensagem_processada_id=None):
        return Mock(id=1, ultima_mensagem_processada_id=ultima_mensagem_processada_id)

    def test_padroes_sem_variavel_de_ambiente(self):
        self.assertEqual(webhook.limiar_mensagens(), webhook.LIMIAR_MENSAGENS_PADRAO)
        self.assertEqual(
            webhook.teto_espera_minutos(), webhook.TETO_ESPERA_MINUTOS_PADRAO
        )

    def test_variavel_de_ambiente_e_respeitada(self):
        with patch.dict("os.environ", {webhook.ENV_LIMIAR_MENSAGENS: "2"}):
            self.assertEqual(webhook.limiar_mensagens(), 2)
        with patch.dict("os.environ", {webhook.ENV_TETO_ESPERA_MINUTOS: "10"}):
            self.assertEqual(webhook.teto_espera_minutos(), 10)

    def test_sem_mensagem_pendente_nao_extrai(self):
        db = Mock()
        db.mensagens_desde.return_value = 0
        self.assertFalse(webhook._deve_extrair_agora(db, self._conversa()))

    def test_contagem_atinge_limiar_dispara_mesmo_recem_chegada(self):
        db = Mock()
        db.mensagens_desde.return_value = webhook.LIMIAR_MENSAGENS_PADRAO
        # Mesmo que a mais antiga tenha chegado agora mesmo (não deveria nem
        # ser consultada quando a contagem já decide).
        db.primeira_mensagem_pendente_em.return_value = datetime.now(timezone.utc)
        self.assertTrue(webhook._deve_extrair_agora(db, self._conversa()))

    def test_espera_alem_do_teto_dispara_mesmo_com_poucas_pendentes(self):
        db = Mock()
        db.mensagens_desde.return_value = 1
        agora = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
        db.primeira_mensagem_pendente_em.return_value = agora - timedelta(
            minutes=webhook.TETO_ESPERA_MINUTOS_PADRAO + 1
        )
        self.assertTrue(webhook._deve_extrair_agora(db, self._conversa(), agora=agora))

    def test_abaixo_dos_dois_limiares_nao_extrai(self):
        db = Mock()
        db.mensagens_desde.return_value = 1
        agora = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
        db.primeira_mensagem_pendente_em.return_value = agora - timedelta(seconds=5)
        self.assertFalse(webhook._deve_extrair_agora(db, self._conversa(), agora=agora))

    def test_extrair_nao_chama_llm_abaixo_dos_limiares(self):
        extrator = Mock()
        extrator.db.get_conversa.return_value = self._conversa()
        extrator.db.mensagens_desde.return_value = 1
        extrator.db.primeira_mensagem_pendente_em.return_value = datetime.now(
            timezone.utc
        )
        with patch.object(webhook, "get_extrator", return_value=extrator):
            webhook._extrair(1)
        extrator.processar_conversa.assert_not_called()

    def test_extrair_chama_llm_quando_gatilho_permite(self):
        extrator = Mock()
        extrator.db.get_conversa.return_value = self._conversa()
        extrator.db.mensagens_desde.return_value = webhook.LIMIAR_MENSAGENS_PADRAO
        with patch.object(webhook, "get_extrator", return_value=extrator):
            webhook._extrair(1)
        extrator.processar_conversa.assert_called_once_with(1)


class TesteBootFalhaAlto(unittest.TestCase):
    """Requirement (spec.md): "Schema ausente falha no boot, não no primeiro
    evento" — `servir()` chama `ensure_schema()` antes de subir o uvicorn."""

    def test_schema_ausente_ou_banco_indisponivel_derruba_boot(self):
        db_falso = Mock()
        db_falso.ensure_schema.side_effect = RuntimeError("schema ausente")
        with patch.object(webhook, "get_db", return_value=db_falso), patch(
            "uvicorn.run"
        ) as uv_run:
            with self.assertRaises(RuntimeError):
                webhook.servir()
        uv_run.assert_not_called()

    def test_schema_ok_sobe_o_servico(self):
        db_ok = Mock()
        with patch.object(webhook, "get_db", return_value=db_ok), patch(
            "uvicorn.run"
        ) as uv_run:
            webhook.servir(9999)
        db_ok.ensure_schema.assert_called_once()
        uv_run.assert_called_once()


class TesteStagingDeEventosBrutos(unittest.TestCase):
    """Requirements: "Payload bruto é preservado antes do processamento" e
    "Falha de ingestão deixa rastro reprocessável"."""

    def test_payload_e_gravado_antes_de_processar(self):
        db_falso = Mock()
        db_falso.registrar_evento_bruto.return_value = 42
        from camucrm.ingest import ResultadoIngestao

        with patch.object(webhook, "get_db", return_value=db_falso), patch.object(
            webhook, "criar_transporte"
        ), patch.object(
            webhook, "ingerir", return_value=ResultadoIngestao(1, "Ana")
        ), patch.object(webhook, "_extrair"):
            webhook._processar({"event": "messages.upsert"})
        db_falso.registrar_evento_bruto.assert_called_once_with(
            {"event": "messages.upsert"}
        )
        db_falso.marcar_evento_bruto_processado.assert_called_once_with(42)
        db_falso.marcar_evento_bruto_falhou.assert_not_called()

    def test_excecao_em_ingerir_marca_falha_sem_apagar_o_payload(self):
        db_falso = Mock()
        db_falso.registrar_evento_bruto.return_value = 7
        with patch.object(webhook, "get_db", return_value=db_falso), patch.object(
            webhook, "criar_transporte", side_effect=RuntimeError("boom")
        ):
            webhook._processar({"event": "messages.upsert"})  # não deve levantar
        db_falso.marcar_evento_bruto_falhou.assert_called_once()
        args = db_falso.marcar_evento_bruto_falhou.call_args[0]
        self.assertEqual(args[0], 7)
        self.assertIn("boom", args[1])
        db_falso.marcar_evento_bruto_processado.assert_not_called()

    def test_falha_ao_gravar_o_staging_nao_propaga(self):
        """Se nem o INSERT do staging funciona (banco fora do ar), o worker
        ainda não pode cair — só não há onde registrar o rastro."""
        db_falso = Mock()
        db_falso.registrar_evento_bruto.side_effect = RuntimeError("sem conexão")
        with patch.object(webhook, "get_db", return_value=db_falso):
            webhook._processar({"event": "x"})  # não deve levantar


class TesteInstanciaRepassadaParaIngerir(unittest.TestCase):
    """Change `ingestao-restrita-por-instancia`: `_processar` extrai
    `payload["instance"]` e repassa para `ingest.ingerir`, que decide a
    restrição — nenhuma regra de negócio mora aqui (docstring do módulo),
    só a extração do campo."""

    def test_campo_instance_do_payload_e_repassado(self):
        from camucrm.ingest import ResultadoIngestao

        db_falso = Mock()
        db_falso.registrar_evento_bruto.return_value = 1
        with patch.object(webhook, "get_db", return_value=db_falso), patch.object(
            webhook, "criar_transporte"
        ), patch.object(
            webhook, "ingerir", return_value=ResultadoIngestao(1, "Ana")
        ) as ingerir_mock, patch.object(webhook, "_extrair"):
            webhook._processar({"event": "messages.upsert", "instance": "pessoal-marcos"})
        self.assertEqual(ingerir_mock.call_args.kwargs.get("instancia"), "pessoal-marcos")

    def test_payload_sem_campo_instance_repassa_none(self):
        from camucrm.ingest import ResultadoIngestao

        db_falso = Mock()
        db_falso.registrar_evento_bruto.return_value = 1
        with patch.object(webhook, "get_db", return_value=db_falso), patch.object(
            webhook, "criar_transporte"
        ), patch.object(
            webhook, "ingerir", return_value=ResultadoIngestao(1, "Ana")
        ) as ingerir_mock, patch.object(webhook, "_extrair"):
            webhook._processar({"event": "messages.upsert"})
        self.assertIsNone(ingerir_mock.call_args.kwargs.get("instancia"))

    def test_evento_ignorado_por_instancia_ainda_fica_staged(self):
        """Requirement "Payload cru continua sendo preservado
        incondicionalmente": o staging roda ANTES de `ingerir` decidir
        qualquer coisa, então um evento ignorado por restrição de
        instância continua tendo sua linha em `eventos_recebidos_bruto`."""
        from camucrm.ingest import ResultadoIngestao

        db_falso = Mock()
        db_falso.registrar_evento_bruto.return_value = 5
        with patch.object(webhook, "get_db", return_value=db_falso), patch.object(
            webhook, "criar_transporte"
        ), patch.object(
            webhook, "ingerir", return_value=ResultadoIngestao(None, None, ignorada=True)
        ), patch.object(webhook, "_extrair") as extrair:
            webhook._processar({"event": "messages.upsert", "instance": "pessoal-marcos"})
        db_falso.registrar_evento_bruto.assert_called_once_with(
            {"event": "messages.upsert", "instance": "pessoal-marcos"}
        )
        db_falso.marcar_evento_bruto_processado.assert_called_once_with(5)
        extrair.assert_not_called()
