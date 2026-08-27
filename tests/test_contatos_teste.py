"""Change `contatos-de-teste-isolados`: contato marcado como teste fica fora
do kanban/fila/conversas/métricas por padrão, e só aparece (tudo junto) com
"modo teste" ligado — nunca os dois juntos na mesma tela.

Cobre, por `FakeDatabase` (um contato normal + um de teste):
  - cada função da lista do proposal.md — modo padrão exclui teste, "modo
    teste" mostra só teste;
  - `marcar_contato_teste` grava em `correcoes`, nunca em `marcos_manuais`;
  - extração/regras/rascunho/resumo continuam rodando para conversa de
    contato de teste — a flag não desliga processamento (teste dedicado);
  - `camucrm marcar-teste`/`--desfazer` e `camucrm fila --incluir-teste`/
    `--somente-teste`;
  - marcação só acontece via ação explícita — nenhum caminho automático
    marca um contato como teste sozinho.

Sem rede e sem Postgres — `FakeDatabase`/`FakeLlm` (convenção do repo).
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm import cli, metrics  # noqa: E402
from camucrm.db import _condicao_teste  # noqa: E402
from camucrm.drafts import gerar as gerar_rascunho  # noqa: E402
from camucrm.extraction.extractor import Extrator  # noqa: E402
from camucrm.llm import FakeLlm  # noqa: E402
from camucrm.painel import server  # noqa: E402
from camucrm.pipeline import recalcular  # noqa: E402
from camucrm.summaries import ContextoResumo, PROMPT_VERSAO_RESUMO  # noqa: E402
from camucrm.summaries import gerar as gerar_resumo  # noqa: E402

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _resposta_extracao(**campos):
    payload = {"objecao": None, "evidencias": {}}
    payload.update(campos)
    return json.dumps(payload)


class TesteCondicaoTeste(unittest.TestCase):
    """`db._condicao_teste` — os três modos, função pura."""

    def test_padrao_exclui_teste(self):
        self.assertEqual(
            _condicao_teste("ct.e_teste", incluir_teste=False, apenas_teste=False),
            "AND ct.e_teste = FALSE",
        )

    def test_apenas_teste_mostra_so_teste(self):
        self.assertEqual(
            _condicao_teste("ct.e_teste", incluir_teste=False, apenas_teste=True),
            "AND ct.e_teste = TRUE",
        )

    def test_incluir_teste_sem_filtro(self):
        self.assertEqual(
            _condicao_teste("ct.e_teste", incluir_teste=True, apenas_teste=False), ""
        )

    def test_os_dois_juntos_recusa(self):
        with self.assertRaises(ValueError):
            _condicao_teste("ct.e_teste", incluir_teste=True, apenas_teste=True)


class TesteMarcarContatoTeste(unittest.TestCase):
    """Requirement "Marcação de teste é sempre manual e registrada"."""

    def test_grava_em_correcoes_nao_em_marcos_manuais(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Marcos Shiba")
        db.marcar_contato_teste(conversa.contato_id, True, por="operador")

        self.assertTrue(db.contatos[conversa.contato_id].e_teste)
        correcoes = [c for c in db.correcoes if c["conversa_id"] == conversa.id]
        self.assertEqual(len(correcoes), 1)
        self.assertEqual(correcoes[0]["campo"], "e_teste")
        self.assertEqual(correcoes[0]["por"], "operador")
        # Nenhum marco manual foi gravado por esta ação (§3 é outro conceito).
        self.assertEqual(db.marcos.get(conversa.id, set()), set())

    def test_desfazer_marca_grava_segunda_correcao(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")
        db.marcar_contato_teste(conversa.contato_id, True, por="op1")
        db.marcar_contato_teste(conversa.contato_id, False, por="op2")

        self.assertFalse(db.contatos[conversa.contato_id].e_teste)
        correcoes = [c for c in db.correcoes if c["conversa_id"] == conversa.id]
        self.assertEqual(len(correcoes), 2)
        self.assertEqual(correcoes[-1]["antes"], True)
        self.assertEqual(correcoes[-1]["depois"], False)

    def test_contato_inexistente_recusa(self):
        db = FakeDatabase()
        with self.assertRaises(ValueError):
            db.marcar_contato_teste(999, True, por="operador")

    def test_upsert_contato_nunca_marca_teste_sozinho(self):
        """Nenhum caminho automático decide "teste" — `upsert_contato` (o
        único jeito de um contato entrar no sistema, via ingestão) sempre
        nasce `e_teste=False`, e chamadas repetidas (reentrega de webhook)
        nunca mudam isso."""
        db = FakeDatabase()
        contato = db.upsert_contato("5511999998888", nome="Cliente", tipo="b2c")
        self.assertFalse(contato.e_teste)
        contato_de_novo = db.upsert_contato("5511999998888", nome="Cliente")
        self.assertFalse(contato_de_novo.e_teste)


class _BaseDoisContatos(unittest.TestCase):
    """Um contato normal e um de teste, cada um com uma conversa aberta —
    fixture compartilhada pelas provas de filtro abaixo."""

    def setUp(self):
        self.db = FakeDatabase()
        self.normal = self.db.criar_conversa(nome="Cliente Real", estagio="S0")
        self.teste = self.db.criar_conversa(nome="Marcos Shiba", estagio="S0", e_teste=True)


class TesteListarConversasAbertas(_BaseDoisContatos):
    def test_padrao_exclui_teste(self):
        ids = {c.id for c in self.db.listar_conversas_abertas()}
        self.assertIn(self.normal.id, ids)
        self.assertNotIn(self.teste.id, ids)

    def test_apenas_teste_mostra_so_teste(self):
        ids = {c.id for c in self.db.listar_conversas_abertas(apenas_teste=True)}
        self.assertEqual(ids, {self.teste.id})

    def test_incluir_teste_mostra_os_dois(self):
        ids = {c.id for c in self.db.listar_conversas_abertas(incluir_teste=True)}
        self.assertEqual(ids, {self.normal.id, self.teste.id})

    def test_os_dois_parametros_juntos_recusa(self):
        with self.assertRaises(ValueError):
            self.db.listar_conversas_abertas(incluir_teste=True, apenas_teste=True)


class TesteMetricasConversao(_BaseDoisContatos):
    def setUp(self):
        super().setUp()
        for c in (self.normal, self.teste):
            self.db.gravar_evento_estagio(c.id, None, "S1")
            self.db.gravar_evento_estagio(c.id, "S1", "S2")

    def test_conversao_padrao_exclui_teste(self):
        resultado = metrics.conversao(self.db, "S1", "S2")
        self.assertEqual(resultado.alcancaram_de, 1)
        self.assertEqual(resultado.alcancaram_para, 1)

    def test_conversao_apenas_teste(self):
        resultado = metrics.conversao(self.db, "S1", "S2", apenas_teste=True)
        self.assertEqual(resultado.alcancaram_de, 1)
        self.assertEqual(resultado.alcancaram_para, 1)

    def test_metricas_chave_padrao_exclui_teste(self):
        # S1->S2 é um dos três pares da §14.
        resultado = {(c.de, c.para): c for c in metrics.metricas_chave(self.db)}
        self.assertEqual(resultado[("S1", "S2")].alcancaram_de, 1)

    def test_conversao_adjacente_apenas_teste(self):
        resultado = {
            (c.de, c.para): c
            for c in metrics.conversao_adjacente(self.db, "b2c", apenas_teste=True)
        }
        self.assertEqual(resultado[("S1", "S2")].alcancaram_de, 1)


class TesteTempoPorEstagio(_BaseDoisContatos):
    def setUp(self):
        super().setUp()
        for c in (self.normal, self.teste):
            self.db.gravar_evento_estagio(c.id, None, "S1", em=AGORA)
            self.db.gravar_evento_estagio(c.id, "S1", "S2", em=AGORA + timedelta(hours=10))

    def test_padrao_exclui_teste(self):
        linhas = {t.estagio: t for t in metrics.tempo_por_estagio(self.db)}
        self.assertEqual(linhas["S1"].conversas, 1)

    def test_apenas_teste(self):
        linhas = {
            t.estagio: t for t in metrics.tempo_por_estagio(self.db, apenas_teste=True)
        }
        self.assertEqual(linhas["S1"].conversas, 1)


class TesteOndeMorrem(_BaseDoisContatos):
    def setUp(self):
        super().setUp()
        for c in (self.normal, self.teste):
            self.db.gravar_evento_estagio(c.id, None, "S1")
            self.db.conversas[c.id].resultado = "perdido"

    def test_padrao_exclui_teste(self):
        resultado = metrics.onde_morrem(self.db)
        self.assertEqual(resultado.n, 1)
        self.assertEqual(resultado.distribuicao, {"S1": 1})

    def test_apenas_teste(self):
        resultado = metrics.onde_morrem(self.db, apenas_teste=True)
        self.assertEqual(resultado.n, 1)


class TesteObjecoesETaxonomia(_BaseDoisContatos):
    def setUp(self):
        super().setUp()
        for c in (self.normal, self.teste):
            self.db.gravar_objecao(c.id, "preco", estagio="S2", trecho=f"caro {c.id}")

    def test_saude_taxonomia_padrao_exclui_teste(self):
        resultado = metrics.saude_taxonomia(self.db)
        self.assertEqual(resultado.total, 1)

    def test_saude_taxonomia_apenas_teste(self):
        resultado = metrics.saude_taxonomia(self.db, apenas_teste=True)
        self.assertEqual(resultado.total, 1)

    def test_objecao_por_estagio_padrao_exclui_teste(self):
        resultado = metrics.objecao_por_estagio(self.db)
        self.assertEqual(resultado.n, 1)

    def test_objecao_por_estagio_apenas_teste(self):
        resultado = metrics.objecao_por_estagio(self.db, apenas_teste=True)
        self.assertEqual(resultado.n, 1)


class TestePadraoCorrecoes(_BaseDoisContatos):
    def setUp(self):
        super().setUp()
        for c in (self.normal, self.teste):
            self.db.registrar_correcao(c.id, "funil", "b2c", "b2b")

    def test_padrao_exclui_teste(self):
        resultado = metrics.padrao_correcoes(self.db)
        self.assertEqual(sum(l.n for l in resultado), 1)

    def test_apenas_teste(self):
        resultado = metrics.padrao_correcoes(self.db, apenas_teste=True)
        self.assertEqual(sum(l.n for l in resultado), 1)


class TesteRetornoPorFollowup(_BaseDoisContatos):
    def setUp(self):
        super().setUp()
        for c in (self.normal, self.teste):
            self.db.registrar_mensagem(c.id, "out", "oi", AGORA)
            self.db.followups[c.id] = [(1, "toque 1", AGORA)]
            self.db.registrar_mensagem(c.id, "in", "respondeu", AGORA + timedelta(hours=1))

    def test_padrao_exclui_teste(self):
        resultado = {r.numero: r for r in metrics.retorno_por_followup(self.db)}
        self.assertEqual(resultado[1].n, 1)

    def test_apenas_teste(self):
        resultado = {
            r.numero: r
            for r in metrics.retorno_por_followup(self.db, apenas_teste=True)
        }
        self.assertEqual(resultado[1].n, 1)


class TesteAbRascunhos(_BaseDoisContatos):
    def _vincular(self, conversa_id):
        rid = self.db.gravar_rascunho(
            conversa_id, estagio="S4", temperatura="quente", funil="b2c",
            opcoes=("opção 1", "opção 2"),
        )
        self.db.registrar_escolha_rascunho(rid, escolhida=1)
        mid = self.db.registrar_mensagem(conversa_id, "out", "texto", AGORA)
        self.db.vincular_rascunho(rid, mid, estagio_no_envio="S4")

    def setUp(self):
        super().setUp()
        self._vincular(self.normal.id)
        self._vincular(self.teste.id)

    def test_padrao_exclui_teste(self):
        resultado = metrics.ab_rascunhos(self.db)
        self.assertEqual(resultado.n_vinculados, 1)

    def test_apenas_teste(self):
        resultado = metrics.ab_rascunhos(self.db, apenas_teste=True)
        self.assertEqual(resultado.n_vinculados, 1)


class TesteProcessamentoContinuaParaContatoDeTeste(unittest.TestCase):
    """Requirement "Marcação de teste não afeta processamento": extração,
    regras de estágio/temperatura, rascunho e resumo rodam para uma conversa
    de contato de teste exatamente como rodariam para um contato normal — a
    flag é só de visibilidade/agregação (proposal.md, "Fora de escopo").
    """

    def test_extracao_regras_rascunho_e_resumo_rodam_normalmente(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Marcos Shiba", e_teste=True)
        self.assertTrue(db.contatos[conversa.contato_id].e_teste)

        db.registrar_mensagem(
            conversa.id, "in", "oi, vi o insta de voces", AGORA - timedelta(hours=6)
        )
        db.registrar_mensagem(
            conversa.id, "out", "Oi! Me manda uma foto do seu pet?",
            AGORA - timedelta(hours=5),
        )
        db.registrar_mensagem(
            conversa.id, "in", "aqui ele, o nome dele e Thor", AGORA - timedelta(hours=4)
        )

        # Extração: chama o LLM e grava fato com evidência normalmente.
        extrator = Extrator(db, FakeLlm([
            _resposta_extracao(
                foto_pet_recebida=True,
                evidencias={"foto_pet_recebida": "aqui ele, o nome dele e Thor"},
            )
        ]))
        resultado = extrator.processar_conversa(conversa.id, agora=AGORA)
        self.assertEqual(resultado.mensagens_processadas, 3)
        self.assertTrue(db.fatos_da_conversa(conversa.id)["foto_pet_recebida"])

        # Regras: estágio e temperatura derivados normalmente (não travados
        # nem pulados por causa de `e_teste`).
        self.assertEqual(resultado.estado.estagio, "S2")

        # Rascunho: `drafts.gerar` roda e produz as duas opções.
        estado = recalcular(db, db.get_conversa(conversa.id), agora=AGORA)
        historico = [(m.direcao, m.texto) for m in db.listar_mensagens(conversa.id)]
        rascunho = gerar_rascunho(
            FakeLlm([json.dumps({"opcoes": [
                "Adorei o Thor!\nJá te mando a prévia.",
                "Que fofo o Thor!\nSegue a prévia.",
            ]})]),
            historico,
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            funil=conversa.funil,
            followups_enviados=0,
        )
        self.assertEqual(len(rascunho.opcoes), 2)

        # Resumo: `summaries.gerar` roda sobre a mesma conversa.
        contexto = ContextoResumo(
            funil=conversa.funil,
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            sinal=estado.classificacao.sinal,
            fatos=db.fatos_detalhados(conversa.id),
            eventos=db.eventos_da_conversa(conversa.id),
            objecoes=db.objecoes_da_conversa(conversa.id),
            correcoes=db.correcoes_da_conversa(conversa.id),
            followups=db.followups_da_conversa(conversa.id),
            historico=historico,
        )
        resumo_json = json.dumps({
            "resumo": "Marcos mandou a foto do Thor.",
            "proximo_passo": "Enviar a prévia.",
        })
        resumo = gerar_resumo(FakeLlm([resumo_json]), contexto)
        db.gravar_resumo(
            conversa.id,
            resumo=resumo.resumo,
            proximo_passo=resumo.proximo_passo,
            ultima_mensagem_id=None,
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            prompt_versao=PROMPT_VERSAO_RESUMO,
        )
        cache = db.resumo_vigente(conversa.id, PROMPT_VERSAO_RESUMO)
        self.assertIsNotNone(cache)
        self.assertEqual(cache.resumo, "Marcos mandou a foto do Thor.")

        # E continua de teste — o processamento não desmarcou nada.
        self.assertTrue(db.contatos[conversa.contato_id].e_teste)


class TesteCliMarcarTeste(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        contexto = patch.object(cli, "_db", return_value=self.db)
        contexto.start()
        self.addCleanup(contexto.stop)

    def _rodar(self, argv):
        parser = cli.build_parser()
        args = parser.parse_args(argv)
        return args.func(args)

    def test_marca_contato_como_teste(self):
        conversa = self.db.criar_conversa(nome="Ana")
        codigo = self._rodar(
            ["marcar-teste", str(conversa.contato_id), "--por", "operador"]
        )
        self.assertEqual(codigo, 0)
        self.assertTrue(self.db.contatos[conversa.contato_id].e_teste)
        self.assertEqual(len(self.db.correcoes), 1)
        self.assertEqual(self.db.correcoes[0]["campo"], "e_teste")

    def test_desfazer_desmarca(self):
        conversa = self.db.criar_conversa(nome="Ana", e_teste=True)
        codigo = self._rodar(
            ["marcar-teste", str(conversa.contato_id), "--desfazer", "--por", "operador"]
        )
        self.assertEqual(codigo, 0)
        self.assertFalse(self.db.contatos[conversa.contato_id].e_teste)

    def test_contato_inexistente_sai_com_erro(self):
        with self.assertRaises(SystemExit):
            self._rodar(["marcar-teste", "999", "--por", "operador"])


class TesteCliFilaIncluirTeste(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        contexto = patch.object(cli, "_db", return_value=self.db)
        contexto.start()
        self.addCleanup(contexto.stop)
        self.normal = self.db.criar_conversa(nome="Cliente Real")
        self.teste = self.db.criar_conversa(nome="Marcos Shiba", e_teste=True)
        for c in (self.normal, self.teste):
            self.db.registrar_mensagem(c.id, "in", "oi", AGORA - timedelta(hours=1))

    def _rodar(self, argv):
        parser = cli.build_parser()
        args = parser.parse_args(argv)
        return args.func(args)

    def test_padrao_exclui_teste_da_fila(self):
        with patch("sys.stdout"):
            self._rodar(["fila", "--simular"])
        # Só a conversa normal foi recalculada/considerada — a de teste nunca
        # apareceu na lista que `cmd_fila` usa como base.
        conversas = self.db.listar_conversas_abertas()
        ids = {c.id for c in conversas}
        self.assertIn(self.normal.id, ids)

    def test_somente_teste(self):
        with patch("sys.stdout"):
            codigo = self._rodar(["fila", "--simular", "--somente-teste"])
        self.assertEqual(codigo, 0)

    def test_incluir_e_somente_juntos_recusa(self):
        with self.assertRaises(SystemExit):
            self._rodar(["fila", "--simular", "--incluir-teste", "--somente-teste"])


class TestePainelApi(unittest.TestCase):
    """Requirement "Modo teste nunca mistura as duas visões na mesma tela":
    `apenas_teste` propagado até kanban/fila/conversas/métricas/"o que
    funciona", e o botão de marcar/desmarcar via `POST /conversas/{id}/teste`.
    """

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
        self.normal = self.fake.criar_conversa(funil="b2c", estagio="S0", nome="Cliente Real")
        self.teste = self.fake.criar_conversa(
            funil="b2c", estagio="S0", nome="Marcos Shiba", e_teste=True
        )
        # Mensagem inbound recente em ambos — sem isso nenhuma das duas entra
        # na fila (§6: QUENTE + bola com a Camu), e o teste de `apenas_teste`
        # não provaria nada sobre o filtro de teste.
        for c in (self.normal, self.teste):
            self.fake.registrar_mensagem(c.id, "in", "oi", AGORA)

    def test_kanban_padrao_exclui_teste(self):
        corpo = self.cliente.get("/api/kanban").json()
        nomes = {
            card["nome"]
            for kanban in corpo["kanbans"]
            for coluna in kanban["colunas"]
            for card in coluna["cards"]
        }
        self.assertIn("Cliente Real", nomes)
        self.assertNotIn("Marcos Shiba", nomes)

    def test_kanban_apenas_teste_mostra_so_teste(self):
        corpo = self.cliente.get("/api/kanban?apenas_teste=true").json()
        nomes = {
            card["nome"]
            for kanban in corpo["kanbans"]
            for coluna in kanban["colunas"]
            for card in coluna["cards"]
        }
        self.assertEqual(nomes, {"Marcos Shiba"})

    def test_conversas_apenas_teste(self):
        corpo = self.cliente.get("/api/conversas?apenas_teste=true").json()
        nomes = {c["nome"] for c in corpo["conversas"]}
        self.assertEqual(nomes, {"Marcos Shiba"})

    def test_fila_apenas_teste(self):
        corpo = self.cliente.get("/api/fila?apenas_teste=true").json()
        nomes = {i["nome"] for i in corpo["itens"]}
        self.assertEqual(nomes, {"Marcos Shiba"})

    def test_metricas_aceita_apenas_teste_sem_quebrar(self):
        resposta = self.cliente.get("/api/metricas?apenas_teste=true")
        self.assertEqual(resposta.status_code, 200)

    def test_o_que_funciona_aceita_apenas_teste_sem_quebrar(self):
        resposta = self.cliente.get("/api/o-que-funciona?apenas_teste=true")
        self.assertEqual(resposta.status_code, 200)

    def test_marcar_contato_de_teste_via_detalhe(self):
        resposta = self.cliente.post(
            f"/api/conversas/{self.normal.id}/teste",
            json={"e_teste": True, "por": "operador"},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(self.fake.contatos[self.normal.contato_id].e_teste)
        correcoes = [c for c in self.fake.correcoes if c["conversa_id"] == self.normal.id]
        self.assertEqual(len(correcoes), 1)
        self.assertEqual(correcoes[0]["campo"], "e_teste")

    def test_marcar_teste_conversa_inexistente_devolve_422(self):
        resposta = self.cliente.post(
            "/api/conversas/999999/teste", json={"e_teste": True}
        )
        self.assertEqual(resposta.status_code, 422)


if __name__ == "__main__":
    unittest.main()
