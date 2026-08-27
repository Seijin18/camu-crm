"""Change `rascunho-registrado` (§10): rascunho gerado é persistido, não
descartado — as duas opções, a escolha humana nos três formatos, e os dois
caminhos automáticos de vínculo com a mensagem realmente enviada.

Sem rede e sem Postgres: `FakeDatabase`. A garantia real das constraints
(`rascunhos_forma`, `rascunhos_escolha`, índice único parcial de
`mensagem_id`) é do Postgres e está provada em
`tests/integration/test_rascunhos_postgres.py` — aqui só se prova o
contrato do lado do Python que chama `Database`.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm import acoes  # noqa: E402
from camucrm.acoes import ENV_RECONCILIAR_RASCUNHO  # noqa: E402


class TesteGravarRascunho(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        self.conversa = self.db.criar_conversa(nome="Ana", estagio="S1")

    def test_grava_as_duas_opcoes(self):
        rascunho_id = self.db.gravar_rascunho(
            self.conversa.id,
            estagio="S1",
            temperatura="quente",
            funil="b2c",
            followups_enviados=0,
            opcoes=("opção um", "opção dois"),
        )
        registro = self.db.rascunho(rascunho_id)
        self.assertEqual(registro.opcao_1, "opção um")
        self.assertEqual(registro.opcao_2, "opção dois")
        self.assertFalse(registro.encerrar)
        self.assertIsNone(registro.motivo)

    def test_grava_recusa_com_motivo_sem_opcoes(self):
        rascunho_id = self.db.gravar_rascunho(
            self.conversa.id,
            estagio="S1",
            temperatura="frio",
            funil="b2c",
            followups_enviados=2,
            encerrar=True,
            motivo="teto de 2 follow-ups atingido",
        )
        registro = self.db.rascunho(rascunho_id)
        self.assertTrue(registro.encerrar)
        self.assertEqual(registro.motivo, "teto de 2 follow-ups atingido")
        self.assertIsNone(registro.opcao_1)
        self.assertIsNone(registro.opcao_2)

    def test_recusa_nunca_e_meia_geracao(self):
        """Nem as duas opções fora de `encerrar`, nem `encerrar` com opções."""
        with self.assertRaises(ValueError):
            self.db.gravar_rascunho(
                self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
                encerrar=False, opcoes=None,
            )
        with self.assertRaises(ValueError):
            self.db.gravar_rascunho(
                self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
                encerrar=True, opcoes=("a", "b"), motivo="x",
            )


class TesteEscolhaNosTresFormatos(unittest.TestCase):
    """Requirement "Opção não escolhida não é descartada": as duas opções
    continuam na linha depois de qualquer uma das três formas de escolha."""

    def setUp(self):
        self.db = FakeDatabase()
        self.conversa = self.db.criar_conversa(nome="Ana", estagio="S1")
        self.rascunho_id = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("Manda a foto do seu pet?", "Consegue mandar uma foto dele?"),
        )

    def test_escolheu_uma_opcao_tal_como_veio(self):
        self.db.registrar_escolha_rascunho(self.rascunho_id, escolhida=1, por="Marcos")
        registro = self.db.rascunho(self.rascunho_id)
        self.assertEqual(registro.escolhida, 1)
        self.assertIsNone(registro.texto_final)
        self.assertIsNotNone(registro.escolhido_em)
        self.assertEqual(registro.escolhido_por, "Marcos")
        # As duas opções continuam na linha.
        self.assertIsNotNone(registro.opcao_1)
        self.assertIsNotNone(registro.opcao_2)

    def test_escolheu_e_editou(self):
        self.db.registrar_escolha_rascunho(
            self.rascunho_id, escolhida=2, texto_final="Manda foto do peludo aí?",
            por="Marcos",
        )
        registro = self.db.rascunho(self.rascunho_id)
        self.assertEqual(registro.escolhida, 2)
        self.assertEqual(registro.texto_final, "Manda foto do peludo aí?")
        self.assertIsNotNone(registro.opcao_1)
        self.assertIsNotNone(registro.opcao_2)

    def test_escreveu_do_zero(self):
        """`escolhida IS NULL` com `texto_final` preenchido é válido."""
        self.db.registrar_escolha_rascunho(
            self.rascunho_id, texto_final="Oi! Me conta mais do seu pet.", por="Marcos"
        )
        registro = self.db.rascunho(self.rascunho_id)
        self.assertIsNone(registro.escolhida)
        self.assertEqual(registro.texto_final, "Oi! Me conta mais do seu pet.")
        self.assertIsNotNone(registro.escolhido_em)

    def test_escolha_sem_opcao_nem_texto_final_e_recusada(self):
        with self.assertRaises(ValueError):
            self.db.registrar_escolha_rascunho(self.rascunho_id)

    def test_escolhida_fora_de_1_ou_2_e_recusada(self):
        with self.assertRaises(ValueError):
            self.db.registrar_escolha_rascunho(self.rascunho_id, escolhida=3)


class TesteVinculoPorFlagDaCli(unittest.TestCase):
    """Caminho 1 (design.md) — o mais confiável: o operador declara qual
    rascunho usou. `cli.cmd_enviar` faz exatamente esta sequência depois de
    `registrar_mensagem` devolver o id."""

    def setUp(self):
        self.db = FakeDatabase()
        self.conversa = self.db.criar_conversa(nome="Ana", estagio="S1")
        self.rascunho_id = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("Manda a foto do seu pet?", "Consegue mandar uma foto dele?"),
        )

    def test_enviar_com_flag_vincula_mensagem_id(self):
        mensagem_id = self.db.registrar_mensagem(
            self.conversa.id, "out", "Manda a foto do seu pet?"
        )
        vinculado = self.db.vincular_rascunho(
            self.rascunho_id, mensagem_id, estagio_no_envio=self.conversa.estagio
        )
        self.assertTrue(vinculado)
        registro = self.db.rascunho(self.rascunho_id)
        self.assertEqual(registro.mensagem_id, mensagem_id)
        self.assertEqual(registro.estagio_no_envio, "S1")

    def test_segunda_mensagem_nao_reivindica_o_mesmo_rascunho_duas_vezes(self):
        """A garantia real é do índice único parcial (Postgres); o fake só
        espelha a recusa para o caminho de chamada não passar despercebido."""
        m1 = self.db.registrar_mensagem(self.conversa.id, "out", "primeira")
        m2 = self.db.registrar_mensagem(self.conversa.id, "out", "segunda")
        self.db.vincular_rascunho(self.rascunho_id, m1)
        outro_rascunho = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("x", "y"),
        )
        with self.assertRaises(ValueError):
            self.db.vincular_rascunho(outro_rascunho, m1)
        # A segunda mensagem, livre, ainda pode ser vinculada a outro rascunho.
        self.assertTrue(self.db.vincular_rascunho(outro_rascunho, m2))


class TesteReconciliacaoPeloEco(unittest.TestCase):
    """Caminho 2 (design.md): casamento EXATO de texto normalizado. A
    ausência de vínculo quando o texto foi editado é o contrato — não um
    bug a "melhorar" com fuzzy matching."""

    def setUp(self):
        self.db = FakeDatabase()
        self.conversa = self.db.criar_conversa(nome="Ana", estagio="S1")
        self.rascunho_id = self.db.gravar_rascunho(
            self.conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("Manda a foto do seu pet?\nTe mostro como fica.",
                    "Consegue mandar uma foto dele?\nJá te envio a prévia."),
        )

    def test_normalizar_colapsa_espaco_e_casefold(self):
        self.assertEqual(
            acoes._normalizar("  Manda a FOTO   do seu pet?  "),
            "manda a foto do seu pet?",
        )
        self.assertEqual(acoes._normalizar(None), "")

    def test_texto_exato_apos_normalizacao_vincula(self):
        mensagem_id = self.db.registrar_mensagem(
            self.conversa.id, "out",
            "  Manda a foto do seu pet?\nTe mostro como fica.  ",
        )
        vinculado = acoes.reconciliar_rascunho(
            self.db, self.conversa.id, mensagem_id,
            "  Manda a foto do seu pet?\nTe mostro como fica.  ",
        )
        self.assertEqual(vinculado, self.rascunho_id)
        self.assertEqual(self.db.rascunho(self.rascunho_id).mensagem_id, mensagem_id)

    def test_texto_editado_nao_vincula(self):
        """A asserção sobre a AUSÊNCIA de vínculo é o contrato."""
        mensagem_id = self.db.registrar_mensagem(
            self.conversa.id, "out", "Manda foto do peludo aí? Te mostro como fica."
        )
        vinculado = acoes.reconciliar_rascunho(
            self.db, self.conversa.id, mensagem_id,
            "Manda foto do peludo aí? Te mostro como fica.",
        )
        self.assertIsNone(vinculado)
        self.assertIsNone(self.db.rascunho(self.rascunho_id).mensagem_id)

    def test_desligavel_por_variavel_de_ambiente(self):
        mensagem_id = self.db.registrar_mensagem(
            self.conversa.id, "out",
            "Manda a foto do seu pet?\nTe mostro como fica.",
        )
        with patch.dict(os.environ, {ENV_RECONCILIAR_RASCUNHO: "false"}):
            vinculado = acoes.reconciliar_rascunho(
                self.db, self.conversa.id, mensagem_id,
                "Manda a foto do seu pet?\nTe mostro como fica.",
            )
        self.assertIsNone(vinculado)
        self.assertIsNone(self.db.rascunho(self.rascunho_id).mensagem_id)

    def test_casa_tambem_contra_texto_final_editado_manualmente(self):
        """Se o humano editou e registrou `texto_final`, o eco casa contra
        ele — não mais contra as opções originais."""
        self.db.registrar_escolha_rascunho(
            self.rascunho_id, texto_final="Oi! Manda foto do bichinho.", por="Marcos"
        )
        mensagem_id = self.db.registrar_mensagem(
            self.conversa.id, "out", "Oi! Manda foto do bichinho."
        )
        vinculado = acoes.reconciliar_rascunho(
            self.db, self.conversa.id, mensagem_id, "Oi! Manda foto do bichinho."
        )
        self.assertEqual(vinculado, self.rascunho_id)

    def test_rascunho_ja_vinculado_nao_e_candidato(self):
        primeira = self.db.registrar_mensagem(
            self.conversa.id, "out",
            "Manda a foto do seu pet?\nTe mostro como fica.",
        )
        self.db.vincular_rascunho(self.rascunho_id, primeira)
        segunda = self.db.registrar_mensagem(
            self.conversa.id, "out",
            "Manda a foto do seu pet?\nTe mostro como fica.",
        )
        vinculado = acoes.reconciliar_rascunho(
            self.db, self.conversa.id, segunda,
            "Manda a foto do seu pet?\nTe mostro como fica.",
        )
        self.assertIsNone(vinculado)


class TesteRegistroManualDeEscolha(unittest.TestCase):
    """Caminho 3 (design.md): `POST /api/rascunhos/{id}/escolha` sem
    `mensagem_id` — o operador diz "usei a opção 1" sem que o sistema saiba
    qual mensagem concreta corresponde."""

    def test_escolha_manual_nao_cria_vinculo_com_mensagem(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana", estagio="S1")
        rascunho_id = db.gravar_rascunho(
            conversa.id, estagio="S1", temperatura="quente", funil="b2c",
            opcoes=("a", "b"),
        )
        db.registrar_escolha_rascunho(rascunho_id, escolhida=1, por="Marcos")
        registro = db.rascunho(rascunho_id)
        self.assertEqual(registro.escolhida, 1)
        self.assertIsNone(registro.mensagem_id)


if __name__ == "__main__":
    unittest.main()
