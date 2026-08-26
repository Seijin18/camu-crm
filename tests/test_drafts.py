"""Rascunhos (§10): duas opções, restrições verificadas, nunca envio."""

import json
import unittest

from camucrm.drafts import (
    RascunhoInvalidoError,
    deve_encerrar,
    gerar,
    validar_opcoes,
)
from camucrm.llm import FakeLlm

DUAS_OK = json.dumps({"opcoes": [
    "Manda uma foto do seu pet?\nTe mostro como fica antes de fechar nada.",
    "Consegue mandar uma foto dele?\nEm alguns minutos te envio a prévia.",
]})


class TesteRecusa(unittest.TestCase):
    def test_frio_com_um_followup_devolve_encerrar(self):
        self.assertIsNotNone(deve_encerrar("frio", 1))

    def test_teto_atingido_devolve_encerrar(self):
        self.assertIsNotNone(deve_encerrar("quente", 2))

    def test_quente_sem_followup_gera(self):
        self.assertIsNone(deve_encerrar("quente", 0))

    def test_gerar_nao_chama_llm_quando_deve_encerrar(self):
        llm = FakeLlm([DUAS_OK])
        rascunho = gerar(llm, [("in", "oi")], estagio="S3", temperatura="frio",
                         funil="b2c", followups_enviados=1)
        self.assertTrue(rascunho.encerrar)
        self.assertEqual(llm.chamadas, [])


class TesteRestricoes(unittest.TestCase):
    def test_sempre_duas_opcoes(self):
        """§10: rascunho único vira aprovação automática."""
        with self.assertRaises(RascunhoInvalidoError):
            validar_opcoes(["uma opção só\ncom duas linhas"], estagio="S4")

    def test_opcoes_identicas_sao_recusadas(self):
        texto = "Oi!\nTudo bem?"
        with self.assertRaises(RascunhoInvalidoError):
            validar_opcoes([texto, texto.upper()], estagio="S4")

    def test_limite_de_linhas(self):
        longa = "\n".join(f"linha {i}" for i in range(6))
        with self.assertRaises(RascunhoInvalidoError):
            validar_opcoes([longa, "ok\nok"], estagio="S4")

    def test_tabela_de_preco_completa_e_recusada(self):
        with self.assertRaises(RascunhoInvalidoError):
            validar_opcoes(
                ["Temos R$ 149 e R$ 199.\nQual prefere?", "Outra opção\naqui"],
                estagio="S4",
            )

    def test_preco_em_s2_e_recusado(self):
        with self.assertRaises(RascunhoInvalidoError):
            validar_opcoes(["Fica R$ 149.\nQuer fechar?", "Outra\nopção"], estagio="S2")

    def test_preco_em_s4_e_permitido(self):
        opcoes, _ = validar_opcoes(
            ["A peça sai R$ 149.\nO frete varia por região.", "Posso te passar\no valor fechado?"],
            estagio="S4",
        )
        self.assertEqual(len(opcoes), 2)

    def test_tom_infantilizado_vira_aviso_nao_recusa(self):
        _, avisos = validar_opcoes(
            ["Manda uma fotinha dele?\nTe mostro como fica.", "Manda uma foto?\nJá te envio."],
            estagio="S2",
        )
        self.assertTrue(avisos)


class TesteGeracao(unittest.TestCase):
    def test_gera_duas_opcoes(self):
        rascunho = gerar(FakeLlm([DUAS_OK]), [("in", "oi")], estagio="S1",
                         temperatura="quente", funil="b2c")
        self.assertEqual(len(rascunho.opcoes), 2)
        self.assertFalse(rascunho.encerrar)

    def test_retenta_uma_vez_com_o_motivo(self):
        ruim = json.dumps({"opcoes": ["Fica R$ 149.\nQuer?", "Sai R$ 149\ne frete R$ 20."]})
        llm = FakeLlm([ruim, DUAS_OK])
        rascunho = gerar(llm, [("in", "quanto")], estagio="S2",
                         temperatura="quente", funil="b2c")
        self.assertEqual(len(rascunho.opcoes), 2)
        self.assertEqual(len(llm.chamadas), 2)
        self.assertIn("recusada", llm.chamadas[1][1])

    def test_desiste_apos_a_segunda_falha(self):
        ruim = json.dumps({"opcoes": ["uma só\nlinha dupla"]})
        with self.assertRaises(RascunhoInvalidoError):
            gerar(FakeLlm([ruim, ruim]), [("in", "oi")], estagio="S4",
                  temperatura="quente", funil="b2c")

    def test_resposta_sem_json_e_recusada(self):
        with self.assertRaises(RascunhoInvalidoError):
            gerar(FakeLlm(["claro, aqui vai:", "ainda não"]), [("in", "oi")],
                  estagio="S4", temperatura="quente", funil="b2c")


if __name__ == "__main__":
    unittest.main()
