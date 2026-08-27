"""Ground truth pelo painel (§7, change `ground-truth-no-painel`).

Toda leitura/escrita de dataset nestes testes usa `CAMU_EVAL_DATASET`
apontando para um arquivo dentro de um diretório temporário — nenhum teste
aqui toca `data/eval/conversas.jsonl` real (requirement "Testes nunca tocam
o dataset real"). O cache de `POST /eval/rodar` é sempre irmão do dataset
(`config.eval_dataset_caminho().parent / "ultimo_resultado.json"`), então
fica isolado no mesmo diretório temporário automaticamente.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from camucrm import config
from camucrm.evaluation.dataset import TAMANHO_MINIMO
from camucrm.llm import FakeLlm
from camucrm.painel import api, server
from tests.fakes import FakeDatabase


def _mensagem(direcao: str, texto: str, quando: str) -> dict:
    return {"direcao": direcao, "texto": texto, "enviada_em": quando}


def _entrada(
    id_: str,
    *,
    funil: str = "b2c",
    estagio_final: str = "S1",
    objecao: str | None = None,
    fatos: dict | None = None,
    marcos: list | None = None,
    nota: str | None = None,
    mensagens: list | None = None,
) -> dict:
    return {
        "id": id_,
        "funil": funil,
        "mensagens": mensagens
        or [
            _mensagem("in", "oi, vi o insta de voces", "2026-07-01T10:00:00Z"),
            _mensagem("out", "Oi! tudo bem?", "2026-07-01T10:05:00Z"),
        ],
        "rotulo": {
            "estagio_final": estagio_final,
            "objecao": objecao,
            "fatos": fatos or {},
            "marcos": marcos or [],
        },
        "nota": nota,
    }


def _escrever_dataset(caminho: Path, entradas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conteudo = "\n".join(json.dumps(e) for e in entradas)
    if conteudo:
        conteudo += "\n"
    caminho.write_text(conteudo, encoding="utf-8")


class BaseTesteEvalPainel(unittest.TestCase):
    """`CAMU_EVAL_DATASET` sempre aponta para dentro de `self.tmpdir` —
    nunca para `data/eval/conversas.jsonl`."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.caminho_dataset = self.tmpdir / "conversas.jsonl"

        contexto = patch.dict(
            os.environ, {config.ENV_EVAL_DATASET: str(self.caminho_dataset)}, clear=False
        )
        contexto.start()
        self.addCleanup(contexto.stop)
        os.environ.pop(server.ENV_TOKEN, None)

        self.cliente = TestClient(server.app)
        self.fake = FakeDatabase()
        patcher = patch.object(server, "get_db", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)


class TesteConfigEvalDataset(unittest.TestCase):
    def test_env_var_sobrescreve_caminho_padrao(self):
        with patch.dict(os.environ, {config.ENV_EVAL_DATASET: "/tmp/qualquer.jsonl"}):
            self.assertEqual(str(config.eval_dataset_caminho()), "/tmp/qualquer.jsonl")

    def test_sem_env_var_usa_caminho_padrao(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(config.ENV_EVAL_DATASET, None)
            self.assertEqual(str(config.eval_dataset_caminho()), config.EVAL_DATASET_PADRAO)


class TesteStatusEval(BaseTesteEvalPainel):
    """Requirement 'Status do dataset reflete completude real'."""

    def test_dataset_ausente_reporta_vazio_e_incompleto(self):
        resposta = self.cliente.get("/api/eval/status")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["total"], 0)
        self.assertFalse(corpo["completo"])

    def test_abaixo_do_minimo_reporta_incompleto(self):
        entradas = [_entrada(f"e{i}") for i in range(5)]
        _escrever_dataset(self.caminho_dataset, entradas)
        resposta = self.cliente.get("/api/eval/status")
        corpo = resposta.json()
        self.assertEqual(corpo["total"], 5)
        self.assertFalse(corpo["completo"])
        self.assertEqual(len(corpo["entradas"]), 5)

    def test_no_minimo_reporta_completo(self):
        entradas = [_entrada(f"e{i}") for i in range(TAMANHO_MINIMO)]
        _escrever_dataset(self.caminho_dataset, entradas)
        resposta = self.cliente.get("/api/eval/status")
        corpo = resposta.json()
        self.assertEqual(corpo["total"], TAMANHO_MINIMO)
        self.assertTrue(corpo["completo"])


class TesteCriarEntradaAPartirDeConversaReal(BaseTesteEvalPainel):
    """Requirement 'Criar entrada a partir de conversa real puxa as mensagens'."""

    def test_conversa_id_preenche_mensagens_reais(self):
        conversa = self.fake.criar_conversa(funil="b2c", estagio="S2", nome="Ana")
        self.fake.registrar_mensagem(conversa.id, "in", "oi, vi o insta de voces")
        self.fake.registrar_mensagem(conversa.id, "out", "Oi! Me manda uma foto do seu pet")
        self.fake.registrar_mensagem(conversa.id, "in", "aqui esta ele")

        resposta = self.cliente.post(
            "/api/eval/rotulos",
            json={
                "conversa_id": conversa.id,
                "rotulo": {
                    "estagio_final": "S2",
                    "objecao": None,
                    "fatos": {"foto_pet_recebida": True},
                    "marcos": [],
                },
                "nota": "puxada do CRM",
            },
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        entrada = corpo["entrada"]
        self.assertEqual(entrada["funil"], "b2c")
        textos_reais = [m.texto for m in self.fake.listar_mensagens_registradas(conversa_id=conversa.id)]
        textos_gravados = [m["texto"] for m in entrada["mensagens"]]
        self.assertEqual(textos_gravados, textos_reais)
        self.assertEqual(len(entrada["mensagens"]), 3)

        # gravado no arquivo, não só na resposta
        status = self.cliente.get("/api/eval/status").json()
        self.assertEqual(status["total"], 1)

    def test_conversa_inexistente_recusa_422(self):
        resposta = self.cliente.post(
            "/api/eval/rotulos",
            json={"conversa_id": 999999, "rotulo": {"estagio_final": "S1"}},
        )
        self.assertEqual(resposta.status_code, 422)

    def test_sem_conversa_id_e_sem_mensagens_recusa_422(self):
        resposta = self.cliente.post(
            "/api/eval/rotulos", json={"rotulo": {"estagio_final": "S1"}}
        )
        self.assertEqual(resposta.status_code, 422)

    def test_mensagens_digitadas_funcionam_como_fallback(self):
        resposta = self.cliente.post(
            "/api/eval/rotulos",
            json={
                "funil": "b2b",
                "mensagens": [
                    _mensagem("out", "Oi, aqui e a Camu", "2026-07-05T11:00:00Z"),
                    _mensagem("in", "pode mandar sim", "2026-07-05T13:20:00Z"),
                ],
                "rotulo": {"estagio_final": "P3", "objecao": None, "fatos": {}, "marcos": []},
            },
        )
        self.assertEqual(resposta.status_code, 200)
        entrada = resposta.json()["entrada"]
        self.assertEqual(len(entrada["mensagens"]), 2)
        self.assertEqual(entrada["funil"], "b2b")


class TesteValidacaoUnicaFonteDeVerdade(BaseTesteEvalPainel):
    """Requirement 'Validação de rótulo tem um único lugar de verdade' —
    a rota do painel rejeita com o mesmo tipo de erro que `dataset.carregar`
    rejeitaria para a mesma entrada malformada."""

    def test_estagio_fora_da_taxonomia_e_recusado_422(self):
        resposta = self.cliente.post(
            "/api/eval/rotulos",
            json={
                "mensagens": [_mensagem("in", "oi", "2026-07-01T10:00:00Z")],
                "rotulo": {"estagio_final": "S9", "objecao": None, "fatos": {}, "marcos": []},
            },
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("fora da taxonomia", resposta.json()["erro"])

    def test_objecao_fora_da_lista_e_recusada_422(self):
        resposta = self.cliente.post(
            "/api/eval/rotulos",
            json={
                "mensagens": [_mensagem("in", "oi", "2026-07-01T10:00:00Z")],
                "rotulo": {
                    "estagio_final": "S1",
                    "objecao": "muito caro",
                    "fatos": {},
                    "marcos": [],
                },
            },
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("objeção", resposta.json()["erro"])

    def test_fato_fora_do_contrato_e_recusado_422(self):
        resposta = self.cliente.post(
            "/api/eval/rotulos",
            json={
                "mensagens": [_mensagem("in", "oi", "2026-07-01T10:00:00Z")],
                "rotulo": {
                    "estagio_final": "S1",
                    "objecao": None,
                    "fatos": {"cliente_simpatico": True},
                    "marcos": [],
                },
            },
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("fora do contrato", resposta.json()["erro"])

    def test_entrada_invalida_nao_e_gravada(self):
        """Requirement 'Entrada malformada nunca corrompe o arquivo'."""
        resposta = self.cliente.post(
            "/api/eval/rotulos",
            json={
                "mensagens": [_mensagem("in", "oi", "2026-07-01T10:00:00Z")],
                "rotulo": {"estagio_final": "S9", "objecao": None, "fatos": {}, "marcos": []},
            },
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertFalse(self.caminho_dataset.exists())


class TesteEditarEExcluir(BaseTesteEvalPainel):
    """Requirement 'Detalhe de entrada é editável'."""

    def _criar(self, id_: str = "e1") -> dict:
        resposta = self.cliente.post(
            "/api/eval/rotulos",
            json={
                "id": id_,
                "mensagens": [_mensagem("in", "oi", "2026-07-01T10:00:00Z")],
                "rotulo": {"estagio_final": "S1", "objecao": None, "fatos": {}, "marcos": []},
                "nota": "original",
            },
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta.json()["entrada"]

    def test_editar_preserva_id(self):
        self._criar("e1")
        resposta = self.cliente.put(
            "/api/eval/rotulos/e1",
            json={
                "rotulo": {
                    "estagio_final": "S3",
                    "objecao": "preco",
                    "fatos": {"previa_enviada": True},
                    "marcos": [],
                },
                "nota": "revisado",
            },
        )
        self.assertEqual(resposta.status_code, 200)
        entrada = resposta.json()["entrada"]
        self.assertEqual(entrada["id"], "e1")
        self.assertEqual(entrada["rotulo"]["estagio_final"], "S3")
        self.assertEqual(entrada["rotulo"]["objecao"], "preco")
        self.assertEqual(entrada["nota"], "revisado")
        # mensagens originais preservadas — PUT sem `mensagens`/`conversa_id`
        self.assertEqual(len(entrada["mensagens"]), 1)

        detalhe = self.cliente.get("/api/eval/rotulos/e1").json()
        self.assertEqual(detalhe["id"], "e1")
        self.assertEqual(detalhe["rotulo"]["estagio_final"], "S3")

    def test_editar_entrada_inexistente_422(self):
        resposta = self.cliente.put(
            "/api/eval/rotulos/nao-existe",
            json={"rotulo": {"estagio_final": "S1", "objecao": None, "fatos": {}, "marcos": []}},
        )
        self.assertEqual(resposta.status_code, 422)

    def test_editar_com_rotulo_invalido_recusa_e_preserva_original(self):
        self._criar("e1")
        resposta = self.cliente.put(
            "/api/eval/rotulos/e1",
            json={"rotulo": {"estagio_final": "S9", "objecao": None, "fatos": {}, "marcos": []}},
        )
        self.assertEqual(resposta.status_code, 422)
        detalhe = self.cliente.get("/api/eval/rotulos/e1").json()
        self.assertEqual(detalhe["rotulo"]["estagio_final"], "S1")

    def test_excluir_remove_a_entrada(self):
        self._criar("e1")
        self._criar("e2")
        resposta = self.cliente.delete("/api/eval/rotulos/e1")
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["ok"])

        status = self.cliente.get("/api/eval/status").json()
        self.assertEqual(status["total"], 1)
        ids = {e["id"] for e in status["entradas"]}
        self.assertEqual(ids, {"e2"})

    def test_excluir_entrada_inexistente_422(self):
        resposta = self.cliente.delete("/api/eval/rotulos/nao-existe")
        self.assertEqual(resposta.status_code, 422)


class TesteRodarEvalAbaixoDoMinimo(BaseTesteEvalPainel):
    """Requirement 'Rodar eval abaixo do tamanho mínimo é estruturalmente
    recusado' — teste estrutural, não só de UI."""

    def test_recusa_com_422_e_nao_chama_llm(self):
        entradas = [_entrada(f"e{i}") for i in range(TAMANHO_MINIMO - 1)]
        _escrever_dataset(self.caminho_dataset, entradas)

        llm = FakeLlm()
        with patch.object(api, "criar_llm", return_value=llm) as criar_llm_mock:
            resposta = self.cliente.post("/api/eval/rodar", json={})

        self.assertEqual(resposta.status_code, 422)
        criar_llm_mock.assert_not_called()
        self.assertEqual(llm.chamadas, [])
        self.assertFalse(self._caminho_resultado().exists())

    def _caminho_resultado(self) -> Path:
        return self.caminho_dataset.parent / "ultimo_resultado.json"

    def test_dataset_vazio_recusa_com_422(self):
        resposta = self.cliente.post("/api/eval/rodar", json={})
        self.assertEqual(resposta.status_code, 422)


class TesteEvalPontaAPonta(BaseTesteEvalPainel):
    """Requirement 'Tela /o-que-funciona só afirma acurácia com eval
    disponível' — dataset fake de 30 entradas roda de ponta a ponta."""

    def test_resultado_indisponivel_antes_de_rodar(self):
        resposta = self.cliente.get("/api/eval/resultado")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"disponivel": False})

    def test_o_que_funciona_sem_cache_mostra_indisponivel(self):
        resposta = self.cliente.get("/api/o-que-funciona")
        corpo = resposta.json()
        self.assertEqual(corpo["acuracia_extracao"], {"disponivel": False})

    def test_dataset_de_30_roda_e_popula_bloco_novo(self):
        entradas = [_entrada(f"e{i}") for i in range(TAMANHO_MINIMO)]
        _escrever_dataset(self.caminho_dataset, entradas)

        llm = FakeLlm()  # sem fila de respostas: devolve `{}` em todo `completar`
        with patch.object(api, "criar_llm", return_value=llm):
            resposta = self.cliente.post("/api/eval/rodar", json={})

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["disponivel"])
        self.assertEqual(corpo["n_conversas"], TAMANHO_MINIMO)
        self.assertEqual(len(llm.chamadas), TAMANHO_MINIMO)

        # cache persistido em arquivo, irmão do dataset
        caminho_resultado = self.caminho_dataset.parent / "ultimo_resultado.json"
        self.assertTrue(caminho_resultado.exists())
        cache_em_disco = json.loads(caminho_resultado.read_text(encoding="utf-8"))
        self.assertEqual(cache_em_disco["n_conversas"], TAMANHO_MINIMO)
        # nenhum texto de mensagem no cache (design.md)
        self.assertNotIn("oi, vi o insta de voces", json.dumps(cache_em_disco))

        # GET /eval/resultado lê o mesmo cache
        resultado = self.cliente.get("/api/eval/resultado").json()
        self.assertTrue(resultado["disponivel"])
        self.assertEqual(resultado["n_conversas"], TAMANHO_MINIMO)

        # e o bloco novo de /o-que-funciona aparece populado
        funciona = self.cliente.get("/api/o-que-funciona").json()
        acuracia = funciona["acuracia_extracao"]
        self.assertTrue(acuracia["disponivel"])
        self.assertEqual(acuracia["n_conversas"], TAMANHO_MINIMO)
        self.assertIn("meta_fatos", acuracia)
        self.assertIn("meta_objecao", acuracia)
        self.assertIn("meta_falsos_positivos", acuracia)


if __name__ == "__main__":
    unittest.main()
