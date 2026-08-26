"""`camucrm.painel.views` — funções puras, sem FastAPI e sem banco."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from camucrm.db import (
    Conversa,
    CorrecaoRegistro,
    EventoRegistro,
    FatoRegistro,
    FollowupRegistro,
    MarcoRegistro,
    ObjecaoRegistro,
)
from camucrm.painel import views
from camucrm.pipeline import EstadoConversa
from camucrm.rules.estagio import ORIGEM_BACKFILL, ORIGEM_LIVE
from camucrm.rules.fila import ItemFila
from camucrm.rules.sinais import SinaisConversa
from camucrm.rules.temperatura import Classificacao


def _conversa(**kwargs) -> Conversa:
    base = dict(
        id=1, contato_id=1, funil="b2c", estagio="S1", bola_com="camu",
        temperatura="quente", ultimo_inbound=None, ultimo_outbound=None,
        followups_enviados=0, resultado=None, ultima_mensagem_processada_id=None,
        nome_contato="Ana",
    )
    base.update(kwargs)
    return Conversa(**base)


def _estado(**kwargs) -> EstadoConversa:
    base = dict(
        conversa_id=1,
        estagio="S1",
        classificacao=Classificacao("quente", "bola com a Camu"),
        sinais=SinaisConversa(),
        transicao=None,
    )
    base.update(kwargs)
    return EstadoConversa(**base)


class TesteCardConversa(unittest.TestCase):
    def test_campos_essenciais(self):
        conversa = _conversa()
        estado = _estado()
        card = views.card_conversa(conversa, estado)
        self.assertEqual(card["id"], 1)
        self.assertEqual(card["nome"], "Ana")
        self.assertEqual(card["funil"], "b2c")
        self.assertEqual(card["estagio"], "S1")
        self.assertEqual(card["estagio_label"], "Respondeu")
        self.assertEqual(card["temperatura"], "quente")
        # `bola_com` vem de `sinais` (derivado), não de `conversa.bola_com`
        # (cache) — `SinaisConversa()` default não tem inbound, então a bola
        # está com o cliente.
        self.assertEqual(card["bola_com"], "cliente")
        self.assertEqual(card["followups_enviados"], 0)

    def test_sinal_vem_da_classificacao(self):
        """`Classificacao.sinal` é a primeira superfície a mostrar essa justificativa."""
        estado = _estado(classificacao=Classificacao("frio", "3.2 dias sem resposta"))
        card = views.card_conversa(_conversa(), estado)
        self.assertEqual(card["sinal"], "3.2 dias sem resposta")

    def test_sem_nome_usa_hashtag_id(self):
        card = views.card_conversa(_conversa(nome_contato=None), _estado())
        self.assertEqual(card["nome"], "#1")


class TesteColunasKanban(unittest.TestCase):
    def test_b2c_estagios_derivados_recusam_drop(self):
        colunas = {c["estagio"]: c for c in views.colunas_kanban("b2c")}
        for estagio in ("S0", "S1", "S2", "S3", "S4", "S5"):
            with self.subTest(estagio=estagio):
                self.assertFalse(colunas[estagio]["aceita_drop"])
                self.assertTrue(colunas[estagio]["derivada"])
                self.assertIn("§3", colunas[estagio]["motivo_recusa"])

    def test_b2c_manuais_e_terminal_aceitam_drop(self):
        colunas = {c["estagio"]: c for c in views.colunas_kanban("b2c")}
        for estagio in ("S6", "SX"):
            with self.subTest(estagio=estagio):
                self.assertTrue(colunas[estagio]["aceita_drop"])
                self.assertFalse(colunas[estagio]["derivada"])
                self.assertIsNone(colunas[estagio]["motivo_recusa"])

    def test_b2b_estagios_derivados_recusam_drop(self):
        colunas = {c["estagio"]: c for c in views.colunas_kanban("b2b")}
        for estagio in ("P0", "P1", "P2", "P3", "P4"):
            with self.subTest(estagio=estagio):
                self.assertFalse(colunas[estagio]["aceita_drop"])

    def test_b2b_manuais_e_terminal_aceitam_drop(self):
        colunas = {c["estagio"]: c for c in views.colunas_kanban("b2b")}
        for estagio in ("P5", "P6", "PX"):
            with self.subTest(estagio=estagio):
                self.assertTrue(colunas[estagio]["aceita_drop"])


class TesteMontarKanban(unittest.TestCase):
    def test_agrupa_cards_por_estagio_do_funil(self):
        cards = [
            {"id": 1, "funil": "b2c", "estagio": "S1"},
            {"id": 2, "funil": "b2c", "estagio": "S1"},
            {"id": 3, "funil": "b2c", "estagio": "S2"},
            {"id": 4, "funil": "b2b", "estagio": "P1"},
        ]
        kanban = views.montar_kanban(cards, "b2c")
        por_estagio = {c["estagio"]: c["cards"] for c in kanban["colunas"]}
        self.assertEqual(len(por_estagio["S1"]), 2)
        self.assertEqual(len(por_estagio["S2"]), 1)
        self.assertEqual(por_estagio["S6"], [])


class TesteFiltrarConversas(unittest.TestCase):
    def setUp(self):
        self.cards = [
            {"id": 1, "estagio": "S1", "temperatura": "quente", "bola_com": "camu"},
            {"id": 2, "estagio": "S2", "temperatura": "frio", "bola_com": "cliente"},
            {"id": 3, "estagio": "S1", "temperatura": "frio", "bola_com": "camu"},
        ]

    def test_filtra_por_estagio(self):
        self.assertEqual(
            {c["id"] for c in views.filtrar_conversas(self.cards, estagio="S1")}, {1, 3}
        )

    def test_filtra_por_temperatura(self):
        self.assertEqual(
            {c["id"] for c in views.filtrar_conversas(self.cards, temperatura="frio")}, {2, 3}
        )

    def test_filtra_por_bola(self):
        self.assertEqual(
            {c["id"] for c in views.filtrar_conversas(self.cards, bola="cliente")}, {2}
        )

    def test_filtra_por_ids_com_objecao(self):
        self.assertEqual(
            {c["id"] for c in views.filtrar_conversas(self.cards, ids_com_objecao={1, 2})},
            {1, 2},
        )

    def test_sem_filtro_devolve_tudo(self):
        self.assertEqual(len(views.filtrar_conversas(self.cards)), 3)

    def test_filtros_combinam(self):
        resultado = views.filtrar_conversas(self.cards, estagio="S1", temperatura="frio")
        self.assertEqual([c["id"] for c in resultado], [3])


class TesteOrdenarConversas(unittest.TestCase):
    def setUp(self):
        self.cards = [
            {"id": 1, "nome": "carlos", "estagio": "S2", "temperatura": "frio", "horas_esperando": 10.0},
            {"id": 2, "nome": "ana", "estagio": "S1", "temperatura": "quente", "horas_esperando": 40.0},
            {"id": 3, "nome": "bruno", "estagio": "S5", "temperatura": "morno", "horas_esperando": None},
        ]

    def test_ordena_por_nome_asc(self):
        ordenado = views.ordenar_conversas(self.cards, campo="nome", direcao="asc")
        self.assertEqual([c["id"] for c in ordenado], [2, 3, 1])

    def test_ordena_por_estagio_desc(self):
        ordenado = views.ordenar_conversas(self.cards, campo="estagio", direcao="desc")
        self.assertEqual([c["id"] for c in ordenado], [3, 1, 2])

    def test_ordena_por_temperatura_asc(self):
        # TEMPERATURAS = (QUENTE, MORNO, ESFRIANDO, FRIO, ENCERRADO)
        ordenado = views.ordenar_conversas(self.cards, campo="temperatura", direcao="asc")
        self.assertEqual([c["id"] for c in ordenado], [2, 3, 1])

    def test_ordena_por_horas_esperando_desc_none_por_ultimo(self):
        ordenado = views.ordenar_conversas(self.cards, campo="horas_esperando", direcao="desc")
        self.assertEqual([c["id"] for c in ordenado], [2, 1, 3])

    def test_direcao_invalida_levanta(self):
        with self.assertRaises(ValueError):
            views.ordenar_conversas(self.cards, campo="nome", direcao="lateral")

    def test_campo_invalido_levanta(self):
        with self.assertRaises(ValueError):
            views.ordenar_conversas(self.cards, campo="cor_favorita")


class TestePaginar(unittest.TestCase):
    def test_pagina_com_limite_e_offset(self):
        cards = [{"id": i} for i in range(25)]
        pagina = views.paginar(cards, limite=10, offset=10)
        self.assertEqual([c["id"] for c in pagina], list(range(10, 20)))


class TesteDetalheConversa(unittest.TestCase):
    def test_fato_aparece_com_evidencia(self):
        agora = datetime.now(timezone.utc)
        detalhe = views.detalhe_conversa(
            _conversa(), _estado(),
            fatos=[FatoRegistro("foto_pet_recebida", True, "manda a foto do bichinho", agora, agora)],
            eventos=[], objecoes=[], followups=[], marcos=[], correcoes=[], contato=None,
        )
        self.assertEqual(len(detalhe["fatos"]), 1)
        self.assertEqual(detalhe["fatos"][0]["evidencia"], "manda a foto do bichinho")

    def test_evento_backfill_carrega_aviso(self):
        agora = datetime.now(timezone.utc)
        detalhe = views.detalhe_conversa(
            _conversa(), _estado(),
            fatos=[],
            eventos=[EventoRegistro(None, "S1", agora, ORIGEM_BACKFILL, "reconstruído")],
            objecoes=[], followups=[], marcos=[], correcoes=[], contato=None,
        )
        self.assertIn("§8", detalhe["eventos"][0]["aviso"])

    def test_evento_live_nao_carrega_aviso(self):
        agora = datetime.now(timezone.utc)
        detalhe = views.detalhe_conversa(
            _conversa(), _estado(),
            fatos=[],
            eventos=[EventoRegistro(None, "S1", agora, ORIGEM_LIVE, "mensagem")],
            objecoes=[], followups=[], marcos=[], correcoes=[], contato=None,
        )
        self.assertNotIn("aviso", detalhe["eventos"][0])

    def test_nunca_inclui_telefone(self):
        """§12: o painel jamais devolve telefone em claro."""
        from camucrm.db import ContatoResumido

        contato = ContatoResumido(1, "Ana", "b2c", True, datetime.now(timezone.utc))
        detalhe = views.detalhe_conversa(
            _conversa(), _estado(), fatos=[], eventos=[], objecoes=[], followups=[],
            marcos=[], correcoes=[], contato=contato,
        )
        self.assertNotIn("telefone", detalhe["contato"])
        self.assertTrue(detalhe["contato"]["tem_telefone"])

    def test_objecoes_correcoes_marcos_followups_presentes(self):
        agora = datetime.now(timezone.utc)
        detalhe = views.detalhe_conversa(
            _conversa(), _estado(),
            fatos=[],
            eventos=[],
            objecoes=[ObjecaoRegistro(1, "preco", "S4", "achei caro", agora)],
            followups=[FollowupRegistro(1, "oi de novo", agora)],
            marcos=[MarcoRegistro("ganho", agora, "Marcos")],
            correcoes=[CorrecaoRegistro(1, "estagio", "S3", "S4", agora, "Marcos")],
            contato=None,
        )
        self.assertEqual(detalhe["objecoes"][0]["categoria"], "preco")
        self.assertEqual(detalhe["followups"][0]["numero"], 1)
        self.assertEqual(detalhe["marcos"][0]["marco"], "ganho")
        self.assertEqual(detalhe["correcoes"][0]["campo"], "estagio")


class TesteSerializarMensagens(unittest.TestCase):
    def test_eco_desde_id_e_lista(self):
        from camucrm.db import MensagemRegistro

        agora = datetime.now(timezone.utc)
        pacote = views.serializar_mensagens(
            [MensagemRegistro(5, 1, "in", "oi", agora)], desde_id=4
        )
        self.assertEqual(pacote["desde_id"], 4)
        self.assertEqual(pacote["mensagens"][0]["id"], 5)
        self.assertEqual(pacote["mensagens"][0]["texto"], "oi")


class TesteItemFilaParaJson(unittest.TestCase):
    def test_shape(self):
        item = ItemFila(
            conversa_id=9, nome="Ana", funil="b2c", estagio="S1", temperatura="quente",
            prioridade=1, acao="Responder agora", motivo="bola com a Camu",
            horas_esperando=2.5,
        )
        payload = views.item_fila_para_json(item)
        self.assertEqual(payload["conversa_id"], 9)
        self.assertEqual(payload["estagio_label"], "Respondeu")
        self.assertEqual(payload["horas_esperando"], 2.5)


class TesteErro(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(views.erro("x", "§3"), {"erro": "x", "regra": "§3"})

    def test_regra_pode_ser_none(self):
        self.assertEqual(views.erro("x", None), {"erro": "x", "regra": None})


if __name__ == "__main__":
    unittest.main()
