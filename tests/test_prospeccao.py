"""Prospecção B2B (change `prospeccao-b2b-shortlist`): importação, filtros,
detecção de conversão e o link de WhatsApp — sempre separado de
contatos/conversas (§12 do documento, base legal = legítimo interesse B2B,
ver `openspec/project.md`)."""

from __future__ import annotations

import ast
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm.db import hash_telefone  # noqa: E402
from camucrm.prospeccao import (  # noqa: E402
    link_whatsapp,
    montar_mensagem,
    nome_curto,
    normalizar_telefone_br,
)

CAMUCRM_DIR = Path(__file__).parent.parent / "camucrm"


def _linha(
    petshop="Petshop Teste",
    bairro="Centro",
    zona="Leste",
    telefone="(12) 98157-5051",
    nota="4.6",
    avaliacoes="223",
    site="",
    tier_origem="A",
    status_origem="valido",
):
    return {
        "petshop": petshop,
        "bairro": bairro,
        "zona": zona,
        "telefone": telefone,
        "nota": nota,
        "avaliacoes": avaliacoes,
        "site": site,
        "tier_origem": tier_origem,
        "status_origem": status_origem,
    }


class TesteNormalizarTelefone(unittest.TestCase):
    """Requirement do link: dígitos + código do país — o MESMO formato que
    `contatos.telefone` carrega quando o número responde de verdade pelo
    WhatsApp (design.md)."""

    def test_telefone_local_ganha_codigo_do_pais(self):
        self.assertEqual(normalizar_telefone_br("(12) 98157-5051"), "5512981575051")

    def test_telefone_ja_com_codigo_do_pais_fica_igual(self):
        self.assertEqual(normalizar_telefone_br("5512981575051"), "5512981575051")

    def test_telefone_vazio_e_invalido(self):
        self.assertIsNone(normalizar_telefone_br(""))
        self.assertIsNone(normalizar_telefone_br(None))

    def test_telefone_com_poucos_digitos_e_invalido(self):
        self.assertIsNone(normalizar_telefone_br("981-5051"))


class TesteNomeCurto(unittest.TestCase):
    def test_corta_no_pipe_e_vira_minusculo(self):
        nome = "NetCão Pet Shop | Desde 1997 apaixonados por pet, assim como você!"
        self.assertEqual(nome_curto(nome), "netcão pet shop")

    def test_corta_no_traco_com_espacos(self):
        self.assertEqual(nome_curto("Vale Pet - Clínica Veterinária e Banho & Tosa"), "vale pet")

    def test_sem_separador_so_vira_minusculo(self):
        self.assertEqual(nome_curto("AGRO DOG SJC"), "agro dog sjc")


class TesteLinkWhatsapp(unittest.TestCase):
    def test_texto_acentuado_e_url_encoded_sem_quebrar(self):
        mensagem = montar_mensagem("Oi, {nome}! Tudo bem?", "Casa de Ração Ortega Pet shop")
        self.assertIn("casa de ração ortega pet shop", mensagem)
        link = link_whatsapp("5512982152820", mensagem)
        self.assertTrue(link.startswith("https://api.whatsapp.com/send/?phone=5512982152820&text="))
        self.assertNotIn(" ", link)  # espaço sempre escapado
        self.assertNotIn("ç", link)  # acento nunca cru na URL
        self.assertIn("%C3%A7", link)  # "ç" percent-encoded (UTF-8)


class TesteImportacao(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()

    def test_importar_csv_novo_cria_linhas(self):
        resumo = self.db.importar_prospeccoes([_linha(), _linha(petshop="Outro Pet", telefone="(12) 99653-9100")])
        self.assertEqual(resumo.novos, 2)
        self.assertEqual(resumo.atualizados, 0)
        self.assertEqual(resumo.invalidas, [])
        self.assertEqual(len(self.db.prospeccoes), 2)

    def test_reimportar_mesma_planilha_atualiza_nao_duplica(self):
        self.db.importar_prospeccoes([_linha(nota="4.6")])
        resumo = self.db.importar_prospeccoes([_linha(nota="4.9")])
        self.assertEqual(resumo.novos, 0)
        self.assertEqual(resumo.atualizados, 1)
        self.assertEqual(len(self.db.prospeccoes), 1)
        registro = self.db.listar_prospeccoes()[0]
        self.assertEqual(registro.nota, 4.9)

    def test_telefone_ilegivel_e_reportado_nao_vira_linha(self):
        resumo = self.db.importar_prospeccoes([_linha(telefone="")])
        self.assertEqual(resumo.novos, 0)
        self.assertEqual(len(resumo.invalidas), 1)
        self.assertEqual(resumo.invalidas[0].linha, 1)
        self.assertIn("ilegível", resumo.invalidas[0].motivo)
        self.assertEqual(len(self.db.prospeccoes), 0)

    def test_nome_vazio_tambem_e_reportado(self):
        resumo = self.db.importar_prospeccoes([_linha(petshop="")])
        self.assertEqual(len(resumo.invalidas), 1)
        self.assertEqual(len(self.db.prospeccoes), 0)

    def test_linha_invalida_nao_impede_as_demais(self):
        resumo = self.db.importar_prospeccoes([
            _linha(telefone=""),
            _linha(petshop="Petshop Válido", telefone="(12) 99653-9100"),
        ])
        self.assertEqual(resumo.novos, 1)
        self.assertEqual(len(resumo.invalidas), 1)


class TesteListagemComFiltros(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        self.db.criar_prospeccao(
            nome="Petshop Leste", zona="Leste", bairro="Jardim Paulista",
            nota=4.9, tier_origem="A", telefone="5512999990001",
        )
        self.db.criar_prospeccao(
            nome="Petshop Norte", zona="Norte", bairro="Santana",
            nota=4.2, tier_origem="B", telefone="5512999990002",
        )

    def test_filtro_por_zona(self):
        resultado = self.db.listar_prospeccoes(zona="Leste")
        self.assertEqual([p.nome for p in resultado], ["Petshop Leste"])

    def test_filtro_por_bairro(self):
        resultado = self.db.listar_prospeccoes(bairro="Santana")
        self.assertEqual([p.nome for p in resultado], ["Petshop Norte"])

    def test_filtro_por_nota_minima(self):
        resultado = self.db.listar_prospeccoes(nota_minima=4.5)
        self.assertEqual([p.nome for p in resultado], ["Petshop Leste"])

    def test_filtro_por_tier(self):
        resultado = self.db.listar_prospeccoes(tier="B")
        self.assertEqual([p.nome for p in resultado], ["Petshop Norte"])

    def test_sem_filtro_lista_as_duas(self):
        resultado = self.db.listar_prospeccoes()
        self.assertEqual(len(resultado), 2)


class TesteDeteccaoDeConversao(unittest.TestCase):
    """Requirement "Detecção de conversão sem estado próprio" (design.md):
    sem coluna/job de sincronização — a próxima leitura já enxerga."""

    def test_prospeccao_vira_conversa_real_aparece_na_proxima_leitura(self):
        db = FakeDatabase()
        telefone = "5512999990003"
        prospeccao = db.criar_prospeccao(nome="Petshop Convertido", telefone=telefone)
        antes = db.listar_prospeccoes()[0]
        self.assertIsNone(antes.contato_id)
        self.assertIsNone(antes.conversa_id)

        from camucrm.ingest import ingerir
        from camucrm.transport.base import EventoRecebido

        ingerir(
            db,
            EventoRecebido(telefone=telefone, texto="oi", enviada_em=datetime.now(timezone.utc)),
        )

        depois = db.listar_prospeccoes()[0]
        self.assertIsNotNone(depois.contato_id)
        self.assertIsNotNone(depois.conversa_id)

    def test_apenas_nao_convertidas_exclui_quem_ja_e_conversa(self):
        db = FakeDatabase()
        telefone_convertido = "5512999990004"
        telefone_livre = "5512999990005"
        db.criar_prospeccao(nome="Convertido", telefone=telefone_convertido)
        db.criar_prospeccao(nome="Livre", telefone=telefone_livre)

        from camucrm.ingest import ingerir
        from camucrm.transport.base import EventoRecebido

        ingerir(
            db,
            EventoRecebido(
                telefone=telefone_convertido, texto="oi",
                enviada_em=datetime.now(timezone.utc),
            ),
        )

        resultado = db.listar_prospeccoes(apenas_nao_convertidas=True)
        self.assertEqual([p.nome for p in resultado], ["Livre"])


class TesteAberturaDeLink(unittest.TestCase):
    def test_marcar_prospeccao_aberta_grava_quem_e_quando(self):
        db = FakeDatabase()
        prospeccao = db.criar_prospeccao(nome="Petshop X", telefone="5512999990006")
        db.marcar_prospeccao_aberta(prospeccao.id, por="marcos")
        registro = db.listar_prospeccoes()[0]
        self.assertIsNotNone(registro.aberto_em)
        self.assertEqual(registro.aberto_por, "marcos")


class TesteMarcasManuais(unittest.TestCase):
    """Change `prospeccao-marcar-enviada-e-nao-whatsapp`: as duas marcas
    manuais da aba de prospecção — "já enviado" (sem passar pela API) e "não
    é número de WhatsApp"."""

    def setUp(self):
        self.db = FakeDatabase()
        self.p = self.db.criar_prospeccao(nome="Petshop M", telefone="5512999990020")

    def test_marcar_enviada_manual_grava_como_instancia_manual(self):
        self.db.marcar_prospeccao_enviada_manual(self.p.id, por="marcos")
        registro = self.db.listar_prospeccoes()[0]
        self.assertIsNotNone(registro.enviado_em)
        self.assertEqual(registro.enviado_por, "marcos")
        self.assertEqual(registro.enviado_instancia, "manual")

    def test_desfazer_enviada_manual_so_limpa_marca_manual(self):
        self.db.marcar_prospeccao_enviada_manual(self.p.id, por="marcos")
        self.db.marcar_prospeccao_enviada_manual(self.p.id, por="marcos", valor=False)
        registro = self.db.listar_prospeccoes()[0]
        self.assertIsNone(registro.enviado_em)
        self.assertIsNone(registro.enviado_instancia)

    def test_desfazer_nao_apaga_envio_real_pela_api(self):
        self.db.registrar_envio_prospeccao(
            self.p.id, por="marcos", sucesso=True, instancia="pessoal-felipe"
        )
        self.db.marcar_prospeccao_enviada_manual(self.p.id, por="marcos", valor=False)
        registro = self.db.listar_prospeccoes()[0]
        self.assertIsNotNone(registro.enviado_em)
        self.assertEqual(registro.enviado_instancia, "pessoal-felipe")

    def test_marcar_nao_whatsapp_e_desfazer(self):
        self.db.marcar_prospeccao_nao_whatsapp(self.p.id, por="marcos")
        registro = self.db.listar_prospeccoes()[0]
        self.assertTrue(registro.nao_whatsapp)
        self.assertIsNotNone(registro.nao_whatsapp_em)
        self.assertEqual(registro.nao_whatsapp_por, "marcos")

        self.db.marcar_prospeccao_nao_whatsapp(self.p.id, por="marcos", valor=False)
        self.assertFalse(self.db.listar_prospeccoes()[0].nao_whatsapp)

    def test_nao_whatsapp_sobrevive_a_reimportacao_da_planilha(self):
        self.db.marcar_prospeccao_nao_whatsapp(self.p.id, por="marcos")
        self.db.importar_prospeccoes([_linha(petshop="Petshop M", telefone="5512999990020")])
        self.assertTrue(self.db.listar_prospeccoes()[0].nao_whatsapp)


class TesteProspeccaoPorTelefoneHash(unittest.TestCase):
    def test_existe(self):
        db = FakeDatabase()
        telefone = "5512999990007"
        db.criar_prospeccao(nome="Petshop Y", telefone=telefone)
        registro = db.prospeccao_por_telefone_hash(hash_telefone(telefone))
        self.assertIsNotNone(registro)
        self.assertEqual(registro.nome, "Petshop Y")

    def test_nao_existe_devolve_none(self):
        db = FakeDatabase()
        self.assertIsNone(db.prospeccao_por_telefone_hash(hash_telefone("5512999999999")))


class TesteGuardaDeArquitetura(unittest.TestCase):
    """Por `ast.parse` (mesmo padrão de `tests/test_summaries.py`,
    `tests/test_painel_api.py`): `camucrm/prospeccao.py` nunca importa
    `camucrm.llm`/`camucrm.transport` (requirement "Mensagem é template
    fixo, não geração por LLM" / "Disparo é link do WhatsApp, nunca envio
    pela API")."""

    def test_prospeccao_nao_importa_llm_nem_transport(self):
        caminho = CAMUCRM_DIR / "prospeccao.py"
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        proibidos = ("llm", "transport")
        for node in ast.walk(arvore):
            nomes = []
            if isinstance(node, ast.Import):
                nomes = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                nomes = [node.module or ""] + [alias.name for alias in node.names]
            for nome in nomes:
                partes = nome.split(".")
                for proibido in proibidos:
                    self.assertNotIn(
                        proibido, partes,
                        f"camucrm/prospeccao.py não deve importar {proibido!r}",
                    )


if __name__ == "__main__":
    unittest.main()
