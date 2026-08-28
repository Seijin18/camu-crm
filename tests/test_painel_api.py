"""API do painel: token, ausência de rota de escrita, telefone nunca vaza."""

from __future__ import annotations

import ast
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from camucrm import metrics
from camucrm.llm import FakeLlm
from camucrm.painel import api, server
from camucrm.transport import ResultadoEnvio, TransporteError
from tests.fakes import FakeDatabase

PAINEL_DIR = Path(server.__file__).resolve().parent


class FakeTransporteEvolution:
    """Satisfaz o protocolo `Transporte` (change
    `envio-prospeccao-pela-evolution-api`) — nunca a rede real. Mesmo
    espírito de `FakeLlm`: um objeto de verdade que exercita o caminho real
    de chamada (`enviar(Destinatario(...), texto, aprovado_por=...)`), não
    um `Mock` genérico que só confirma "algo foi chamado"."""

    nome = "evolution-fake"

    def __init__(self, *, sucesso: bool, externa_id: str | None = None, mensagem_erro: str = "falhou"):
        self.sucesso = sucesso
        self.externa_id = externa_id
        self.mensagem_erro = mensagem_erro
        self.chamadas: list[dict] = []

    def enviar(self, contato, texto, *, aprovado_por):
        self.chamadas.append(
            {"telefone": contato.telefone, "texto": texto, "aprovado_por": aprovado_por}
        )
        if not self.sucesso:
            raise TransporteError(self.nome, self.mensagem_erro)
        return ResultadoEnvio(entregue=True, externa_id=self.externa_id)


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
    """§1/§10: envio é sempre humano, nunca automático.

    Até o change `envio-prospeccao-pela-evolution-api`, essa garantia tomava
    a forma "o painel só lê, nenhuma rota chama transporte" — nenhuma rota
    continha "enviar", nenhum módulo importava `camucrm.transport`. Aquele
    change reabriu essa forma de propósito (pedido do usuário), isolando o
    novo caminho num único módulo nomeado (`camucrm.painel.envio`) em vez de
    espalhar `import camucrm.transport` pelo pacote. A garantia que continua
    de pé, e que estes testes provam: `aprovado_por` é obrigatório antes de
    qualquer chamada de rede (ver `TesteEnvioProspeccao` mais abaixo) — não
    "nenhum módulo toca transporte", que deixou de ser verdade por decisão
    explícita, registrada em `openspec/changes/
    envio-prospeccao-pela-evolution-api/design.md`.
    """

    # Único path autorizado a conter "enviar" — qualquer OUTRO é bug, e
    # nenhum novo pode ser adicionado sem atualizar este teste (o que força
    # quem adicionar a justificar a exceção, mesmo padrão de `_EXCECAO`).
    _UNICO_PATH_DE_ENVIO = "/api/prospeccao/{prospeccao_id}/enviar"

    # Único módulo do painel autorizado a importar `camucrm.transport` —
    # mesma lógica: adicionar um segundo exige mexer neste teste.
    _EXCECAO_TRANSPORT = "envio.py"

    def test_apenas_o_path_de_envio_de_prospeccao_contem_enviar(self):
        caminhos = set(server.app.openapi()["paths"].keys())
        self.assertTrue(caminhos, "esperava pelo menos uma rota")
        com_enviar = {c for c in caminhos if "enviar" in c}
        self.assertEqual(com_enviar, {self._UNICO_PATH_DE_ENVIO})

    def test_nenhum_modulo_do_painel_importa_transport_exceto_envio(self):
        """Checagem por AST — não por grep — de que `camucrm.transport` só
        é importado por `envio.py`, e por nenhum outro módulo de
        `camucrm/painel/`."""
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

    def test_envio_py_de_fato_importa_transport(self):
        """O outro lado da exceção: `envio.py` PRECISA importar transporte,
        senão a rota de envio não teria como funcionar — um teste que só
        provasse ausência em outros módulos não pegaria "esqueceram de
        implementar o envio de verdade"."""
        caminho = PAINEL_DIR / self._EXCECAO_TRANSPORT
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        importa_transport = any(
            (isinstance(node, ast.ImportFrom) and "transport" in (node.module or ""))
            or (
                isinstance(node, ast.Import)
                and any("transport" in a.name for a in node.names)
            )
            for node in ast.walk(arvore)
        )
        self.assertTrue(importa_transport, f"{self._EXCECAO_TRANSPORT} deveria importar transport")


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

    def test_kanban_expoe_total_real_mesmo_cortado(self):
        """Requirement "Kanban e fila expõem contagem real": `total` reflete
        a contagem real de conversas abertas, mesmo quando
        `_carregar_candidatos` corta pelo `LIMITE_CONVERSAS_PADRAO`."""
        for i in range(3):
            self.fake.criar_conversa(funil="b2c", estagio="S0", nome=f"C{i}")
        with patch.object(api, "LIMITE_CONVERSAS_PADRAO", 2):
            resposta = self.cliente.get("/api/kanban")
        corpo = resposta.json()
        self.assertEqual(corpo["total"], 3)

    def test_fila_expoe_total_real_mesmo_cortado(self):
        for i in range(3):
            self.fake.criar_conversa(funil="b2c", estagio="S0", nome=f"C{i}")
        with patch.object(api, "LIMITE_CONVERSAS_PADRAO", 2):
            resposta = self.cliente.get("/api/fila")
        corpo = resposta.json()
        self.assertEqual(corpo["total"], 3)

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

    def test_conversas_inclui_fechada_por_marco_manual_com_indicador(self):
        """Change `marco-manual-visivel-na-aba-conversas`, requirement
        "Conversa fechada por marco manual continua na aba Conversas": uma
        conversa com `resultado` preenchido não some de `GET /api/conversas`,
        e o card carrega o `resultado` para a UI diferenciar."""
        aberta = self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        fechada = self.fake.criar_conversa(funil="b2b", estagio="P1", nome="Beto")
        self.fake.atualizar_estado_conversa(fechada.id, resultado="perdido")

        resposta = self.cliente.get("/api/conversas")
        corpo = resposta.json()
        por_id = {c["id"]: c for c in corpo["conversas"]}

        self.assertIn(aberta.id, por_id)
        self.assertIsNone(por_id[aberta.id]["resultado"])
        self.assertIn(fechada.id, por_id)
        self.assertEqual(por_id[fechada.id]["resultado"], "perdido")

    def test_conversa_fechada_por_marco_manual_some_do_kanban(self):
        """Requirement "Kanban e fila continuam mostrando só conversas
        abertas": a mesma conversa fechada não aparece em `GET
        /api/kanban`."""
        fechada = self.fake.criar_conversa(funil="b2b", estagio="P1", nome="Beto")
        self.fake.atualizar_estado_conversa(fechada.id, resultado="perdido")

        resposta = self.cliente.get("/api/kanban")
        corpo = resposta.json()
        ids_no_kanban = {
            card["id"]
            for kanban in corpo["kanbans"]
            for coluna in kanban["colunas"]
            for card in coluna["cards"]
        }
        self.assertNotIn(fechada.id, ids_no_kanban)

    def test_conversa_fechada_por_marco_manual_some_da_fila(self):
        """Mesmo requirement acima, aplicado à fila do dia."""
        fechada = self.fake.criar_conversa(funil="b2b", estagio="P1", nome="Beto")
        self.fake.atualizar_estado_conversa(fechada.id, resultado="perdido")

        resposta = self.cliente.get("/api/fila")
        ids_na_fila = {item["conversa_id"] for item in resposta.json()["itens"]}
        self.assertNotIn(fechada.id, ids_na_fila)

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

    def test_o_que_funciona_smoke(self):
        """Change `analise-desempenho`: rota nova, banco vazio — nenhum bloco
        pode quebrar com n=0.

        Change `ground-truth-no-painel`: o bloco `acuracia_extracao` agora
        existe sempre, mas sem cache de `POST /eval/rodar` ele só carrega
        `disponivel: False` — nenhum número de acurácia é afirmado (a
        restrição de `project.md`), o que esta suíte confere mais abaixo em
        `TesteEvalPainel`.
        """
        resposta = self.cliente.get("/api/o-que-funciona")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        for chave in ("funil", "tempo_por_estagio", "objecoes", "correcoes",
                      "followups", "rascunhos", "acuracia_extracao"):
            self.assertIn(chave, corpo)
        self.assertIn("onde_morrem", corpo["funil"])
        self.assertEqual(corpo["funil"]["onde_morrem"]["n"], 0)
        self.assertIn("bloqueado", corpo["rascunhos"])
        self.assertTrue(corpo["rascunhos"]["bloqueado"])
        self.assertEqual(corpo["acuracia_extracao"], {"disponivel": False})

    def test_o_que_funciona_respeita_dias(self):
        resposta = self.cliente.get("/api/o-que-funciona?dias=7")
        self.assertEqual(resposta.status_code, 200)

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
        self.assertEqual(corpo["total"], 1)
        self.assertFalse(corpo["tem_mais"])

    def test_mensagens_recentes_por_padrao_em_conversa_longa(self):
        """Requirement "Mensagens recentes aparecem por padrão": conversa com
        mais de 200 mensagens devolve, sem `desde_id`, as MAIS RECENTES — não
        as 200 mais antigas (o bug que este change corrige)."""
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        for i in range(250):
            self.fake.registrar_mensagem(conversa.id, "in", f"msg {i}")

        resposta = self.cliente.get(f"/api/conversas/{conversa.id}/mensagens")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["mensagens"]), 200)
        self.assertEqual(corpo["total"], 250)
        self.assertTrue(corpo["tem_mais"])
        # As mais RECENTES: a última da janela é a msg 249 (a mais nova),
        # não a msg 199 (o que o bug antigo, `ORDER BY id ASC LIMIT 200`,
        # devolvia).
        self.assertEqual(corpo["mensagens"][-1]["texto"], "msg 249")
        self.assertEqual(corpo["mensagens"][0]["texto"], "msg 50")

    def test_mensagens_desde_id_continua_incremental(self):
        """Catch-up (usado pelo SSE, `painel/stream.py`) não regride: com
        `desde_id`, continua ORDEM CRESCENTE a partir do id informado, nunca
        as "mais recentes"."""
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        ids = [
            self.fake.registrar_mensagem(conversa.id, "in", f"msg {i}")
            for i in range(5)
        ]
        resposta = self.cliente.get(
            f"/api/conversas/{conversa.id}/mensagens?desde_id={ids[1]}"
        )
        corpo = resposta.json()
        self.assertEqual([m["texto"] for m in corpo["mensagens"]], ["msg 2", "msg 3", "msg 4"])

    def test_mensagens_antes_de_pagina_para_tras(self):
        """Requirement "Mensagens recentes aparecem por padrão": `antes_de` é
        o cursor real para carregar histórico mais antigo sob demanda, depois
        que a página inicial (mais recentes) já foi exibida."""
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Ana")
        ids = [
            self.fake.registrar_mensagem(conversa.id, "in", f"msg {i}")
            for i in range(10)
        ]

        primeira_pagina = self.cliente.get(
            f"/api/conversas/{conversa.id}/mensagens?limite=4"
        ).json()
        self.assertEqual(
            [m["texto"] for m in primeira_pagina["mensagens"]],
            ["msg 6", "msg 7", "msg 8", "msg 9"],
        )
        self.assertTrue(primeira_pagina["tem_mais"])

        cursor = primeira_pagina["mensagens"][0]["id"]
        self.assertEqual(cursor, ids[6])
        segunda_pagina = self.cliente.get(
            f"/api/conversas/{conversa.id}/mensagens?limite=4&antes_de={cursor}"
        ).json()
        self.assertEqual(
            [m["texto"] for m in segunda_pagina["mensagens"]],
            ["msg 2", "msg 3", "msg 4", "msg 5"],
        )


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

    def test_desconsiderar_recusa_reabre_e_grava_correcao(self):
        """Change `estagio-reabertura-manual-e-relogio`: botão no detalhe da
        conversa, mesma sequência de efeitos que a CLI (`acoes.
        desconsiderar_recusa`)."""
        conversa = self.fake.criar_conversa(funil="b2c", estagio="SX")
        self.fake.fatos.append((conversa.id, "recusa_explicita", "não quero", None, None))
        resposta = self.cliente.post(
            f"/api/conversas/{conversa.id}/desconsiderar-recusa",
            json={"por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertTrue(self.fake.recusa_desconsiderada(conversa.id))
        self.assertTrue(self.fake.fatos_da_conversa(conversa.id).get("recusa_explicita"))

    def test_desconsiderar_recusa_sem_por_devolve_422(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="SX")
        self.fake.fatos.append((conversa.id, "recusa_explicita", "não quero", None, None))
        resposta = self.cliente.post(
            f"/api/conversas/{conversa.id}/desconsiderar-recusa", json={}
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertFalse(self.fake.recusa_desconsiderada(conversa.id))

    def test_desconsiderar_recusa_sem_fato_devolve_422(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S2")
        resposta = self.cliente.post(
            f"/api/conversas/{conversa.id}/desconsiderar-recusa",
            json={"por": "marcos"},
        )
        self.assertEqual(resposta.status_code, 422)


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


class TesteRotaDeExtracaoManual(unittest.TestCase):
    """Change `extracao-em-lote-por-janela`: `POST /conversas/{id}/extrair`
    ignora o gatilho híbrido do webhook — o operador força a extração do
    bloco pendente na hora, sem esperar limiar nem `camucrm extrair`."""

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

    def test_extrai_o_bloco_pendente_e_devolve_o_card_atualizado(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "aqui esta a foto do meu pet")
        resposta_llm = json.dumps({
            "foto_pet_recebida": True,
            "objecao": None,
            "evidencias": {"foto_pet_recebida": "aqui esta a foto do meu pet"},
        })
        with patch.object(api, "criar_llm", return_value=FakeLlm([resposta_llm])):
            resposta = self.cliente.post(f"/api/conversas/{conversa.id}/extrair")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["mensagens_processadas"], 1)
        self.assertEqual(corpo["card"]["estagio"], "S2")

    def test_conversa_inexistente_devolve_422(self):
        resposta = self.cliente.post("/api/conversas/999/extrair")
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("erro", resposta.json())

    def test_llm_indisponivel_devolve_422(self):
        from camucrm.llm import LlmIndisponivelError

        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi")

        def _quebrado(*args, **kwargs):
            raise LlmIndisponivelError("sem chave configurada")

        with patch.object(api, "criar_llm", side_effect=_quebrado):
            resposta = self.cliente.post(f"/api/conversas/{conversa.id}/extrair")
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("erro", resposta.json())

    def test_sem_mensagem_pendente_nao_chama_llm_e_devolve_ok(self):
        """Extrair uma conversa já em dia é barato — `mensagens_novas` vazia
        cai no ramo de `recalcular` sem chamar o LLM (mesma garantia de
        `Extrator.processar_conversa`)."""
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S1", nome="Ana")
        with patch.object(api, "criar_llm", return_value=FakeLlm([])):
            resposta = self.cliente.post(f"/api/conversas/{conversa.id}/extrair")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["mensagens_processadas"], 0)


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


class TesteRotasDeProspeccao(unittest.TestCase):
    """Change `prospeccao-b2b-shortlist`: as três rotas novas — importar
    (CSV), listar (com filtros) e abrir (registra intenção de disparo,
    nunca envia). Sempre separado de contatos/conversas — ver
    `TesteProspeccaoNuncaAparecEmTelasDeConversa` abaixo."""

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

    def _csv(self, linhas: str) -> bytes:
        cabecalho = "petshop,bairro,zona,telefone,nota,avaliacoes,site,tier_origem,status_origem\n"
        return (cabecalho + linhas).encode("utf-8")

    def test_importar_csv_cria_linhas_e_reporta_resumo(self):
        conteudo = self._csv(
            "Petshop A,Centro,Leste,(12) 98157-5051,4.6,223,,A,valido\n"
            "Petshop Sem Telefone,Centro,Leste,,4.0,10,,B,valido\n"
        )
        resposta = self.cliente.post(
            "/api/prospeccao/importar",
            files={"arquivo": ("shortlist.csv", conteudo, "text/csv")},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["novos"], 1)
        self.assertEqual(corpo["atualizados"], 0)
        self.assertEqual(len(corpo["invalidas"]), 1)
        self.assertIn("ilegível", corpo["invalidas"][0]["motivo"])

    def test_reimportar_mesmo_csv_atualiza_nao_duplica(self):
        conteudo = self._csv("Petshop A,Centro,Leste,(12) 98157-5051,4.6,223,,A,valido\n")
        self.cliente.post(
            "/api/prospeccao/importar",
            files={"arquivo": ("shortlist.csv", conteudo, "text/csv")},
        )
        resposta = self.cliente.post(
            "/api/prospeccao/importar",
            files={"arquivo": ("shortlist.csv", conteudo, "text/csv")},
        )
        corpo = resposta.json()
        self.assertEqual(corpo["novos"], 0)
        self.assertEqual(corpo["atualizados"], 1)

    def test_listar_prospeccao_traz_link_e_mensagem(self):
        self.fake.criar_prospeccao(
            nome="Petshop com Slogan | Desde sempre", telefone="5512999990010",
        )
        with patch("camucrm.painel.api.config.mensagem_prospeccao", return_value="Oi, {nome}!"):
            resposta = self.cliente.get("/api/prospeccao")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["prospeccoes"]), 1)
        item = corpo["prospeccoes"][0]
        self.assertFalse(item["convertida"])
        self.assertEqual(item["mensagem"], "Oi, petshop com slogan!")
        self.assertTrue(item["link_whatsapp"].startswith("https://api.whatsapp.com/send/"))

    def test_listar_prospeccao_respeita_filtro_de_zona(self):
        self.fake.criar_prospeccao(nome="Leste", zona="Leste", telefone="5512999990011")
        self.fake.criar_prospeccao(nome="Norte", zona="Norte", telefone="5512999990012")
        resposta = self.cliente.get("/api/prospeccao?zona=Norte")
        corpo = resposta.json()
        self.assertEqual([p["nome"] for p in corpo["prospeccoes"]], ["Norte"])

    def test_linha_convertida_nao_traz_link_de_whatsapp(self):
        telefone = "5512999990013"
        self.fake.criar_prospeccao(nome="Convertido", telefone=telefone)
        self.fake.criar_conversa(funil="b2b", estagio="P0", nome="Convertido", telefone=telefone)
        with patch("camucrm.painel.api.config.mensagem_prospeccao", return_value="Oi, {nome}!"):
            resposta = self.cliente.get("/api/prospeccao")
        item = resposta.json()["prospeccoes"][0]
        self.assertTrue(item["convertida"])
        self.assertIsNone(item["mensagem"])
        self.assertIsNone(item["link_whatsapp"])
        self.assertIsNotNone(item["conversa_id"])

    def test_abrir_prospeccao_registra_quem_e_quando(self):
        prospeccao = self.fake.criar_prospeccao(nome="Petshop Z", telefone="5512999990014")
        resposta = self.cliente.post(
            f"/api/prospeccao/{prospeccao.id}/abrir", json={"por": "marcos"}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["ok"])
        registro = self.fake.listar_prospeccoes()[0]
        self.assertIsNotNone(registro.aberto_em)
        self.assertEqual(registro.aberto_por, "marcos")

    def test_rota_de_envio_de_prospeccao_e_a_unica_com_enviar(self):
        """Até o change `envio-prospeccao-pela-evolution-api`, nenhuma rota
        de prospecção continha "enviar" — o disparo era só o link `wa.me`.
        Esse change adicionou EXATAMENTE uma, de propósito (ver
        `TesteEnvioProspeccao`); qualquer outra seria bug."""
        caminhos = {c for c in server.app.openapi()["paths"].keys() if "prospeccao" in c}
        com_enviar = {c for c in caminhos if "enviar" in c}
        self.assertEqual(com_enviar, {"/api/prospeccao/{prospeccao_id}/enviar"})


class TesteEnvioProspeccao(unittest.TestCase):
    """Change `envio-prospeccao-pela-evolution-api`: `POST /prospeccao/{id}/
    enviar` chama a Evolution API de verdade, via `camucrm.painel.envio`.

    `criar_transporte` é substituído por um fake local (nunca a rede real) —
    mesmo padrão de `FakeLlm` para LLM: um objeto que satisfaz o protocolo
    de `Transporte`, não um `Mock` genérico, para exercitar o caminho real
    de chamada (`transporte.enviar(Destinatario(...), texto,
    aprovado_por=...)`) em vez de só verificar que "algo foi chamado"."""

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
        self.prospeccao = self.fake.criar_prospeccao(
            nome="Petshop Envio", telefone="5512999990099"
        )

    def _enviar(self, **corpo):
        base = {"telefone": "5512999990099", "mensagem": "Oi, tudo bem?", "por": "marcos"}
        base.update(corpo)
        return self.cliente.post(
            f"/api/prospeccao/{self.prospeccao.id}/enviar", json=base
        )

    def test_envio_com_sucesso_grava_enviado_em_e_devolve_ok(self):
        transporte = FakeTransporteEvolution(sucesso=True, externa_id="MSG1")
        with patch("camucrm.painel.envio.criar_transporte", return_value=transporte):
            resposta = self._enviar()
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(corpo["externa_id"], "MSG1")
        registro = self.fake.listar_prospeccoes()[0]
        self.assertIsNotNone(registro.enviado_em)
        self.assertEqual(registro.enviado_por, "marcos")
        self.assertIsNone(registro.enviado_erro)

    def test_texto_e_telefone_enviados_sao_os_do_corpo_da_requisicao(self):
        """Requirement do spec.md: o que o operador editou no popup é o que
        sai — não o telefone/mensagem originais de `prospeccoes`/template."""
        transporte = FakeTransporteEvolution(sucesso=True)
        with patch("camucrm.painel.envio.criar_transporte", return_value=transporte):
            self._enviar(telefone="5511888887777", mensagem="Texto editado à mão")
        self.assertEqual(transporte.chamadas[0]["telefone"], "5511888887777")
        self.assertEqual(transporte.chamadas[0]["texto"], "Texto editado à mão")
        self.assertEqual(transporte.chamadas[0]["aprovado_por"], "marcos")

    def test_falha_de_transporte_devolve_502_e_grava_erro(self):
        transporte = FakeTransporteEvolution(sucesso=False, mensagem_erro="Evolution fora do ar")
        with patch("camucrm.painel.envio.criar_transporte", return_value=transporte):
            resposta = self._enviar()
        self.assertEqual(resposta.status_code, 502)
        self.assertIn("Evolution fora do ar", resposta.json()["erro"])
        registro = self.fake.listar_prospeccoes()[0]
        self.assertIsNone(registro.enviado_em)
        self.assertIn("Evolution fora do ar", registro.enviado_erro)

    def test_sucesso_anterior_sobrevive_a_falha_seguinte(self):
        """`enviado_em` de uma tentativa que funcionou não é apagado por uma
        tentativa seguinte que falha (design.md)."""
        ok = FakeTransporteEvolution(sucesso=True)
        with patch("camucrm.painel.envio.criar_transporte", return_value=ok):
            self._enviar()
        enviado_em_original = self.fake.listar_prospeccoes()[0].enviado_em

        ruim = FakeTransporteEvolution(sucesso=False, mensagem_erro="timeout")
        with patch("camucrm.painel.envio.criar_transporte", return_value=ruim):
            self._enviar()
        registro = self.fake.listar_prospeccoes()[0]
        self.assertEqual(registro.enviado_em, enviado_em_original)
        self.assertIn("timeout", registro.enviado_erro)

    def test_por_vazio_e_recusado_antes_de_tocar_rede(self):
        transporte = FakeTransporteEvolution(sucesso=True)
        with patch("camucrm.painel.envio.criar_transporte", return_value=transporte) as fabrica:
            resposta = self._enviar(por="")
        self.assertEqual(resposta.status_code, 422)
        fabrica.assert_not_called()

    def test_telefone_vazio_e_recusado(self):
        resposta = self._enviar(telefone="")
        self.assertEqual(resposta.status_code, 422)

    def test_mensagem_vazia_e_recusada(self):
        resposta = self._enviar(mensagem="  ")
        self.assertEqual(resposta.status_code, 422)

    def test_config_ausente_no_processo_do_painel_devolve_502_com_detalhe(self):
        """`criar_transporte` levanta `RuntimeError` (não `TransporteError`)
        quando faltam as env vars da Evolution — caso real até o operador
        configurar o `.env` do painel. A rota traduz para 502 com o detalhe,
        não um 500 genérico."""
        with patch(
            "camucrm.painel.envio.criar_transporte",
            side_effect=RuntimeError("CAMU_TRANSPORTE=evolution exige EVOLUTION_API_KEY"),
        ):
            resposta = self._enviar()
        self.assertEqual(resposta.status_code, 502)
        self.assertIn("EVOLUTION_API_KEY", resposta.json()["erro"])


class TesteProspeccaoNuncaAparecEmTelasDeConversa(unittest.TestCase):
    """Requirement "Shortlist separada de contatos/conversas" (design.md):
    importar prospecção não pode mudar o que kanban/fila/conversas/
    o-que-funciona mostram — nenhuma dessas rotas lê `prospeccoes`."""

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

    def test_prospeccoes_nao_aparecem_em_kanban_fila_conversas_ou_o_que_funciona(self):
        for i in range(5):
            self.fake.criar_prospeccao(nome=f"Petshop {i}", telefone=f"551299999{i:04d}")

        kanban = self.cliente.get("/api/kanban").json()
        self.assertEqual(kanban["total"], 0)
        for k in kanban["kanbans"]:
            for coluna in k["colunas"]:
                self.assertEqual(coluna["cards"], [])

        fila = self.cliente.get("/api/fila").json()
        self.assertEqual(fila["total"], 0)
        self.assertEqual(fila["itens"], [])

        conversas = self.cliente.get("/api/conversas").json()
        self.assertEqual(conversas["total"], 0)
        self.assertEqual(conversas["conversas"], [])

        funciona = self.cliente.get("/api/o-que-funciona").json()
        self.assertEqual(funciona["funil"]["onde_morrem"]["n"], 0)


class TesteRotaDeImportacaoWhatsapp(unittest.TestCase):
    """Change `importacao-conversas-whatsapp`: upload do `.txt` exportado
    pelo próprio WhatsApp — parse em memória + `backfill.
    importar_conversas` reaproveitado. Extração continua sendo a rota que
    já existe (`TesteRotaDeExtracaoManual` acima), nunca uma rota nova."""

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

    def _upload(self, texto: str, **campos):
        dados = {
            "telefone": "5512999990099",
            "tipo": "b2c",
            "nome_operador": "Camu",
            **campos,
        }
        return self.cliente.post(
            "/api/importacao-whatsapp",
            files={"arquivo": ("conversa.txt", texto.encode("utf-8"), "text/plain")},
            data=dados,
        )

    def test_upload_feliz_cria_mensagens_e_devolve_resumo(self):
        texto = (
            "17/03/24, 14:32 - Ana Petshop: oi, vocês fazem porta-chaves?\n"
            "17/03/24, 14:35 - Camu: fazemos sim! manda a foto do pet\n"
            "17/03/24, 14:36 - Ana Petshop: <Mídia oculta>\n"
            "17/03/24, 14:37 - alguma coisa sem remetente reconhecido"
        )
        resposta = self._upload(texto)
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertIn("conversa_id", corpo)
        self.assertEqual(corpo["nome_contato"], "Ana Petshop")
        self.assertEqual(corpo["mensagens_novas"], 3)
        self.assertEqual(corpo["midia_preservada"], 1)
        self.assertEqual(len(corpo["ignoradas"]), 1)

        conversa_id = corpo["conversa_id"]
        mensagens = self.fake.listar_mensagens(conversa_id)
        self.assertEqual(len(mensagens), 3)

    def test_reimportar_mesmo_arquivo_nao_duplica(self):
        texto = "17/03/24, 14:32 - Ana Petshop: oi\n17/03/24, 14:35 - Camu: oi"
        primeira = self._upload(texto).json()
        segunda = self._upload(texto).json()
        self.assertEqual(primeira["conversa_id"], segunda["conversa_id"])
        self.assertEqual(segunda["mensagens_novas"], 0)
        mensagens = self.fake.listar_mensagens(primeira["conversa_id"])
        self.assertEqual(len(mensagens), 2)

    def test_grupo_retorna_422_sem_gravar_nada(self):
        texto = (
            "17/03/24, 14:32 - Ana: oi\n"
            "17/03/24, 14:33 - Bruno: fala\n"
            "17/03/24, 14:34 - Camu: oi pessoal"
        )
        resposta = self._upload(texto)
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("erro", resposta.json())
        self.assertEqual(self.fake.contatos, {})
        self.assertEqual(self.fake.mensagens, {})

    def test_nome_operador_sem_correspondencia_retorna_422_sem_gravar_nada(self):
        texto = "17/03/24, 14:32 - Ana Petshop: oi"
        resposta = self._upload(texto, nome_operador="Ninguém Com Esse Nome")
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.fake.contatos, {})
        self.assertEqual(self.fake.mensagens, {})

    def test_tipo_invalido_retorna_422(self):
        resposta = self._upload("17/03/24, 14:32 - Ana: oi", tipo="pessoa-fisica")
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.fake.contatos, {})

    def test_telefone_vazio_retorna_422(self):
        resposta = self._upload("17/03/24, 14:32 - Ana: oi", telefone="   ")
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.fake.contatos, {})

    def test_upload_sozinho_nao_chama_llm(self):
        """Upload só grava mensagem — extração é passo separado (a rota já
        existente `POST /conversas/{id}/extrair`, `TesteRotaDeExtracaoManual`
        acima). Nenhuma chamada a `criar_llm` acontece nesta rota."""
        texto = "17/03/24, 14:32 - Ana Petshop: oi\n17/03/24, 14:35 - Camu: oi"
        with patch.object(api, "criar_llm") as llm_mock:
            resposta = self._upload(texto)
        self.assertEqual(resposta.status_code, 200)
        llm_mock.assert_not_called()

    def test_extracao_da_conversa_importada_entra_em_metrica_de_tempo(self):
        """Requirement "Extração usa origem='live', com timestamp real por
        transição" (`design.md`, Decisão 1 revisada): nunca
        `origem='backfill'` aqui, então o evento fica de pé em
        `metrics.tempo_por_estagio` como qualquer conversa ao vivo — a rota
        de extração é a que já existe, sem nenhuma mudança nela."""
        # Datas relativas a "agora", não fixas: §3 deriva SX (perdido) para
        # B2C sem resposta há >=14 dias (DIAS_ATE_PERDIDO_B2C) — uma data
        # fixa antiga faria o teste depender de quando ele roda.
        ontem = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%d/%m/%y")
        texto = (
            f"{ontem}, 08:00 - Ana Petshop: oi, tudo bem?\n"
            f"{ontem}, 08:05 - Camu: tudo, manda a foto do pet\n"
            f"{ontem}, 09:00 - Ana Petshop: aqui esta a foto do meu pet"
        )
        conversa_id = self._upload(texto).json()["conversa_id"]

        resposta_llm = json.dumps({
            "foto_pet_recebida": True,
            "objecao": None,
            "evidencias": {"foto_pet_recebida": "aqui esta a foto do meu pet"},
        })
        with patch.object(api, "criar_llm", return_value=FakeLlm([resposta_llm])):
            extracao = self.cliente.post(f"/api/conversas/{conversa_id}/extrair")
        self.assertEqual(extracao.status_code, 200)
        self.assertEqual(extracao.json()["card"]["estagio"], "S2")

        eventos = [e for e in self.fake.eventos if e["conversa_id"] == conversa_id]
        self.assertTrue(eventos)
        for evento in eventos:
            self.assertEqual(evento["origem"], "live")

        linhas = {t.estagio: t for t in metrics.tempo_por_estagio(self.fake)}
        self.assertIn("S1", linhas)
        self.assertGreaterEqual(linhas["S1"].conversas, 1)

    def test_rota_de_importacao_nao_escreve_arquivo_em_disco(self):
        """Requirement "Upload não persiste o arquivo bruto em disco" —
        verificação estrutural por AST (mesmo padrão de
        `test_nenhum_modulo_do_painel_importa_transport`): a função da rota
        nunca chama `open(...)` nem `Path(...).write_*`."""
        caminho = Path(api.__file__)
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        funcao = next(
            no
            for no in ast.walk(arvore)
            if isinstance(no, ast.FunctionDef) and no.name == "importar_conversa_whatsapp"
        )
        for no in ast.walk(funcao):
            if isinstance(no, ast.Call):
                alvo = no.func
                if isinstance(alvo, ast.Name):
                    self.assertNotEqual(alvo.id, "open")
                if isinstance(alvo, ast.Attribute):
                    self.assertNotIn(alvo.attr, {"write_bytes", "write_text", "write"})


if __name__ == "__main__":
    unittest.main()
