"""Prova, contra Postgres real, que `pipeline.recalcular_lote` produz
EXATAMENTE o mesmo resultado que `pipeline.recalcular` chamado uma conversa
de cada vez (otimização de 2026-08-28, `Database.contexto_para_recalculo`).

Esta é a garantia mais importante desta otimização: ela existe só para
reduzir o número de idas ao banco, nunca para mudar o que o sistema decide.
Uma divergência aqui — o caminho em lote calculando um estágio ou temperatura
diferente do caminho de sempre — é exatamente o tipo de erro estrutural que
o projeto trata como o mais caro que existe (um estágio errado gravado no
banco), e um fake não pode provar isso: `tests/fakes.py::FakeDatabase.
contexto_para_recalculo` foi escrito para IMITAR o SQL real, mas só uma
comparação contra o SQL de verdade prova que a imitação está certa.

    make db-up
    CAMU_TEST_DSN=postgresql://camu:camu@localhost:5433/camucrm make test-db
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from camucrm.db import Database
from camucrm.pipeline import recalcular, recalcular_lote

DSN = os.getenv("CAMU_TEST_DSN", "").strip()


class CasoIntegracao(unittest.TestCase):
    """Mesma base de `test_teto_followup.py`: limpa o que criou."""

    rotulo = "teste-lote"

    @classmethod
    def setUpClass(cls):
        cls.db = Database(DSN)
        cls.db.ensure_schema()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        self._criados: list[int] = []
        self.addCleanup(self._limpar)

    def _nova_conversa(self, *, funil: str = "b2c"):
        contato = self.db.upsert_contato(
            f"5511{os.urandom(4).hex()}"[:15], nome=self.rotulo, tipo=funil
        )
        self._criados.append(contato.id)
        return self.db.get_or_create_conversa(contato.id, funil)

    def _limpar(self):
        if not self._criados:
            return
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM contatos WHERE id = ANY(%s)", (self._criados,)
                )


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteEquivalenciaComOCaminhoIndividual(CasoIntegracao):
    """Cada teste monta uma conversa com um tipo de dado diferente (fatos,
    trilha de eventos, follow-up, marco manual, recusa desconsiderada) e
    confere que `recalcular_lote([conversa])` bate, campo a campo, com
    `recalcular(conversa)` chamado sozinho — as duas ÚNICAS fontes de
    verdade que podem divergir aqui."""

    def _comparar(self, conversa, *, agora):
        individual = recalcular(self.db, conversa, agora=agora, persistir=False)
        em_lote = recalcular_lote(self.db, [conversa], agora=agora, persistir=False)[0]

        self.assertEqual(individual.estagio, em_lote.estagio)
        self.assertEqual(individual.temperatura, em_lote.temperatura)
        self.assertEqual(individual.classificacao.sinal, em_lote.classificacao.sinal)
        self.assertEqual(
            individual.sinais.bola_com, em_lote.sinais.bola_com,
            "bola_com divergiu entre o caminho individual e o em lote",
        )
        self.assertEqual(
            individual.sinais.estagio_maximo_alcancado,
            em_lote.sinais.estagio_maximo_alcancado,
        )
        self.assertEqual(
            (individual.transicao.para if individual.transicao else None),
            (em_lote.transicao.para if em_lote.transicao else None),
        )
        return individual

    def test_conversa_sem_nenhum_dado(self):
        """`ContextoConversa.vazio()` precisa se comportar como uma conversa
        nova de verdade — S0/P0, sem sinal nenhum."""
        conversa = self._nova_conversa()
        resultado = self._comparar(conversa, agora=datetime.now(timezone.utc))
        self.assertEqual(resultado.estagio, "S0")

    def test_conversa_com_fatos_e_mensagens(self):
        """Foto do pet + prévia + preço — cruza vários estágios na mesma
        passada (S1→S4), exercitando `fatos`/`mensagens` em lote."""
        conversa = self._nova_conversa()
        agora = datetime.now(timezone.utc)
        base = agora - timedelta(hours=6)
        self.db.registrar_mensagem(conversa.id, "in", "oi, aqui a foto do meu cachorro", base)
        self.db.registrar_mensagem(conversa.id, "out", "ficou linda a prévia!", base + timedelta(minutes=5))
        self.db.registrar_mensagem(conversa.id, "out", "fica R$ 150", base + timedelta(minutes=10))
        self.db.gravar_fatos(
            conversa.id,
            {"foto_pet_recebida": True, "previa_enviada": True, "preco_apresentado": True},
            {
                "foto_pet_recebida": "aqui a foto do meu cachorro",
                "previa_enviada": "ficou linda a prévia",
                "preco_apresentado": "fica R$ 150",
            },
            extraido_em=agora,
        )
        resultado = self._comparar(conversa, agora=agora)
        self.assertEqual(resultado.estagio, "S4")

    def test_conversa_com_trilha_de_eventos_e_reabertura(self):
        """Vários eventos de estágio, incluindo um terminal e uma reabertura
        — exercita `estagio_corrente`/`estagio_maximo_alcancado`/
        `ultimo_avanco_em`/`ultimo_avanco_causada_por` em lote, que dependem
        de ORDEM (por id E por `em`), não só de presença."""
        conversa = self._nova_conversa()
        agora = datetime.now(timezone.utc)
        self.db.registrar_mensagem(conversa.id, "in", "oi", agora - timedelta(hours=1))
        self.db.gravar_evento_estagio(conversa.id, "S0", "S1", em=agora - timedelta(days=10))
        self.db.gravar_evento_estagio(conversa.id, "S1", "S2", em=agora - timedelta(days=9))
        self.db.gravar_evento_estagio(
            conversa.id, "S2", "SX", em=agora - timedelta(days=8), motivo="14 dias sem resposta",
        )
        self.db.atualizar_estado_conversa(conversa.id, estagio="SX")
        resultado = self._comparar(conversa, agora=agora)
        # A conversa reabre (bola virou do cliente) no maior estágio já
        # alcançado — S2, não S1 nem um estágio novo derivado do zero.
        self.assertEqual(resultado.estagio, "S2")

    def test_conversa_com_followup_e_recusa_desconsiderada(self):
        """Um follow-up enviado + uma recusa explícita desconsiderada por
        correção humana — exercita `ultimo_followup_em` e
        `recusa_desconsiderada` em lote."""
        conversa = self._nova_conversa()
        agora = datetime.now(timezone.utc)
        self.db.registrar_mensagem(conversa.id, "in", "oi, adorei a prévia", agora - timedelta(days=4))
        self.db.registrar_mensagem(conversa.id, "in", "não quero, obrigado", agora - timedelta(days=3))
        self.db.gravar_fatos(
            conversa.id, {"recusa_explicita": True},
            {"recusa_explicita": "não quero, obrigado"}, extraido_em=agora - timedelta(days=3),
        )
        # Precisa de um estágio não-terminal registrado ANTES do terminal —
        # `reabrir()` reabre no maior estágio já alcançado, e sem nenhum
        # evento não-terminal esse máximo é `None` (nada para reabrir).
        self.db.gravar_evento_estagio(conversa.id, "S0", "S1", em=agora - timedelta(days=4))
        self.db.gravar_evento_estagio(conversa.id, "S1", "SX", em=agora - timedelta(days=3))
        self.db.atualizar_estado_conversa(conversa.id, estagio="SX")
        self.db.registrar_followup(conversa.id, "oi, tudo bem?")
        self.db.registrar_correcao(
            conversa.id, "recusa_explicita", "true", "desconsiderado", por="teste",
        )
        resultado = self._comparar(conversa, agora=agora)
        # Recusa desconsiderada + bola com a Camu (só há mensagem inbound,
        # nenhuma outbound — "fomos nós que abrimos e estamos esperando" não
        # se aplica; aqui é "o cliente falou, ainda devemos resposta")
        # reabre no maior estágio não-terminal já alcançado: S1.
        self.assertEqual(resultado.estagio, "S1")

    def test_conversa_b2b_com_marco_manual(self):
        """Marco manual (consignação assinada) — exercita `marcos`/`marco_em`
        em lote, incluindo o timestamp que carimba o evento de P5."""
        conversa = self._nova_conversa(funil="b2b")
        agora = datetime.now(timezone.utc)
        self.db.registrar_mensagem(conversa.id, "out", "oi, posso te visitar?", agora - timedelta(days=2))
        self.db.registrar_mensagem(conversa.id, "in", "pode sim", agora - timedelta(days=2, hours=-1))
        self.db.registrar_marco(conversa.id, "consignacao_assinada", por="teste")
        resultado = self._comparar(conversa, agora=agora)
        self.assertEqual(resultado.estagio, "P5")

    def test_multiplas_conversas_no_mesmo_lote_nao_se_misturam(self):
        """A prova mais direta contra o risco desta otimização: um lote com
        VÁRIAS conversas de dados bem diferentes precisa devolver, para
        CADA uma, o mesmo resultado que ela teria sozinha — nunca os dados
        de uma vazando para o cálculo de outra."""
        agora = datetime.now(timezone.utc)

        c1 = self._nova_conversa()
        self.db.registrar_mensagem(c1.id, "in", "aqui a foto", agora - timedelta(hours=1))
        self.db.gravar_fatos(
            c1.id, {"foto_pet_recebida": True}, {"foto_pet_recebida": "aqui a foto"}, extraido_em=agora,
        )

        c2 = self._nova_conversa(funil="b2b")
        self.db.registrar_mensagem(c2.id, "out", "posso mandar uma foto?", agora - timedelta(days=1))
        self.db.registrar_marco(c2.id, "primeira_reposicao", por="teste")

        c3 = self._nova_conversa()  # sem nenhum dado

        individuais = {
            c.id: recalcular(self.db, c, agora=agora, persistir=False) for c in (c1, c2, c3)
        }
        em_lote = {
            e.conversa_id: e
            for e in recalcular_lote(self.db, [c1, c2, c3], agora=agora, persistir=False)
        }

        for cid in individuais:
            with self.subTest(conversa_id=cid):
                self.assertEqual(individuais[cid].estagio, em_lote[cid].estagio)
                self.assertEqual(individuais[cid].temperatura, em_lote[cid].temperatura)

        self.assertEqual(em_lote[c1.id].estagio, "S2")
        self.assertEqual(em_lote[c2.id].estagio, "P6")
        self.assertEqual(em_lote[c3.id].estagio, "S0")


if __name__ == "__main__":
    unittest.main()
