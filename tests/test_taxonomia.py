"""Taxonomias fechadas: o que §0 diz que não dá para refazer depois."""

import unittest

from camucrm import taxonomia as t


class TesteEstagios(unittest.TestCase):
    def test_ordem_do_funil_define_o_rank(self):
        self.assertLess(t.rank_estagio("S1"), t.rank_estagio("S2"))
        self.assertLess(t.rank_estagio("P2"), t.rank_estagio("P5"))

    def test_terminal_nao_tem_rank_de_avanco(self):
        self.assertEqual(t.rank_estagio("SX"), -1)
        self.assertEqual(t.rank_estagio("PX"), -1)
        self.assertTrue(t.is_terminal("SX"))
        self.assertTrue(t.is_terminal("PX"))

    def test_estagio_desconhecido_levanta(self):
        with self.assertRaises(ValueError):
            t.rank_estagio("S9")

    def test_funil_derivado_do_prefixo(self):
        self.assertEqual(t.funil_do_estagio("S3"), t.B2C)
        self.assertEqual(t.funil_do_estagio("P3"), t.B2B)

    def test_estagios_manuais_sao_os_tres_da_secao_3(self):
        self.assertEqual(t.ESTAGIOS_MANUAIS, frozenset({"S6", "P5", "P6"}))


class TesteObjecoes(unittest.TestCase):
    def test_preco_e_frete_sao_categorias_distintas(self):
        """§4: somá-los apagaria a pergunta em aberto sobre o choque de frete."""
        self.assertNotEqual(t.OBJECAO_PRECO, t.OBJECAO_FRETE)
        self.assertIn(t.OBJECAO_PRECO, t.OBJECOES)
        self.assertIn(t.OBJECAO_FRETE, t.OBJECOES)

    def test_lista_e_fechada(self):
        with self.assertRaises(ValueError):
            t.validate_objecao("caro demais")

    def test_normaliza_caixa_e_espaco(self):
        self.assertEqual(t.validate_objecao("  FRETE "), "frete")

    def test_vazio_vira_none(self):
        self.assertIsNone(t.validate_objecao(""))
        self.assertIsNone(t.validate_objecao(None))

    def test_toda_objecao_tem_rotulo(self):
        for codigo in t.OBJECOES:
            self.assertIn(codigo, t.OBJECAO_LABELS)


class TesteLimites(unittest.TestCase):
    def test_teto_de_followups_e_dois(self):
        self.assertEqual(t.MAX_FOLLOWUPS, 2)

    def test_fila_tem_no_maximo_dez(self):
        self.assertEqual(t.FILA_TAMANHO_MAXIMO, 10)

    def test_limites_de_revisao_da_taxonomia(self):
        self.assertEqual(t.OUTRO_LIMITE_SUPERIOR, 0.15)
        self.assertEqual(t.OUTRO_LIMITE_INFERIOR, 0.03)


class TesteRotulos(unittest.TestCase):
    def test_todo_estagio_tem_rotulo(self):
        for estagio in t.TODOS_ESTAGIOS:
            self.assertNotEqual(t.estagio_label(estagio), estagio)


if __name__ == "__main__":
    unittest.main()
