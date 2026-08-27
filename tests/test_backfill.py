"""Backfill (§8): estado final recuperado, tempo explicitamente não confiável."""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm.backfill import extrair_historico, importar_conversas  # noqa: E402
from camucrm.extraction.extractor import Extrator  # noqa: E402
from camucrm.llm import FakeLlm  # noqa: E402

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)

DUMP = [
    {
        "telefone": "5511999998888",
        "nome": "Ana",
        "tipo": "b2c",
        "mensagens": [
            {"direcao": "in", "texto": "oi", "enviada_em": "2026-07-01T10:00:00Z"},
            {"direcao": "out", "texto": "Oi! Manda uma foto do pet?",
             "enviada_em": "2026-07-01T10:20:00Z"},
            {"direcao": "in", "texto": "aqui esta a foto do Thor",
             "enviada_em": "2026-07-01T10:40:00Z"},
        ],
    }
]


def resposta(**campos):
    payload = {"objecao": None, "evidencias": {}}
    payload.update(campos)
    return json.dumps(payload)


DUMP_OBJECAO = [
    {
        "telefone": "5511999997777",
        "nome": "Bruno",
        "tipo": "b2c",
        "mensagens": [
            {"direcao": "in", "texto": "quero saber o preco",
             "enviada_em": "2026-07-01T10:00:00Z"},
            {"direcao": "out", "texto": "Ficou R$ 149 com frete gratis",
             "enviada_em": "2026-07-01T10:20:00Z"},
            {"direcao": "in", "texto": "achei caro",
             "enviada_em": "2026-07-01T10:40:00Z"},
        ],
    }
]


def _resposta_com_objecao():
    return json.dumps({"objecao": "preco", "evidencias": {"objecao": "achei caro"}})


class TesteImportacao(unittest.TestCase):
    def test_importa_contato_conversa_e_mensagens(self):
        db = FakeDatabase()
        resumo = importar_conversas(db, DUMP)
        self.assertEqual(resumo.conversas, 1)
        self.assertEqual(resumo.mensagens, 3)

    def test_registro_sem_telefone_e_ignorado(self):
        db = FakeDatabase()
        resumo = importar_conversas(db, [{"nome": "sem telefone", "mensagens": []}])
        self.assertEqual(resumo.conversas, 0)

    def test_timestamp_iso_com_z_e_aceito(self):
        db = FakeDatabase()
        importar_conversas(db, DUMP)
        conversa = next(iter(db.conversas.values()))
        primeira = db.listar_mensagens(conversa.id)[0]
        self.assertEqual(primeira.enviada_em.tzinfo, timezone.utc)

    def test_reimportar_mesmo_dump_sem_externa_id_nao_duplica_mensagem(self):
        """Change `backfill-seguro-para-reexecucao`, §8: o próprio dump de
        `DUMP` não traz `externa_id` nas mensagens (caso comum de um dump
        externo sem os ids do WhatsApp) — sem um `externa_id` SINTÉTICO
        estável, a segunda importação duplicaria as 3 mensagens."""
        db = FakeDatabase()
        primeiro = importar_conversas(db, DUMP)
        self.assertEqual(primeiro.mensagens, 3)

        segundo = importar_conversas(db, DUMP)
        self.assertEqual(segundo.mensagens, 0)

        conversa = next(iter(db.conversas.values()))
        self.assertEqual(len(db.listar_mensagens(conversa.id)), 3)


class TesteOrigemBackfill(unittest.TestCase):
    def test_eventos_ficam_marcados_como_backfill(self):
        """§8: sem isso, a média de duração por estágio vira ficção."""
        db = FakeDatabase()
        importar_conversas(db, DUMP)
        extrator = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui esta a foto do Thor"})
        ]))
        resumo, resultados = extrair_historico(db, extrator, agora=AGORA)

        self.assertEqual(resumo.extraidas, 1)
        self.assertTrue(db.eventos)
        self.assertTrue(all(e["origem"] == "backfill" for e in db.eventos))
        # Conversa de julho, backfill em agosto: o estado final é SX (14 dias
        # sem resposta), e isso está certo.
        self.assertEqual(resultados[0].estado.estagio, "SX")

    def test_conversao_e_recuperada_mesmo_sem_tempo_confiavel(self):
        """Métricas de conversão podem usar backfill; as de tempo, não (§8)."""
        db = FakeDatabase()
        importar_conversas(db, DUMP)
        extrator = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui esta a foto do Thor"})
        ]))
        extrair_historico(db, extrator, agora=AGORA)
        alcancados = {e["para"] for e in db.eventos}
        # A trilha preserva que a conversa chegou em S2 antes de esfriar — sem
        # isso, a conversão S1→S2 do histórico nasceria zerada.
        self.assertIn("S1", alcancados)
        self.assertIn("S2", alcancados)
        self.assertIn("SX", alcancados)
        # E `ultimo_avanco_em` ignora backfill, então nada fica "quente" por
        # ter acabado de ser importado.
        conversa = next(iter(db.conversas.values()))
        self.assertIsNone(db.ultimo_avanco_em(conversa.id))


class TesteReprocessamentoIdempotente(unittest.TestCase):
    """§2 (change `literalidade-e-idempotencia-da-extracao`): o cenário
    concreto que motivou o achado — `forcar=True` sempre relê a conversa
    inteira (`desde=None`), então rodar `extrair_historico`/`make backfill
    --forcar` duas vezes reapresenta a mesma objeção ao LLM. Sem o índice
    único de `objecoes` (`gravar_objecao` com `ON CONFLICT DO NOTHING`), a
    segunda rodada duplicaria a linha e poluiria `distribuicao_objecoes`
    (§4) permanentemente.
    """

    def test_forcar_duas_vezes_nao_muda_contagem_de_objecoes(self):
        db = FakeDatabase()
        importar_conversas(db, DUMP_OBJECAO)
        extrator = Extrator(
            db, FakeLlm([_resposta_com_objecao(), _resposta_com_objecao()])
        )

        extrair_historico(db, extrator, agora=AGORA)
        self.assertEqual(len(db.objecoes), 1)

        # Segunda rodada de `--forcar`: mesma conversa, mesma objeção.
        extrair_historico(db, extrator, agora=AGORA)
        self.assertEqual(len(db.objecoes), 1)

    def test_processar_conversa_forcar_duas_vezes_nao_duplica_objecao(self):
        """Mesmo cenário, direto em `Extrator.processar_conversa` — sem
        passar pelo `backfill.extrair_historico` — para não deixar a garantia
        acoplada só ao caminho de backfill."""
        db = FakeDatabase()
        importar_conversas(db, DUMP_OBJECAO)
        conversa = next(iter(db.conversas.values()))
        extrator = Extrator(
            db, FakeLlm([_resposta_com_objecao(), _resposta_com_objecao()])
        )

        extrator.processar_conversa(conversa.id, agora=AGORA, forcar=True)
        self.assertEqual(len(db.objecoes), 1)

        extrator.processar_conversa(conversa.id, agora=AGORA, forcar=True)
        self.assertEqual(len(db.objecoes), 1)


class TesteChunkingDeHistoricoGrande(unittest.TestCase):
    """Change `backfill-seguro-para-reexecucao`, §8: uma conversa longa não
    pode virar uma única chamada de LLM — estoura contexto (extração vazia,
    silenciosa) ou degrada recall."""

    def test_historico_de_1000_mais_mensagens_e_processado_em_blocos(self):
        from camucrm.extraction.extractor import TAMANHO_MAXIMO_BLOCO

        total = TAMANHO_MAXIMO_BLOCO * 5 + 50  # "1000+", não múltiplo exato
        mensagens = [
            {
                "direcao": "in" if i % 2 == 0 else "out",
                "texto": f"mensagem numero {i}",
                "enviada_em": (
                    datetime(2026, 7, 1, tzinfo=timezone.utc)
                    + timedelta(minutes=i)
                ).isoformat(),
            }
            for i in range(total)
        ]
        dump = [
            {
                "telefone": "5511999993333",
                "nome": "Diana",
                "tipo": "b2c",
                "mensagens": mensagens,
            }
        ]

        db = FakeDatabase()
        importar_conversas(db, dump)
        conversa = next(iter(db.conversas.values()))

        llm = FakeLlm([])
        extrator = Extrator(db, llm)
        resultado = extrator.processar_conversa(conversa.id, agora=AGORA, forcar=True)

        esperado_blocos = -(-total // TAMANHO_MAXIMO_BLOCO)  # divisão inteira p/ cima
        self.assertGreater(esperado_blocos, 1)
        self.assertEqual(len(llm.chamadas), esperado_blocos)

        total_por_chamada = 0
        for _, user in llm.chamadas:
            contadas = user.count("CLIENTE:") + user.count("CAMU:")
            self.assertLessEqual(contadas, TAMANHO_MAXIMO_BLOCO)
            total_por_chamada += contadas
        self.assertEqual(total_por_chamada, total)
        self.assertEqual(resultado.mensagens_processadas, total)


class TesteOrdemDeLeituraBateComEnviadaEm(unittest.TestCase):
    """Change `backfill-seguro-para-reexecucao`, §8: `id` de inserção e
    `enviada_em` podem divergir num dump não estritamente ordenado — a
    extração precisa ler pela ordem CRONOLÓGICA real, não pela ordem em que
    as linhas entraram no banco."""

    def test_mensagens_inseridas_fora_de_ordem_sao_lidas_cronologicamente(self):
        db = FakeDatabase()
        contato = db.upsert_contato("5511999992222", nome="Elis", tipo="b2c")
        conversa = db.get_or_create_conversa(contato.id, funil="b2c")

        base = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        # Inseridas fora de ordem cronológica: a primeira linha gravada
        # (menor `id`) é a ÚLTIMA mensagem real; a ordem de `enviada_em` é o
        # inverso da ordem de inserção.
        db.registrar_mensagem(conversa.id, "in", "terceira", base + timedelta(minutes=20))
        db.registrar_mensagem(conversa.id, "in", "primeira", base)
        db.registrar_mensagem(conversa.id, "in", "segunda", base + timedelta(minutes=10))

        llm = FakeLlm([])
        extrator = Extrator(db, llm)
        extrator.processar_conversa(conversa.id, agora=AGORA)

        self.assertEqual(len(llm.chamadas), 1)
        _, user = llm.chamadas[0]
        posicao = {
            texto: user.index(texto) for texto in ("primeira", "segunda", "terceira")
        }
        self.assertLess(posicao["primeira"], posicao["segunda"])
        self.assertLess(posicao["segunda"], posicao["terceira"])


class TesteCoberturaPorVersaoDePrompt(unittest.TestCase):
    """Change `backfill-cobertura-por-prompt`: `extrair_historico`
    (`somente_desatualizados=True` por padrão) só relê de fato o que a
    versão de prompt ATUAL ainda não cobriu."""

    def test_segunda_execucao_sob_a_mesma_versao_nao_chama_llm(self):
        db = FakeDatabase()
        importar_conversas(db, DUMP)
        extrator = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui esta a foto do Thor"})
        ]))

        resumo1, _ = extrair_historico(db, extrator, agora=AGORA)
        self.assertEqual(resumo1.extraidas, 1)
        self.assertEqual(len(extrator.llm.chamadas), 1)

        resumo2, _ = extrair_historico(db, extrator, agora=AGORA)
        self.assertEqual(resumo2.extraidas, 1)  # ainda "extraída" (sem erro)
        self.assertEqual(len(extrator.llm.chamadas), 1)  # nenhuma chamada nova

    def test_bump_de_prompt_versao_forca_releitura_total(self):
        import camucrm.extraction.prompt as prompt_mod

        db = FakeDatabase()
        importar_conversas(db, DUMP)
        extrator = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui esta a foto do Thor"})
        ]))
        extrair_historico(db, extrator, agora=AGORA)
        self.assertEqual(len(extrator.llm.chamadas), 1)

        versao_original = prompt_mod.PROMPT_VERSAO
        try:
            prompt_mod.PROMPT_VERSAO = "2"
            extrator.llm.respostas.append(resposta(
                foto_pet_recebida=True,
                evidencias={"foto_pet_recebida": "aqui esta a foto do Thor"},
            ))
            extrair_historico(db, extrator, agora=AGORA)
            # A versão "2" nunca tinha tocado esta conversa: releitura total,
            # uma chamada de LLM a mais.
            self.assertEqual(len(extrator.llm.chamadas), 2)
        finally:
            prompt_mod.PROMPT_VERSAO = versao_original

    def test_forcar_tudo_ignora_cobertura_existente(self):
        db = FakeDatabase()
        importar_conversas(db, DUMP)
        extrator = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui esta a foto do Thor"}),
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui esta a foto do Thor"}),
        ]))
        extrair_historico(db, extrator, agora=AGORA)
        self.assertEqual(len(extrator.llm.chamadas), 1)

        # `somente_desatualizados=False` (CLI: `--forcar-tudo`): relê mesmo
        # com cobertura completa para a versão atual.
        extrair_historico(db, extrator, agora=AGORA, somente_desatualizados=False)
        self.assertEqual(len(extrator.llm.chamadas), 2)

    def test_objecao_nao_duplica_com_bump_de_versao_e_reexecucao(self):
        """Regressão sobre `backfill-seguro-para-reexecucao`: bump de versão
        + reexecução continua sem duplicar objeção, agora também no caminho
        que passa a reler (versão nova) e no que passa a pular (mesma
        versão, segunda vez)."""
        import camucrm.extraction.prompt as prompt_mod

        db = FakeDatabase()
        importar_conversas(db, DUMP_OBJECAO)
        extrator = Extrator(
            db, FakeLlm([_resposta_com_objecao(), _resposta_com_objecao()])
        )
        extrair_historico(db, extrator, agora=AGORA)
        self.assertEqual(len(db.objecoes), 1)

        # Mesma versão, segunda execução: pula, sem chance de duplicar.
        extrair_historico(db, extrator, agora=AGORA)
        self.assertEqual(len(db.objecoes), 1)

        versao_original = prompt_mod.PROMPT_VERSAO
        try:
            prompt_mod.PROMPT_VERSAO = "2"
            extrair_historico(db, extrator, agora=AGORA)
            # Versão nova relê tudo, inclusive a objeção — mesmo texto,
            # mesmo estágio de referência (releitura do zero), então o
            # `ON CONFLICT` de `objecoes_dedupe_idx` ainda a reconhece.
            self.assertEqual(len(db.objecoes), 1)
        finally:
            prompt_mod.PROMPT_VERSAO = versao_original


if __name__ == "__main__":
    unittest.main()
