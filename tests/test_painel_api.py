"""API do painel: token, ausência de rota de escrita, telefone nunca vaza."""

from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from camucrm.painel import server
from tests.fakes import FakeDatabase

PAINEL_DIR = Path(server.__file__).resolve().parent


class TesteToken(unittest.TestCase):
    """Espelha `test_webhook.TesteToken` — mesmo padrão de token."""

    def setUp(self):
        self.cliente = TestClient(server.app)
        contexto = patch.dict("os.environ", {}, clear=False)
        contexto.start()
        self.addCleanup(contexto.stop)
        os.environ.pop(server.ENV_TOKEN, None)
        self.addCleanup(setattr, server, "_db", None)

    def test_sem_token_configurado_aceita(self):
        with patch.object(server, "get_db", return_value=FakeDatabase()):
            resposta = self.cliente.get("/api/fila")
        self.assertEqual(resposta.status_code, 200)

    def test_token_errado_recusa(self):
        with patch.dict("os.environ", {server.ENV_TOKEN: "segredo"}):
            resposta = self.cliente.get(
                "/api/fila", headers={"x-camu-token": "errado"}
            )
        self.assertEqual(resposta.status_code, 401)

    def test_token_ausente_quando_exigido_recusa(self):
        with patch.dict("os.environ", {server.ENV_TOKEN: "segredo"}):
            resposta = self.cliente.get("/api/fila")
        self.assertEqual(resposta.status_code, 401)

    def test_token_certo_aceita(self):
        with patch.dict("os.environ", {server.ENV_TOKEN: "segredo"}):
            with patch.object(server, "get_db", return_value=FakeDatabase()):
                resposta = self.cliente.get(
                    "/api/fila", headers={"x-camu-token": "segredo"}
                )
        self.assertEqual(resposta.status_code, 200)

    def test_health_nao_exige_token(self):
        with patch.dict("os.environ", {server.ENV_TOKEN: "segredo"}):
            resposta = self.cliente.get("/health")
        self.assertEqual(resposta.status_code, 200)


class TesteSemRotaDeEnvio(unittest.TestCase):
    """§10: o painel só lê. Nenhuma rota escreve nem importa transporte."""

    def test_nenhum_path_contem_enviar(self):
        caminhos = set(server.app.openapi()["paths"].keys())
        self.assertTrue(caminhos, "esperava pelo menos uma rota")
        for caminho in caminhos:
            self.assertNotIn("enviar", caminho)

    def test_nenhum_modulo_do_painel_importa_transport(self):
        """Checagem por AST — não por grep — de que `camucrm.transport` não
        é importado por nenhum módulo de `camucrm/painel/`."""
        for arquivo in ("__init__.py", "server.py", "api.py", "views.py", "stream.py"):
            caminho = PAINEL_DIR / arquivo
            with self.subTest(arquivo=arquivo):
                arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
                for node in ast.walk(arvore):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn(
                                "transport", alias.name,
                                f"{arquivo} importa {alias.name}",
                            )
                    elif isinstance(node, ast.ImportFrom):
                        modulo = node.module or ""
                        self.assertNotIn(
                            "transport", modulo,
                            f"{arquivo} importa de {modulo}",
                        )


class TesteRotas(unittest.TestCase):
    def setUp(self):
        self.cliente = TestClient(server.app)
        contexto = patch.dict("os.environ", {}, clear=False)
        contexto.start()
        self.addCleanup(contexto.stop)
        os.environ.pop(server.ENV_TOKEN, None)
        self.fake = FakeDatabase()
        patcher = patch.object(server, "get_db", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_health(self):
        resposta = self.cliente.get("/health")
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["ok"])

    def test_csp_presente(self):
        resposta = self.cliente.get("/health")
        self.assertEqual(
            resposta.headers.get("content-security-policy"), "default-src 'self'"
        )

    def test_kanban_smoke(self):
        self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        resposta = self.cliente.get("/api/kanban")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["kanbans"]), 2)

    def test_kanban_funil_invalido(self):
        resposta = self.cliente.get("/api/kanban?funil=xis")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("erro", resposta.json())

    def test_conversas_smoke(self):
        self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        resposta = self.cliente.get("/api/conversas")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertIn("total", corpo)
        self.assertIn("conversas", corpo)

    def test_conversas_filtro_por_estagio(self):
        c1 = self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        self.fake.gravar_evento_estagio(c1.id, None, "S1")
        c2 = self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Beto")
        resposta = self.cliente.get("/api/conversas?estagio=S1")
        corpo = resposta.json()
        ids = {c["id"] for c in corpo["conversas"]}
        self.assertIn(c1.id, ids)

    def test_fila_smoke(self):
        self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        resposta = self.cliente.get("/api/fila")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("itens", resposta.json())

    def test_metricas_smoke(self):
        resposta = self.cliente.get("/api/metricas")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertIn("conversoes_chave", corpo)
        self.assertIn("tempo_por_estagio", corpo)
        self.assertIn("saude_taxonomia", corpo)

    def test_conversa_inexistente_404_shape(self):
        resposta = self.cliente.get("/api/conversas/999")
        self.assertEqual(resposta.status_code, 200)  # painel devolve corpo de erro, não HTTP 404
        corpo = resposta.json()
        self.assertIn("erro", corpo)
        self.assertIn("regra", corpo)

    def test_mensagens_conversa_inexistente(self):
        resposta = self.cliente.get("/api/conversas/999/mensagens")
        corpo = resposta.json()
        self.assertIn("erro", corpo)

    def test_mensagens_smoke(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")
        resposta = self.cliente.get(f"/api/conversas/{conversa.id}/mensagens")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["mensagens"]), 1)
        self.assertEqual(corpo["mensagens"][0]["texto"], "oi")


class TesteDetalheNuncaDevolveTelefone(unittest.TestCase):
    """§12: telefone em claro nunca sai pela API do painel."""

    TELEFONE_SENTINELA = "5511999998888"

    def setUp(self):
        self.cliente = TestClient(server.app)
        contexto = patch.dict("os.environ", {}, clear=False)
        contexto.start()
        self.addCleanup(contexto.stop)
        os.environ.pop(server.ENV_TOKEN, None)
        self.fake = FakeDatabase()
        patcher = patch.object(server, "get_db", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_telefone_nao_aparece_no_corpo(self):
        import json

        conversa = self.fake.criar_conversa(
            funil="b2c", estagio="S0", nome="Ana", telefone=self.TELEFONE_SENTINELA
        )
        resposta = self.cliente.get(f"/api/conversas/{conversa.id}")
        self.assertEqual(resposta.status_code, 200)
        corpo_serializado = json.dumps(resposta.json())
        self.assertNotIn(self.TELEFONE_SENTINELA, corpo_serializado)
        self.assertTrue(resposta.json()["contato"]["tem_telefone"])


class TesteRotasDeAcao(unittest.TestCase):
    """`acoes-no-painel`: escrita sempre via `camucrm.acoes`, 422 na recusa."""

    def setUp(self):
        self.cliente = TestClient(server.app)
        contexto = patch.dict("os.environ", {}, clear=False)
        contexto.start()
        self.addCleanup(contexto.stop)
        os.environ.pop(server.ENV_TOKEN, None)
        self.fake = FakeDatabase()
        patcher = patch.object(server, "get_db", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_marcar_marco_valido(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S4")
        resposta = self.cliente.post(
            f"/api/conversas/{conversa.id}/marcos",
            json={"marco": "ganho", "por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["card"]["estagio"], "S6")
        self.assertIn("ganho", self.fake.marcos_da_conversa(conversa.id))

    def test_marcar_marco_incompativel_com_funil_devolve_422(self):
        """Requirement 'Coluna derivada recusa drop com 422' — mesmo
        contrato vale para marco incompatível com o funil (§3)."""
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S4")
        resposta = self.cliente.post(
            f"/api/conversas/{conversa.id}/marcos",
            json={"marco": "consignacao_assinada", "por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 422)
        corpo = resposta.json()
        self.assertIn("erro", corpo)
        self.assertEqual(corpo["regra"], "§3")
        self.assertEqual(self.fake.marcos_da_conversa(conversa.id), set())

    def test_marcar_marco_conversa_inexistente_devolve_422(self):
        resposta = self.cliente.post(
            "/api/conversas/999/marcos", json={"marco": "ganho", "por": "marcos"}
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("erro", resposta.json())

    def test_mudar_funil_grava_correcao(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S0")
        resposta = self.cliente.post(
            f"/api/conversas/{conversa.id}/funil",
            json={"funil": "b2b", "por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["card"]["funil"], "b2b")
        self.assertEqual(len(self.fake.correcoes), 1)
        self.assertEqual(self.fake.correcoes[0]["campo"], "funil")

    def test_mudar_funil_invalido_devolve_422(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S0")
        resposta = self.cliente.post(
            f"/api/conversas/{conversa.id}/funil",
            json={"funil": "xis", "por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.fake.correcoes, [])

    def test_registrar_correcao_avulsa_e_sempre_gravada(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S0")
        resposta = self.cliente.post(
            f"/api/conversas/{conversa.id}/correcoes",
            json={"campo": "estagio", "antes": "S0", "depois": "S1", "por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["ok"])
        self.assertEqual(len(self.fake.correcoes), 1)
        correcao = self.fake.correcoes[0]
        self.assertEqual(correcao["campo"], "estagio")
        self.assertEqual(correcao["antes"], "S0")
        self.assertEqual(correcao["depois"], "S1")

    def test_registrar_correcao_conversa_inexistente_devolve_422(self):
        resposta = self.cliente.post(
            "/api/conversas/999/correcoes",
            json={"campo": "estagio", "antes": "S0", "depois": "S1", "por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.fake.correcoes, [])


if __name__ == "__main__":
    unittest.main()
