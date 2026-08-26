"""Receptor de webhook: confirma rápido, processa depois, nunca envia."""

import os
import unittest
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
        """Um evento malformado não pode derrubar o worker."""
        with patch.object(webhook, "criar_transporte", side_effect=RuntimeError("boom")):
            webhook._processar({"event": "x"})  # não deve levantar

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
