"""API do painel: token, ausência de rota de escrita, telefone nunca vaza."""

from __future__ import annotations

import ast
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from camucrm.llm import FakeLlm
from camucrm.painel import api, server
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


class TesteRotasDeRascunho(unittest.TestCase):
    """Change `rascunho-registrado`: gerar/ler histórico/escolha — sempre
    `POST` para gerar e escolher (§10/§7: gastam cota de LLM ou gravam
    linha); nunca envia (o painel não tem rota de envio)."""

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

    def test_gerar_rascunho_persiste_e_devolve_comando_pronto(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi, vi o insta de voces")
        resposta_llm = json.dumps({"opcoes": [
            "Manda uma foto do seu pet?\nTe mostro como fica.",
            "Consegue mandar uma foto dele?\nJá te envio a prévia.",
        ]})
        with patch.object(api, "criar_llm", return_value=FakeLlm([resposta_llm])):
            resposta = self.cliente.post(
                f"/api/conversas/{conversa.id}/rascunho", json={"por": "marcos"}
            )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["opcoes"]), 2)
        self.assertFalse(corpo["encerrar"])
        self.assertIn(f"--rascunho {corpo['id']} --opcao 1", corpo["comandos"]["1"])
        self.assertIn(f"--rascunho {corpo['id']} --opcao 2", corpo["comandos"]["2"])
        self.assertIn(str(conversa.id), corpo["comandos"]["1"])
        # E persistiu de fato — não só devolveu na resposta.
        self.assertIsNotNone(self.fake.rascunho(corpo["id"]))

    def test_gerar_rascunho_conversa_inexistente_devolve_422(self):
        resposta = self.cliente.post("/api/conversas/999/rascunho", json={"por": "marcos"})
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("erro", resposta.json())

    def test_gerar_rascunho_encerrar_grava_recusa_com_motivo(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.followups.setdefault(conversa.id, [])
        conversa.followups_enviados = 2  # teto atingido (§6) -> drafts.gerar recusa
        with patch.object(api, "criar_llm", return_value=FakeLlm()):
            resposta = self.cliente.post(
                f"/api/conversas/{conversa.id}/rascunho", json={"por": "marcos"}
            )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["encerrar"])
        self.assertIsNotNone(corpo["motivo"])
        self.assertIsNone(corpo["opcoes"])
        self.assertIsNone(corpo["comandos"])

    def test_gerar_rascunho_llm_indisponivel_devolve_422(self):
        from camucrm.llm import LlmIndisponivelError

        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")

        class LlmQuebrado:
            nome = "quebrado"

            def completar(self, system, user, *, json_estrito=False):
                raise LlmIndisponivelError("cota esgotada")

        with patch.object(api, "criar_llm", return_value=LlmQuebrado()):
            resposta = self.cliente.post(
                f"/api/conversas/{conversa.id}/rascunho", json={"por": "marcos"}
            )
        self.assertEqual(resposta.status_code, 422)
        corpo = resposta.json()
        self.assertIn("erro", corpo)
        self.assertEqual(corpo["regra"], "§10")

    def test_historico_de_rascunhos_nao_chama_llm(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        rascunho_id = self.fake.gravar_rascunho(
            conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("a", "b"),
        )
        resposta = self.cliente.get(f"/api/conversas/{conversa.id}/rascunhos")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["rascunhos"]), 1)
        self.assertEqual(corpo["rascunhos"][0]["id"], rascunho_id)

    def test_historico_conversa_inexistente(self):
        resposta = self.cliente.get("/api/conversas/999/rascunhos")
        self.assertIn("erro", resposta.json())

    def test_registrar_escolha_por_opcao(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        rascunho_id = self.fake.gravar_rascunho(
            conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("a", "b"),
        )
        resposta = self.cliente.post(
            f"/api/rascunhos/{rascunho_id}/escolha",
            json={"opcao": 1, "por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["escolhida"], 1)
        self.assertIsNone(corpo["mensagem_id"])  # caminho 3: sem vínculo com mensagem

    def test_registrar_escolha_texto_final_do_zero(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        rascunho_id = self.fake.gravar_rascunho(
            conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("a", "b"),
        )
        resposta = self.cliente.post(
            f"/api/rascunhos/{rascunho_id}/escolha",
            json={"texto_final": "Escrevi do zero", "por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertIsNone(corpo["escolhida"])
        self.assertEqual(corpo["texto_final"], "Escrevi do zero")

    def test_registrar_escolha_sem_opcao_nem_texto_final_devolve_422(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        rascunho_id = self.fake.gravar_rascunho(
            conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("a", "b"),
        )
        resposta = self.cliente.post(
            f"/api/rascunhos/{rascunho_id}/escolha", json={"por": "marcos"}
        )
        self.assertEqual(resposta.status_code, 422)

    def test_registrar_escolha_rascunho_inexistente_devolve_422(self):
        resposta = self.cliente.post(
            "/api/rascunhos/999/escolha", json={"opcao": 1, "por": "marcos"}
        )
        self.assertEqual(resposta.status_code, 422)

    def test_nenhuma_rota_de_rascunho_contem_enviar(self):
        caminhos = set(server.app.openapi()["paths"].keys())
        for caminho in caminhos:
            if "rascunho" in caminho:
                self.assertNotIn("enviar", caminho)


RESUMO_OK = json.dumps({
    "resumo": "Ana pediu peça personalizada e mandou a foto do pet.\n"
              "A prévia foi enviada, sem resposta ainda.",
    "proximo_passo": "Enviar follow-up perguntando se ela viu a prévia.",
})


class TesteRotasDeResumo(unittest.TestCase):
    """Change `resumo-conversa`: `POST` gera (checa cache antes do LLM),
    `GET` só lê. Nunca 500 — LLM indisponível ou resumo inválido devolvem
    200 com `resumo: null` (requirement "Falha de LLM não derruba a
    tela")."""

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

    def test_get_sem_resumo_gerado_devolve_nao_gerado_sem_chamar_llm(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        with patch.object(api, "criar_llm") as llm_mock:
            resposta = self.cliente.get(f"/api/conversas/{conversa.id}/resumo")
        llm_mock.assert_not_called()
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertFalse(corpo["gerado"])
        self.assertIsNone(corpo["resumo"])

    def test_post_gera_e_persiste(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")
        with patch.object(api, "criar_llm", return_value=FakeLlm([RESUMO_OK])):
            resposta = self.cliente.post(
                f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"}
            )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["gerado"])
        self.assertIn("prévia", corpo["resumo"])
        self.assertEqual(corpo["mensagens_desde"], 0)
        self.assertEqual(len(self.fake.resumos), 1)

    def test_post_conversa_inexistente_devolve_422(self):
        resposta = self.cliente.post("/api/conversas/999/resumo", json={"por": "marcos"})
        self.assertEqual(resposta.status_code, 422)

    def test_gerar_duas_vezes_sem_mensagem_nova_nao_chama_llm_de_novo(self):
        """Cache é conferido ANTES da chamada ao LLM (requirement "Cache
        por versão de prompt e mensagem")."""
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")
        llm = FakeLlm([RESUMO_OK])
        with patch.object(api, "criar_llm", return_value=llm):
            self.cliente.post(f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"})
            resposta = self.cliente.post(
                f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"}
            )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(llm.chamadas), 1)  # só a primeira chamou o LLM
        self.assertEqual(len(self.fake.resumos), 1)  # nenhuma linha duplicada

    def test_forcar_chama_llm_de_novo_mesmo_sem_mensagem_nova(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")
        llm = FakeLlm([RESUMO_OK, RESUMO_OK])
        with patch.object(api, "criar_llm", return_value=llm):
            self.cliente.post(f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"})
            resposta = self.cliente.post(
                f"/api/conversas/{conversa.id}/resumo",
                json={"por": "marcos", "forcar": True},
            )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(llm.chamadas), 2)
        self.assertEqual(len(self.fake.resumos), 1)  # substitui, não duplica

    def test_mensagem_nova_gera_de_novo_sem_forcar(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")
        llm = FakeLlm([RESUMO_OK])
        with patch.object(api, "criar_llm", return_value=llm):
            self.cliente.post(f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"})
        self.fake.registrar_mensagem(conversa.id, "in", "mais uma coisa")
        llm2 = FakeLlm([RESUMO_OK])
        with patch.object(api, "criar_llm", return_value=llm2):
            resposta = self.cliente.post(
                f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"}
            )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(llm2.chamadas), 1)  # fronteira nova, chamou de novo

    def test_llm_indisponivel_devolve_200_com_resumo_null(self):
        from camucrm.llm import LlmIndisponivelError

        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")

        class LlmQuebrado:
            nome = "quebrado"

            def completar(self, system, user, *, json_estrito=False):
                raise LlmIndisponivelError("sem chave")

        with patch.object(api, "criar_llm", return_value=LlmQuebrado()):
            resposta = self.cliente.post(
                f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"}
            )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertFalse(corpo["gerado"])
        self.assertIsNone(corpo["resumo"])
        self.assertIsNotNone(corpo["erro"])

    def test_get_apos_gerar_devolve_staleness_zero(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")
        with patch.object(api, "criar_llm", return_value=FakeLlm([RESUMO_OK])):
            self.cliente.post(f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"})
        resposta = self.cliente.get(f"/api/conversas/{conversa.id}/resumo")
        corpo = resposta.json()
        self.assertTrue(corpo["gerado"])
        self.assertEqual(corpo["mensagens_desde"], 0)

    def test_get_depois_de_mensagem_nova_mostra_staleness(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")
        with patch.object(api, "criar_llm", return_value=FakeLlm([RESUMO_OK])):
            self.cliente.post(f"/api/conversas/{conversa.id}/resumo", json={"por": "marcos"})
        self.fake.registrar_mensagem(conversa.id, "in", "mais uma")
        resposta = self.cliente.get(f"/api/conversas/{conversa.id}/resumo")
        corpo = resposta.json()
        self.assertEqual(corpo["mensagens_desde"], 1)

    def test_get_nunca_gera_resumo(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.cliente.get(f"/api/conversas/{conversa.id}/resumo")
        self.assertEqual(len(self.fake.resumos), 0)

    def test_nenhuma_rota_de_resumo_contem_enviar(self):
        caminhos = set(server.app.openapi()["paths"].keys())
        for caminho in caminhos:
            if "resumo" in caminho:
                self.assertNotIn("enviar", caminho)


if __name__ == "__main__":
    unittest.main()
