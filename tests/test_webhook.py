"""Receptor de webhook: confirma rápido, processa depois, nunca envia."""

import os
import unittest
from unittest.mock import patch

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
