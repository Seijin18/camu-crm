"""Change `ingestao-a-prova-de-falha`: o que só um Postgres real prova —
transação única da cadeia de ingestão, staging de eventos brutos e a
retenção que nunca apaga falha pendente.

    make db-up
    CAMU_TEST_DSN=postgresql://camu:camu@localhost:5433/camucrm make test-db
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from camucrm.db import Database
from camucrm.ingest import ingerir
from camucrm.transport.evolution import EvolutionTransporte

DSN = os.getenv("CAMU_TEST_DSN", "").strip()


class CasoIntegracao(unittest.TestCase):
    rotulo = "teste-ingestao-a-prova-de-falha"

    @classmethod
    def setUpClass(cls):
        cls.db = Database(DSN)
        cls.db.ensure_schema()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        self._contatos_criados: list[int] = []
        self._eventos_brutos_criados: list[int] = []
        self.addCleanup(self._limpar)

    def _novo_telefone(self) -> str:
        # Só dígitos: `EvolutionTransporte._so_digitos` descarta qualquer
        # outro caractere, e `os.urandom(...).hex()` inclui a-f.
        return f"5511{int.from_bytes(os.urandom(4), 'big') % 100000000:08d}"

    def _limpar(self):
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                if self._contatos_criados:
                    cur.execute(
                        "DELETE FROM contatos WHERE id = ANY(%s)",
                        (self._contatos_criados,),
                    )
                if self._eventos_brutos_criados:
                    cur.execute(
                        "DELETE FROM eventos_recebidos_bruto WHERE id = ANY(%s)",
                        (self._eventos_brutos_criados,),
                    )


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteCadeiaDeIngestaoETransacional(CasoIntegracao):
    """spec.md: "Cadeia de ingestão é transacional"."""

    def test_falha_no_meio_da_cadeia_nao_deixa_contato_ou_conversa_orfaos(self):
        telefone = self._novo_telefone()
        contato_id_gerado = None

        with self.assertRaises(ValueError):
            with self.db.transacao() as conn:
                contato = self.db.upsert_contato(
                    telefone, nome=self.rotulo, tipo="b2c", conn=conn
                )
                contato_id_gerado = contato.id
                conversa = self.db.get_or_create_conversa(contato.id, conn=conn)
                # `direcao` inválida levanta ValueError ANTES de tocar o
                # banco nesta chamada — mas já dentro da MESMA transação que
                # abriu o contato e a conversa acima. Se a cadeia não fosse
                # transacional, contato e conversa já estariam commitados.
                self.db.registrar_mensagem(
                    conversa.id, "direcao-invalida", "oi", conn=conn
                )

        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM contatos WHERE id = %s",
                    (contato_id_gerado,),
                )
                self.assertEqual(
                    cur.fetchone()[0],
                    0,
                    "contato sobreviveu ao rollback — cadeia não é transacional",
                )

    def test_sucesso_no_meio_da_cadeia_persiste_as_tres_operacoes(self):
        telefone = self._novo_telefone()
        with self.db.transacao() as conn:
            contato = self.db.upsert_contato(
                telefone, nome=self.rotulo, tipo="b2c", conn=conn
            )
            self._contatos_criados.append(contato.id)
            conversa = self.db.get_or_create_conversa(contato.id, conn=conn)
            mensagem_id = self.db.registrar_mensagem(
                conversa.id, "in", "oi", conn=conn
            )
        self.assertIsNotNone(mensagem_id)
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM mensagens WHERE conversa_id = %s",
                    (conversa.id,),
                )
                self.assertEqual(cur.fetchone()[0], 1)


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteDedupeEstendidoContraPostgres(CasoIntegracao):
    """spec.md: "Evento sem externa_id ainda é protegido contra
    duplicação" — o índice único parcial `mensagens_externa_id_idx`
    (`WHERE externa_id IS NOT NULL`) já cobre o hash sintético que
    `ingest._externa_id_efetivo` calcula; nenhum índice novo foi
    necessário (design.md)."""

    def test_reentrega_de_evento_sem_key_id_nao_duplica_no_banco_real(self):
        telefone = self._novo_telefone()
        bruto = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": f"{telefone}@s.whatsapp.net", "fromMe": False},
                "message": {"conversation": "oi"},
                "messageTimestamp": int(datetime.now(timezone.utc).timestamp()),
                "pushName": self.rotulo,
            },
        }
        transporte = EvolutionTransporte()

        primeiro = ingerir(self.db, transporte.receber(bruto), origem="whatsapp")
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM contatos WHERE telefone = %s", (telefone,))
                self._contatos_criados.append(cur.fetchone()[0])
        segundo = ingerir(self.db, transporte.receber(bruto), origem="whatsapp")

        self.assertFalse(primeiro.duplicada)
        self.assertTrue(segundo.duplicada)
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM mensagens WHERE conversa_id = %s",
                    (primeiro.conversa_id,),
                )
                self.assertEqual(cur.fetchone()[0], 1)


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TesteStagingDeEventosBrutos(CasoIntegracao):
    """spec.md: "Payload bruto é preservado antes do processamento" e
    "Falha de ingestão deixa rastro reprocessável"."""

    def test_registrar_marcar_processado_e_listar_pendentes(self):
        payload = {"event": "messages.upsert", "data": {"key": {"id": "X1"}}}
        evento_id = self.db.registrar_evento_bruto(payload)
        self._eventos_brutos_criados.append(evento_id)

        pendentes = self.db.listar_eventos_brutos_pendentes()
        self.assertIn(evento_id, [p.id for p in pendentes])
        pendente = next(p for p in pendentes if p.id == evento_id)
        self.assertEqual(pendente.payload, payload)
        self.assertFalse(pendente.processado)

        self.db.marcar_evento_bruto_processado(evento_id)
        pendentes_depois = self.db.listar_eventos_brutos_pendentes()
        self.assertNotIn(evento_id, [p.id for p in pendentes_depois])

    def test_falha_registrada_permanece_pendente_com_erro(self):
        evento_id = self.db.registrar_evento_bruto({"event": "x"})
        self._eventos_brutos_criados.append(evento_id)

        self.db.marcar_evento_bruto_falhou(evento_id, "boom")

        pendentes = self.db.listar_eventos_brutos_pendentes()
        registro = next(p for p in pendentes if p.id == evento_id)
        self.assertFalse(registro.processado)
        self.assertEqual(registro.erro, "boom")
        self.assertEqual(registro.tentativas, 1)


@unittest.skipUnless(DSN, "defina CAMU_TEST_DSN para rodar contra Postgres real")
class TestePurgaDeEventosBrutos(CasoIntegracao):
    """spec.md: "Retenção da caixa de reprocessamento não apaga falha
    pendente"."""

    def _forcar_recebido_em(self, evento_id: int, quando: datetime) -> None:
        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE eventos_recebidos_bruto SET recebido_em = %s WHERE id = %s",
                    (quando, evento_id),
                )

    def test_purga_remove_so_processado_antigo(self):
        antigo = datetime.now(timezone.utc) - timedelta(days=30)

        processado_antigo = self.db.registrar_evento_bruto({"e": "processado-antigo"})
        self.db.marcar_evento_bruto_processado(processado_antigo)
        self._forcar_recebido_em(processado_antigo, antigo)

        processado_recente = self.db.registrar_evento_bruto({"e": "processado-recente"})
        self.db.marcar_evento_bruto_processado(processado_recente)

        falha_antiga = self.db.registrar_evento_bruto({"e": "falha-antiga"})
        self.db.marcar_evento_bruto_falhou(falha_antiga, "erro persistente")
        self._forcar_recebido_em(falha_antiga, antigo)

        for evento_id in (processado_recente, falha_antiga):
            self._eventos_brutos_criados.append(evento_id)

        apagados = self.db.purgar_eventos_brutos_antigos(dias=14)
        self.assertEqual(apagados, 1)

        with self.db._conn() as conn:  # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM eventos_recebidos_bruto WHERE id = ANY(%s)",
                    ([processado_antigo, processado_recente, falha_antiga],),
                )
                restantes = {row[0] for row in cur.fetchall()}
        self.assertNotIn(processado_antigo, restantes)
        self.assertIn(processado_recente, restantes)
        self.assertIn(
            falha_antiga, restantes, "falha pendente antiga não pode ser apagada"
        )


if __name__ == "__main__":
    unittest.main()
