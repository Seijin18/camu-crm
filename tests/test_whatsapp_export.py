"""Parser do `.txt` exportado do WhatsApp (change
`importacao-conversas-whatsapp`) — puro, sem DB, sem LLM, sem rede.

Convenção dos testes: `nosso_nome="Camu"` é sempre exigido pelo parser (ver
`NomeOperadorNaoEncontradoError`), então toda conversa de teste inclui pelo
menos uma linha de "Camu" — exceto os testes da classe
`TesteNomeOperadorNaoEncontrado`, que testam exatamente a ausência dela.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from camucrm.rules.sinais import ENTRADA, SAIDA
from camucrm.whatsapp_export import (
    ExportacaoDeGrupoError,
    NomeOperadorNaoEncontradoError,
    parse,
)

_CAMU = "17/03/24, 08:00 - Camu: oi, tudo bem?"


class TesteFormatoAndroid(unittest.TestCase):
    def test_linha_simples_direcao_entrada(self):
        texto = f"17/03/24, 14:32 - Ana Petshop: oi, vocês fazem porta-chaves?\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 2)
        msg = resultado.mensagens[0]
        self.assertEqual(msg["direcao"], ENTRADA)
        self.assertEqual(msg["texto"], "oi, vocês fazem porta-chaves?")
        self.assertEqual(
            msg["enviada_em"], datetime(2024, 3, 17, 14, 32, tzinfo=timezone.utc)
        )
        self.assertEqual(resultado.nome_contato, "Ana Petshop")

    def test_linha_do_nosso_lado_vira_saida(self):
        texto = (
            "17/03/24, 14:32 - Ana Petshop: oi, vocês fazem porta-chaves?\n"
            "17/03/24, 14:35 - Camu: fazemos sim! manda a foto do pet"
        )
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual([m["direcao"] for m in resultado.mensagens], [ENTRADA, SAIDA])

    def test_ano_com_quatro_digitos_e_segundos(self):
        texto = f"17/03/2024, 14:32:05 - Ana: oi\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(
            resultado.mensagens[0]["enviada_em"],
            datetime(2024, 3, 17, 14, 32, 5, tzinfo=timezone.utc),
        )

    def test_correspondencia_de_nome_ignora_espaco_e_maiuscula(self):
        texto = "17/03/24, 14:32 -   camu  : oi"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(resultado.mensagens[0]["direcao"], SAIDA)


class TesteFormatoIOS(unittest.TestCase):
    def test_linha_com_colchete(self):
        texto = f"[17/03/24, 14:32:05] Ana Petshop: oi, tudo bem?\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(resultado.mensagens[0]["texto"], "oi, tudo bem?")
        self.assertEqual(resultado.mensagens[0]["direcao"], ENTRADA)


class TesteContinuacao(unittest.TestCase):
    def test_linha_sem_prefixo_junta_na_mensagem_anterior(self):
        texto = (
            f"{_CAMU}\n"
            "17/03/24, 14:32 - Ana Petshop: primeira linha\n"
            "segunda linha, sem timestamp"
        )
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 2)
        self.assertEqual(
            resultado.mensagens[1]["texto"],
            "primeira linha\nsegunda linha, sem timestamp",
        )

    def test_continuacao_orfa_sem_mensagem_anterior_e_reportada(self):
        texto = f"linha solta sem timestamp nenhum\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 1)
        self.assertIn("linha solta sem timestamp nenhum", resultado.ignoradas)


class TesteMidia(unittest.TestCase):
    def test_placeholder_generico_vira_midia_preservada(self):
        texto = f"17/03/24, 14:32 - Ana Petshop: <Mídia oculta>\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(resultado.midia_preservada, 1)
        self.assertEqual(resultado.mensagens[0]["texto"], "[mídia]")

    def test_placeholder_tipado_pt_br(self):
        texto = f"17/03/24, 14:32 - Ana Petshop: imagem ocultada\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(resultado.mensagens[0]["texto"], "[imagem]")

    def test_placeholder_ingles(self):
        texto = f"17/03/24, 14:32 - Ana Petshop: audio omitted\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(resultado.mensagens[0]["texto"], "[áudio]")
        self.assertEqual(resultado.midia_preservada, 1)


class TesteSistema(unittest.TestCase):
    def test_aviso_de_criptografia_e_ignorado_e_contado(self):
        texto = (
            "17/03/24, 14:30 - As mensagens e as chamadas são protegidas com "
            "a criptografia de ponta a ponta.\n"
            "17/03/24, 14:32 - Ana Petshop: oi\n"
            f"{_CAMU}"
        )
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 2)
        self.assertEqual(resultado.ignoradas, [])

    def test_mensagem_apagada_e_ignorada(self):
        texto = f"{_CAMU}\n17/03/24, 14:32 - Esta mensagem foi apagada."
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 1)
        self.assertEqual(resultado.ignoradas, [])

    def test_linha_de_continuacao_nao_gruda_apos_linha_de_sistema(self):
        texto = (
            f"{_CAMU}\n"
            "17/03/24, 14:32 - Ana Petshop: primeira mensagem\n"
            "17/03/24, 14:33 - Esta mensagem foi apagada.\n"
            "texto solto depois do apagamento"
        )
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 2)
        self.assertEqual(resultado.mensagens[1]["texto"], "primeira mensagem")
        self.assertIn("texto solto depois do apagamento", resultado.ignoradas)


class TesteLinhaNaoReconhecida(unittest.TestCase):
    def test_linha_com_timestamp_sem_remetente_nem_frase_de_sistema(self):
        texto = (
            "17/03/24, 14:32 - alguma coisa que não é nem mensagem nem aviso "
            f"conhecido\n{_CAMU}"
        )
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 1)
        self.assertEqual(len(resultado.ignoradas), 1)

    def test_hora_ilegivel_e_reportada_nao_descartada_em_silencio(self):
        texto = f"17/03/24, 25:99 - Ana: oi\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 1)
        self.assertEqual(len(resultado.ignoradas), 1)


class TesteGrupo(unittest.TestCase):
    def test_tres_remetentes_distintos_e_rejeitado(self):
        texto = (
            "17/03/24, 14:32 - Ana: oi\n"
            "17/03/24, 14:33 - Bruno: fala\n"
            "17/03/24, 14:34 - Camu: oi pessoal"
        )
        with self.assertRaises(ExportacaoDeGrupoError):
            parse(texto, nosso_nome="Camu")

    def test_linha_de_entrada_de_participante_e_rejeitada(self):
        texto = f"17/03/24, 14:32 - Camu adicionou Ana Petshop\n{_CAMU}"
        with self.assertRaises(ExportacaoDeGrupoError):
            parse(texto, nosso_nome="Camu")

    def test_conversa_1a1_normal_nao_e_rejeitada(self):
        texto = (
            "17/03/24, 14:32 - Ana Petshop: oi\n"
            "17/03/24, 14:35 - Camu: oi, tudo bem?"
        )
        resultado = parse(texto, nosso_nome="Camu")
        self.assertEqual(len(resultado.mensagens), 2)


class TesteNomeOperadorNaoEncontrado(unittest.TestCase):
    def test_nome_sem_correspondencia_levanta_erro(self):
        texto = "17/03/24, 14:32 - Ana Petshop: oi"
        with self.assertRaises(NomeOperadorNaoEncontradoError):
            parse(texto, nosso_nome="Nome Que Não Existe No Arquivo")

    def test_nome_vazio_levanta_erro(self):
        with self.assertRaises(ValueError):
            parse("17/03/24, 14:32 - Ana: oi", nosso_nome="   ")


class TesteFormatoRegistroCompativelComBackfill(unittest.TestCase):
    """`mensagens` precisa bater exatamente com o que
    `backfill.importar_conversas` espera — mesmas chaves, mesmos tipos."""

    def test_chaves_da_mensagem(self):
        texto = f"17/03/24, 14:32 - Ana Petshop: oi\n{_CAMU}"
        resultado = parse(texto, nosso_nome="Camu")
        msg = resultado.mensagens[0]
        self.assertEqual(set(msg.keys()), {"direcao", "texto", "enviada_em"})
        self.assertIsInstance(msg["enviada_em"], datetime)


if __name__ == "__main__":
    unittest.main()
