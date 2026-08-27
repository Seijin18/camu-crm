"""Resumo de conversa por LLM (change `resumo-conversa`) — terceira
superfície de LLM, ao lado de `extraction/` e `drafts.py`.

Ver docstring de `camucrm/summaries.py` para a divergência com §1/CLAUDE.md
e a propriedade que a sustenta (resumo é FOLHA do grafo). Este arquivo prova
essa propriedade de duas formas: por AST (importadores/imports fechados) e
por comportamento (`TesteResumoNaoMudaEstado`, espelhado em
`tests/test_e2e.py`).
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm.db import (  # noqa: E402
    CorrecaoRegistro,
    EventoRegistro,
    FatoRegistro,
    FollowupRegistro,
    ObjecaoRegistro,
)
from camucrm.llm import FakeLlm, LlmIndisponivelError  # noqa: E402
from camucrm.pipeline import recalcular  # noqa: E402
from camucrm.rules.fila import Candidato, montar_fila  # noqa: E402
from camucrm.summaries import (  # noqa: E402
    AVISO_BACKFILL,
    ContextoResumo,
    PROMPT_VERSAO_RESUMO,
    ResumoInvalidoError,
    gerar,
    user_prompt,
    validar_resumo,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CAMUCRM_DIR = REPO_ROOT / "camucrm"

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)

RESUMO_OK = json.dumps({
    "resumo": "Ana pediu peça personalizada e mandou a foto do pet Thor.\n"
              "A prévia foi enviada e ela ainda não respondeu.\n"
              "Nenhum follow-up foi enviado até agora.",
    "proximo_passo": "Enviar follow-up perguntando se ela viu a prévia.",
})


def _contexto_minimo(**overrides) -> ContextoResumo:
    base = dict(
        funil="b2c",
        estagio="S3",
        temperatura="morno",
        sinal="silêncio de 30h, bola com o cliente",
        fatos=[
            FatoRegistro(
                "foto_pet_recebida", True, "aqui ele, o nome dele e Thor",
                AGORA - timedelta(days=1), AGORA - timedelta(days=1),
            ),
        ],
        eventos=[
            EventoRegistro(None, "S1", AGORA - timedelta(days=2), "live", None),
            EventoRegistro("S1", "S2", AGORA - timedelta(days=1), "backfill", None),
        ],
        objecoes=[ObjecaoRegistro(1, "preco", "S3", "achei salgado", AGORA)],
        correcoes=[CorrecaoRegistro(1, "funil", "b2b", "b2c", AGORA, "marcos")],
        followups=[FollowupRegistro(1, "Oi, ainda por aí?", AGORA - timedelta(hours=12))],
        historico=[("in", "oi, vi o insta de voces"), ("out", "Oi! Me manda uma foto?")],
    )
    base.update(overrides)
    return ContextoResumo(**base)


class TesteMontagemDoPrompt(unittest.TestCase):
    def test_prompt_contem_evidencia_literal(self):
        prompt = user_prompt(_contexto_minimo())
        self.assertIn("aqui ele, o nome dele e Thor", prompt)

    def test_prompt_contem_linha_do_tempo_com_aviso_de_backfill(self):
        prompt = user_prompt(_contexto_minimo())
        self.assertIn("S1 → S2", prompt)
        self.assertIn(AVISO_BACKFILL, prompt)

    def test_prompt_contem_objecao_correcao_e_followup(self):
        prompt = user_prompt(_contexto_minimo())
        self.assertIn("achei salgado", prompt)
        self.assertIn("funil", prompt)
        self.assertIn("b2b", prompt)
        self.assertIn("Oi, ainda por aí?", prompt)

    def test_prompt_contem_ultimas_mensagens(self):
        prompt = user_prompt(_contexto_minimo())
        self.assertIn("CLIENTE: oi, vi o insta de voces", prompt)
        self.assertIn("CAMU: Oi! Me manda uma foto?", prompt)

    def test_prompt_so_usa_as_ultimas_30_mensagens(self):
        historico = [("in", f"mensagem {i}") for i in range(40)]
        prompt = user_prompt(_contexto_minimo(historico=historico))
        self.assertNotIn("mensagem 9\n", prompt)  # a 10ª mais antiga, fora da janela
        self.assertIn("mensagem 39", prompt)


class TesteValidarResumo(unittest.TestCase):
    def test_resumo_valido_passa(self):
        limpo, proximo = validar_resumo(
            "Ana mandou a foto do pet.", "Perguntar se ela viu a prévia."
        )
        self.assertEqual(limpo, "Ana mandou a foto do pet.")
        self.assertEqual(proximo, "Perguntar se ela viu a prévia.")

    def test_rejeita_token_de_estagio(self):
        with self.assertRaises(ResumoInvalidoError):
            validar_resumo("A conversa está em S3 agora.", "Aguardar resposta.")

    def test_rejeita_palavra_de_temperatura(self):
        with self.assertRaises(ResumoInvalidoError):
            validar_resumo("A conversa está MORNO.", "Aguardar resposta.")

    def test_rejeita_preco(self):
        with self.assertRaises(ResumoInvalidoError):
            validar_resumo("O valor de R$450 foi informado.", "Aguardar resposta.")

    def test_rejeita_resumo_com_mais_de_5_linhas(self):
        resumo = "\n".join(f"linha {i}" for i in range(6))
        with self.assertRaises(ResumoInvalidoError):
            validar_resumo(resumo, "Aguardar resposta.")

    def test_rejeita_proximo_passo_com_mais_de_1_linha(self):
        with self.assertRaises(ResumoInvalidoError):
            validar_resumo("Resumo ok.", "Linha 1.\nLinha 2.")

    def test_rejeita_resumo_vazio(self):
        with self.assertRaises(ResumoInvalidoError):
            validar_resumo("", "Aguardar resposta.")


class TesteGerar(unittest.TestCase):
    def test_gera_na_primeira_tentativa(self):
        llm = FakeLlm([RESUMO_OK])
        resumo = gerar(llm, _contexto_minimo())
        self.assertIn("Thor", resumo.resumo)
        self.assertEqual(len(llm.chamadas), 1)

    def test_retentativa_exatamente_uma_vez(self):
        """Primeira resposta viola (cita estágio); segunda é aceita. Duas
        chamadas ao LLM, não mais — mesma política de `drafts.gerar`."""
        recusado = json.dumps({
            "resumo": "A conversa está em S3.",
            "proximo_passo": "Aguardar.",
        })
        llm = FakeLlm([recusado, RESUMO_OK])
        resumo = gerar(llm, _contexto_minimo())
        self.assertIn("Thor", resumo.resumo)
        self.assertEqual(len(llm.chamadas), 2)
        self.assertIn("recusada", llm.chamadas[1][1])

    def test_duas_violacoes_seguidas_desiste(self):
        recusado = json.dumps({"resumo": "Está em S3.", "proximo_passo": "Aguardar."})
        llm = FakeLlm([recusado, recusado])
        with self.assertRaises(ResumoInvalidoError):
            gerar(llm, _contexto_minimo())
        self.assertEqual(len(llm.chamadas), 2)

    def test_llm_indisponivel_nao_derruba_vira_erro_tratavel(self):
        """`gerar` propaga como `ResumoInvalidoError` — quem chama
        (`camucrm.painel.api`) trata isso como bloco determinístico com
        `resumo: null`, nunca deixa a exceção subir crua (requirement
        "Falha de LLM não derruba a tela")."""

        class LlmQuebrado:
            nome = "quebrado"

            def completar(self, system, user, *, json_estrito=False):
                raise LlmIndisponivelError("sem chave configurada")

        with self.assertRaises(ResumoInvalidoError):
            gerar(LlmQuebrado(), _contexto_minimo())


class TesteGuardasDeArquitetura(unittest.TestCase):
    """Por `ast.parse`, não por grep — grep falso-positiva em docstring (que
    cita o nome do módulo em texto) e falso-negativa em import com alias."""

    def _nomes_importados(self, caminho: Path) -> set[str]:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        nomes: set[str] = set()
        for node in ast.walk(arvore):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    nomes.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    nomes.add(node.module)
                # `from .. import summaries` (import relativo de submódulo,
                # sem `module`) só aparece nos nomes importados, não em
                # `node.module` — sem isto o guard não pegaria esta forma.
                for alias in node.names:
                    nomes.add(alias.name)
        return nomes

    def test_rules_nunca_importa_llm_drafts_summaries_extraction(self):
        proibidos = ("llm", "drafts", "summaries", "extraction")
        arquivos = list((CAMUCRM_DIR / "rules").glob("*.py"))
        self.assertTrue(arquivos, "esperava pelo menos um arquivo em camucrm/rules/")
        for caminho in arquivos:
            with self.subTest(arquivo=caminho.name):
                nomes = self._nomes_importados(caminho)
                for proibido in proibidos:
                    for nome in nomes:
                        partes = nome.split(".")
                        self.assertNotIn(
                            proibido, partes,
                            f"{caminho.name} importa {nome!r} (contém {proibido!r})",
                        )

    def test_pipeline_metrics_ingest_nunca_importam_summaries_ou_mencionam_a_tabela(self):
        for nome_arquivo in ("pipeline.py", "metrics.py", "ingest.py"):
            caminho = CAMUCRM_DIR / nome_arquivo
            if not caminho.exists():
                continue
            with self.subTest(arquivo=nome_arquivo):
                nomes = self._nomes_importados(caminho)
                for nome in nomes:
                    self.assertNotIn(
                        "summaries", nome.split("."),
                        f"{nome_arquivo} importa {nome!r}",
                    )
                self.assertNotIn(
                    "resumos_conversa", caminho.read_text(encoding="utf-8"),
                    f"{nome_arquivo} menciona a tabela resumos_conversa",
                )

    def test_importadores_de_summaries_sao_um_conjunto_fechado(self):
        """{camucrm.painel.api, camucrm.cli} — um importador novo fora
        deste conjunto DEVE quebrar este teste (requirement "Importadores
        de summaries são um conjunto fechado")."""
        permitidos = {
            CAMUCRM_DIR / "painel" / "api.py",
            CAMUCRM_DIR / "cli.py",
        }
        importadores = []
        for caminho in CAMUCRM_DIR.rglob("*.py"):
            if caminho == CAMUCRM_DIR / "summaries.py":
                continue
            nomes = self._nomes_importados(caminho)
            for nome in nomes:
                if "summaries" in nome.split("."):
                    importadores.append(caminho)
                    break
        self.assertTrue(importadores, "esperava ao menos um importador de camucrm.summaries")
        extras = set(importadores) - permitidos
        self.assertFalse(
            extras,
            f"importador de camucrm.summaries fora do conjunto fechado: "
            f"{sorted(p.relative_to(REPO_ROOT).as_posix() for p in extras)}",
        )


class TesteResumoNaoMudaEstado(unittest.TestCase):
    """Requirement "Resumo é folha do grafo": roda o ciclo completo,
    congela `(estagio, temperatura, fila)`, gera um resumo, roda de novo,
    afirma igualdade exata. Espelhado em
    `tests/test_e2e.py::TesteResumoNaoMudaEstado` — este aqui é a versão
    mínima e rápida, focada só no módulo `summaries`.
    """

    def _estado_e_fila(self, db, conversa):
        estado = recalcular(db, conversa, agora=AGORA)
        candidato = Candidato(
            conversa_id=conversa.id, nome="Ana", funil=conversa.funil,
            estagio=estado.estagio, classificacao=estado.classificacao,
            sinais=estado.sinais,
        )
        fila = montar_fila([candidato])
        return (estado.estagio, estado.temperatura, [i.conversa_id for i in fila])

    def test_gerar_resumo_nao_altera_estagio_temperatura_ou_fila(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")
        db.registrar_mensagem(
            conversa.id, "in", "oi, vi o insta de voces", AGORA - timedelta(hours=6)
        )
        db.registrar_mensagem(
            conversa.id, "out", "Oi! Me manda uma foto do seu pet?",
            AGORA - timedelta(hours=5),
        )

        antes = self._estado_e_fila(db, conversa)

        estado = recalcular(db, conversa, agora=AGORA)
        contexto = ContextoResumo(
            funil=conversa.funil, estagio=estado.estagio, temperatura=estado.temperatura,
            sinal=estado.classificacao.sinal, fatos=db.fatos_detalhados(conversa.id),
            eventos=db.eventos_da_conversa(conversa.id),
            objecoes=db.objecoes_da_conversa(conversa.id),
            correcoes=db.correcoes_da_conversa(conversa.id),
            followups=db.followups_da_conversa(conversa.id),
            historico=[(m.direcao, m.texto) for m in db.listar_mensagens(conversa.id)],
        )
        resumo = gerar(FakeLlm([RESUMO_OK]), contexto)
        db.gravar_resumo(
            conversa.id, resumo=resumo.resumo, proximo_passo=resumo.proximo_passo,
            ultima_mensagem_id=None, estagio=estado.estagio, temperatura=estado.temperatura,
            prompt_versao=PROMPT_VERSAO_RESUMO,
        )

        depois = self._estado_e_fila(db, conversa)
        self.assertEqual(antes, depois)

    def test_apagar_resumos_conversa_nao_muda_estado(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")
        db.registrar_mensagem(conversa.id, "in", "oi", AGORA - timedelta(hours=1))
        estado = recalcular(db, conversa, agora=AGORA)
        db.gravar_resumo(
            conversa.id, resumo="x", proximo_passo="y", ultima_mensagem_id=None,
            estagio=estado.estagio, temperatura=estado.temperatura,
            prompt_versao=PROMPT_VERSAO_RESUMO,
        )
        antes = self._estado_e_fila(db, conversa)

        db.resumos.clear()  # "apagar a tabela inteira"

        depois = self._estado_e_fila(db, conversa)
        self.assertEqual(antes, depois)


if __name__ == "__main__":
    unittest.main()
