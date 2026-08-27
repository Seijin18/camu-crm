"""Contrato de extração (§2): o que impede o modelo de avançar por otimismo."""

import json
import unittest

from camucrm.extraction.contract import (
    EVIDENCIA_NAO_LITERAL,
    SEM_EVIDENCIA,
    ContratoInvalidoError,
    Extracao,
    build_corpus,
    extracao_vazia,
    merge,
    validar,
)

# (direcao, texto) — o cliente manda a foto e reclama do preço; a Camu
# apresenta o preço. Cada mensagem rotulada com quem falou de verdade, porque
# a conferência de literalidade agora exige o lado certo (§2, direção).
CONVERSA = [
    ("in", "Oi, segue a foto do meu cachorro"),
    ("out", "Ficou R$ 149 com frete grátis"),
    ("in", "achei caro"),
]


class TesteExigenciaDeEvidencia(unittest.TestCase):
    def test_true_sem_evidencia_volta_a_false(self):
        extracao = validar({"foto_pet_recebida": True, "evidencias": {}})
        self.assertFalse(extracao["foto_pet_recebida"])
        self.assertEqual(extracao.democoes[0].motivo, SEM_EVIDENCIA)

    def test_true_com_evidencia_literal_sobrevive(self):
        corpus = build_corpus(CONVERSA)
        extracao = validar(
            {
                "foto_pet_recebida": True,
                "evidencias": {"foto_pet_recebida": "segue a foto do meu cachorro"},
            },
            corpus=corpus,
        )
        self.assertTrue(extracao["foto_pet_recebida"])
        self.assertEqual(extracao.democoes, ())

    def test_evidencia_inventada_e_rebaixada(self):
        """O modo de falha mais caro: trecho plausível que ninguém disse."""
        corpus = build_corpus(CONVERSA)
        extracao = validar(
            {
                "previa_enviada": True,
                "evidencias": {"previa_enviada": "olha como ficou a arte do Thor"},
            },
            corpus=corpus,
        )
        self.assertFalse(extracao["previa_enviada"])
        self.assertEqual(extracao.democoes[0].motivo, EVIDENCIA_NAO_LITERAL)

    def test_acento_e_espaco_nao_quebram_a_conferencia(self):
        corpus = build_corpus([("in", "Oi, segue a  foto do meu cachorro")])
        extracao = validar(
            {"foto_pet_recebida": True,
             "evidencias": {"foto_pet_recebida": "segue a foto do meu cachôrro"}},
            corpus=corpus,
        )
        self.assertTrue(extracao["foto_pet_recebida"])

    def test_evidencia_curta_demais_nao_prova_nada(self):
        extracao = validar(
            {"intencao_compra_explicita": True,
             "evidencias": {"intencao_compra_explicita": "ok"}},
            corpus=build_corpus([("in", "ok")]),
        )
        self.assertFalse(extracao["intencao_compra_explicita"])


class TesteFronteiraEntreMensagens(unittest.TestCase):
    """§2: `build_corpus` não pode deixar o fim de uma mensagem colar no
    começo da seguinte — reprodução direta do achado que motivou este change.
    """

    def test_evidencia_que_so_existe_fundida_nao_valida_fato(self):
        # Nenhuma mensagem, isolada, contém "mas nao vou fechar agora" — o
        # trecho só existe se "...mas" (fim da primeira) colar em "nao vou
        # fechar agora" (começo da segunda).
        corpus = build_corpus(
            [
                ("in", "quero saber o preco, mas"),
                ("in", "nao vou fechar agora"),
            ]
        )
        extracao = validar(
            {
                "recusa_explicita": True,
                "evidencias": {"recusa_explicita": "mas nao vou fechar agora"},
            },
            corpus=corpus,
        )
        self.assertFalse(extracao["recusa_explicita"])
        self.assertEqual(extracao.democoes[0].motivo, EVIDENCIA_NAO_LITERAL)

    def test_evidencia_contida_numa_unica_mensagem_continua_valida(self):
        """A correção da fronteira não pode regredir literalidade válida
        dentro de uma única mensagem."""
        corpus = build_corpus([("in", "quero saber o preco, mas nao vou fechar agora")])
        extracao = validar(
            {
                "recusa_explicita": True,
                "evidencias": {"recusa_explicita": "mas nao vou fechar agora"},
            },
            corpus=corpus,
        )
        self.assertTrue(extracao["recusa_explicita"])


class TesteDirecaoDaEvidencia(unittest.TestCase):
    """§2: fato de cliente exige fala do cliente; fato da Camu exige fala da
    Camu — um trecho do lado errado não sustenta o campo, mesmo literal."""

    def test_evidencia_da_camu_nao_valida_fato_de_cliente(self):
        corpus = build_corpus([("out", "Você não vai comprar hoje, certo?")])
        extracao = validar(
            {
                "recusa_explicita": True,
                "evidencias": {"recusa_explicita": "você não vai comprar hoje"},
            },
            corpus=corpus,
        )
        self.assertFalse(extracao["recusa_explicita"])
        self.assertEqual(extracao.democoes[0].motivo, EVIDENCIA_NAO_LITERAL)

    def test_evidencia_do_cliente_nao_valida_fato_da_camu(self):
        corpus = build_corpus([("in", "voce ja mostrou a previa da arte do Thor?")])
        extracao = validar(
            {
                "previa_enviada": True,
                "evidencias": {"previa_enviada": "mostrou a previa da arte do Thor"},
            },
            corpus=corpus,
        )
        self.assertFalse(extracao["previa_enviada"])
        self.assertEqual(extracao.democoes[0].motivo, EVIDENCIA_NAO_LITERAL)

    def test_evidencia_do_lado_certo_continua_valida(self):
        corpus = build_corpus(
            [
                ("in", "oi"),
                ("out", "Ficou R$ 149 com frete grátis"),
            ]
        )
        extracao = validar(
            {
                "preco_apresentado": True,
                "evidencias": {"preco_apresentado": "ficou r$ 149 com frete gratis"},
            },
            corpus=corpus,
        )
        self.assertTrue(extracao["preco_apresentado"])


class TesteObjecao(unittest.TestCase):
    def test_categoria_fora_da_lista_vira_outro_com_trecho(self):
        extracao = validar({"objecao": "muito caro pra mim"})
        self.assertEqual(extracao.objecao, "outro")
        self.assertEqual(extracao.evidencias["objecao"], "muito caro pra mim")

    def test_outro_sem_trecho_e_descartado(self):
        extracao = validar({"objecao": "outro", "evidencias": {}})
        self.assertIsNone(extracao.objecao)
        self.assertTrue(extracao.democoes)

    def test_preco_e_frete_permanecem_separados(self):
        self.assertEqual(validar({"objecao": "preco"}).objecao, "preco")
        self.assertEqual(validar({"objecao": "frete"}).objecao, "frete")


class TesteParsing(unittest.TestCase):
    def test_aceita_cerca_de_markdown(self):
        bruto = '```json\n{"recusa_explicita": false}\n```'
        self.assertFalse(validar(bruto)["recusa_explicita"])

    def test_resposta_sem_json_levanta(self):
        with self.assertRaises(ContratoInvalidoError):
            validar("desculpe, não consegui analisar")

    def test_valor_ilegivel_vira_false(self):
        extracao = validar({"visita_aceita": "talvez"})
        self.assertFalse(extracao["visita_aceita"])
        self.assertTrue(extracao.democoes)


class TesteMerge(unittest.TestCase):
    def test_fato_e_monotonico(self):
        """A foto que chegou ontem não deixa de ter chegado hoje."""
        anterior = validar(
            {"foto_pet_recebida": True, "evidencias": {"foto_pet_recebida": "segue a foto"}}
        )
        nova = validar({"preco_apresentado": True,
                        "evidencias": {"preco_apresentado": "fica R$ 149"}})
        combinada = merge(anterior, nova)
        self.assertTrue(combinada["foto_pet_recebida"])
        self.assertTrue(combinada["preco_apresentado"])

    def test_objecao_mais_recente_ganha(self):
        anterior = Extracao(fatos={}, objecao="preco")
        nova = Extracao(fatos={}, objecao="frete")
        self.assertEqual(merge(anterior, nova).objecao, "frete")

    def test_objecao_ausente_no_bloco_novo_preserva_a_anterior(self):
        anterior = Extracao(fatos={}, objecao="preco")
        self.assertEqual(merge(anterior, extracao_vazia()).objecao, "preco")


class TesteSerializacao(unittest.TestCase):
    def test_to_dict_tem_o_formato_do_contrato(self):
        payload = extracao_vazia().to_dict()
        self.assertIn("evidencias", payload)
        self.assertIsNone(payload["objecao"])
        self.assertFalse(any(payload[c] for c in payload if isinstance(payload[c], bool)))
        json.dumps(payload)  # precisa ser serializável


if __name__ == "__main__":
    unittest.main()
