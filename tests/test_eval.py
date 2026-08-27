"""Eval (§7): as metas, e a trava de falso positivo de avanço."""

import json
import tempfile
import unittest
from pathlib import Path

from camucrm.evaluation import carregar, rodar
from camucrm.evaluation.dataset import DatasetInvalidoError
from camucrm.evaluation.runner import META_FATOS, ResultadoConversa
from camucrm.llm import FakeLlm

CONVERSA = {
    "id": "t1",
    "funil": "b2c",
    "mensagens": [
        {"direcao": "in", "texto": "oi", "enviada_em": "2026-07-01T10:00:00Z"},
        {"direcao": "in", "texto": "aqui esta a foto do Thor",
         "enviada_em": "2026-07-01T10:40:00Z"},
    ],
    "rotulo": {
        "estagio_final": "S2",
        "objecao": None,
        "fatos": {"foto_pet_recebida": True},
    },
}


def escrever(linhas) -> Path:
    caminho = Path(tempfile.mkdtemp()) / "dataset.jsonl"
    caminho.write_text(
        "\n".join(json.dumps(linha) for linha in linhas), encoding="utf-8"
    )
    return caminho


def resposta(**campos):
    payload = {"objecao": None, "evidencias": {}}
    payload.update(campos)
    return json.dumps(payload)


class TesteCarregamento(unittest.TestCase):
    def test_carrega_conversa_rotulada(self):
        conversas = carregar(escrever([CONVERSA]))
        self.assertEqual(conversas[0].estagio_final, "S2")
        self.assertTrue(conversas[0].fatos["foto_pet_recebida"])

    def test_estagio_fora_da_taxonomia_falha_alto(self):
        ruim = json.loads(json.dumps(CONVERSA))
        ruim["rotulo"]["estagio_final"] = "S9"
        with self.assertRaises(DatasetInvalidoError):
            carregar(escrever([ruim]))

    def test_objecao_fora_da_taxonomia_falha_alto(self):
        ruim = json.loads(json.dumps(CONVERSA))
        ruim["rotulo"]["objecao"] = "muito caro"
        with self.assertRaises(DatasetInvalidoError):
            carregar(escrever([ruim]))

    def test_fato_fora_do_contrato_falha_alto(self):
        ruim = json.loads(json.dumps(CONVERSA))
        ruim["rotulo"]["fatos"]["cliente_simpatico"] = True
        with self.assertRaises(DatasetInvalidoError):
            carregar(escrever([ruim]))

    def test_comentarios_sao_ignorados(self):
        caminho = escrever([CONVERSA])
        caminho.write_text(
            "// comentário\n\n" + caminho.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.assertEqual(len(carregar(caminho)), 1)


class TesteTravaDeFalsoPositivo(unittest.TestCase):
    def test_derivar_acima_do_rotulo_e_falso_positivo(self):
        r = ResultadoConversa("x", 7, 7, None, None, "S2", "S4")
        self.assertTrue(r.falso_positivo_estagio)

    def test_derivar_abaixo_do_rotulo_nao_e(self):
        r = ResultadoConversa("x", 7, 7, None, None, "S4", "S2")
        self.assertFalse(r.falso_positivo_estagio)

    def test_terminal_nao_conta_como_avanco(self):
        """SX não é "mais avançado" que S2 — é saída do funil."""
        r = ResultadoConversa("x", 7, 7, None, None, "S2", "SX")
        self.assertFalse(r.falso_positivo_estagio)

    def test_um_falso_positivo_reprova_mesmo_com_fatos_perfeitos(self):
        # `previa_enviada` exige evidência do lado da Camu (§2, direção da
        # evidência) — por isso esta conversa, diferente da `CONVERSA`
        # global, precisa de uma mensagem `out` real para o modelo poder
        # citar. O trecho é literal E do lado certo, mas semanticamente não é
        # uma prévia — é isso que o contrato (literalidade + direção) não
        # pega, e o eval, comparando com o rótulo, precisa reprovar.
        conversa_com_out = json.loads(json.dumps(CONVERSA))
        conversa_com_out["mensagens"].append(
            {"direcao": "out", "texto": "Aqui esta o numero do seu pedido: 482",
             "enviada_em": "2026-07-01T10:41:00Z"}
        )
        conversas = carregar(escrever([conversa_com_out]))
        # O modelo inventa `previa_enviada` com um trecho que existe (literal
        # e do lado certo): o contrato aceita, a regra avança, e o eval
        # precisa reprovar.
        llm = FakeLlm([
            resposta(
                foto_pet_recebida=True,
                previa_enviada=True,
                evidencias={
                    "foto_pet_recebida": "aqui esta a foto do Thor",
                    "previa_enviada": "aqui esta o numero do seu pedido: 482",
                },
            )
        ])
        relatorio = rodar(llm, conversas)
        self.assertEqual(len(relatorio.falsos_positivos), 1)
        self.assertFalse(relatorio.aprovado)


class TesteMetas(unittest.TestCase):
    def test_extracao_correta_aprova(self):
        conversas = carregar(escrever([CONVERSA]))
        llm = FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui esta a foto do Thor"})
        ])
        relatorio = rodar(llm, conversas)
        self.assertEqual(relatorio.concordancia_fatos, 1.0)
        self.assertTrue(relatorio.aprovado)

    def test_extracao_ruim_reprova_por_concordancia(self):
        conversas = carregar(escrever([CONVERSA]))
        relatorio = rodar(FakeLlm([resposta()]), conversas)
        self.assertLess(relatorio.concordancia_fatos, META_FATOS)
        self.assertFalse(relatorio.aprovado)

    def test_dataset_pequeno_gera_aviso(self):
        relatorio = rodar(FakeLlm([resposta()]), carregar(escrever([CONVERSA])))
        self.assertTrue(any("30" in a for a in relatorio.avisos))

    def test_llm_quebrado_nao_derruba_o_eval(self):
        conversas = carregar(escrever([CONVERSA]))
        relatorio = rodar(FakeLlm(["isso não é json"]), conversas)
        self.assertTrue(relatorio.resultados[0].erro)
        self.assertFalse(relatorio.aprovado)


if __name__ == "__main__":
    unittest.main()
