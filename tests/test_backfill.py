"""Backfill (§8): estado final recuperado, tempo explicitamente não confiável."""

import json
import sys
import unittest
from datetime import datetime, timezone
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


if __name__ == "__main__":
    unittest.main()
