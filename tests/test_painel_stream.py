"""Tempo real do painel (change `painel-tempo-real`): poller único, `Event`
compartilhado, heartbeat e reconexão sem perder mensagem.

Nada de `time.sleep`/`asyncio.sleep` real aqui — `PollerMudanca.verificar_uma_vez`
é chamado diretamente (sem o laço com `sleep` de `_ciclo`), e o heartbeat é
testado com um `aguardar_mudanca` de `timeout` explícito, nunca esperando o
relógio de verdade. Isso mantém a suíte instantânea (CLAUDE.md: `make test`
sem rede e sem Postgres, e aqui, sem tempo real).
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from camucrm.painel.stream import PollerMudanca, gerador_sse
from tests.fakes import FakeDatabase


class TokenSequencial:
    """Stub de "comparação pura, sem side effect": uma lista pré-definida de
    tokens, um consumido por chamada. Não fala com banco nenhum — é só o
    contrato que `PollerMudanca` espera de `obter_token`.
    """

    def __init__(self, tokens: list[str]):
        self._tokens = list(tokens)
        self.chamadas = 0

    def __call__(self) -> str:
        indice = min(self.chamadas, len(self._tokens) - 1)
        self.chamadas += 1
        return self._tokens[indice]


class TesteTokenComparacaoPura(unittest.TestCase):
    """Requirement "Token de mudança como cursor": o token muda com mensagem,
    evento de estágio ou toque em conversa — e só nesses casos.
    """

    def test_token_nao_muda_sem_alteracao(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        db.registrar_mensagem(conversa.id, "in", "oi")
        antes = db.token_de_mudanca()
        depois = db.token_de_mudanca()
        self.assertEqual(antes, depois)

    def test_token_muda_com_mensagem_nova(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        antes = db.token_de_mudanca()
        db.registrar_mensagem(conversa.id, "in", "oi")
        depois = db.token_de_mudanca()
        self.assertNotEqual(antes, depois)

    def test_token_muda_com_evento_de_estagio(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        antes = db.token_de_mudanca()
        db.gravar_evento_estagio(conversa.id, "S0", "S1")
        depois = db.token_de_mudanca()
        self.assertNotEqual(antes, depois)

    def test_token_muda_com_atualizacao_de_conversa(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        antes = db.token_de_mudanca()
        db.atualizar_estado_conversa(conversa.id, resultado="ganho")
        depois = db.token_de_mudanca()
        self.assertNotEqual(antes, depois)

    def test_marca_de_prospeccao_mexe_so_na_quarta_parte(self):
        """Change `prospeccao-tempo-real-sem-pulo`: triar uma linha da
        prospecção move a 4ª parte do token e SÓ ela — as três primeiras
        (mensagem, evento, conversa) ficam iguais, para o cliente na aba de
        prospecção reagir sem que a fila/kanban de outro operador redesenhe."""
        db = FakeDatabase()
        p = db.criar_prospeccao(nome="Pet X", telefone="5512999990000")
        antes = db.token_de_mudanca()

        db.marcar_prospeccao_nao_whatsapp(p.id, por="op")
        depois = db.token_de_mudanca()

        self.assertNotEqual(antes, depois)
        self.assertEqual(antes.split(":")[:3], depois.split(":")[:3])
        self.assertNotEqual(antes.split(":")[3], depois.split(":")[3])

    def test_mensagem_nova_nao_mexe_na_parte_de_prospeccao(self):
        db = FakeDatabase()
        conversa = db.criar_conversa()
        antes = db.token_de_mudanca()
        db.registrar_mensagem(conversa.id, "in", "oi")
        depois = db.token_de_mudanca()
        self.assertEqual(antes.split(":")[3], depois.split(":")[3])


class TestePollerUnico(unittest.TestCase):
    """Requirement "Poller único por processo": um `PollerMudanca`, quantos
    geradores quiserem aguardar — a consulta de token só acontece quando
    `verificar_uma_vez` é chamado, nunca por conta de quem está esperando.
    """

    def test_so_dispara_evento_quando_token_muda(self):
        async def cenario():
            tokens = TokenSequencial(["1:0:0", "1:0:0", "2:0:0"])
            poller = PollerMudanca(tokens)

            mudou_1 = await poller.verificar_uma_vez()
            mudou_2 = await poller.verificar_uma_vez()
            mudou_3 = await poller.verificar_uma_vez()

            self.assertTrue(mudou_1)  # None -> "1:0:0"
            self.assertFalse(mudou_2)  # "1:0:0" -> "1:0:0"
            self.assertTrue(mudou_3)  # "1:0:0" -> "2:0:0"
            self.assertEqual(poller.token_atual, "2:0:0")

        asyncio.run(cenario())

    def test_n_geradores_geram_uma_consulta_por_ciclo(self):
        """N "clientes" aguardando o mesmo poller não multiplicam a consulta
        de token — só `verificar_uma_vez` consulta, e é chamado uma vez."""

        async def cenario():
            tokens = TokenSequencial(["1:0:0", "2:0:0"])
            poller = PollerMudanca(tokens)
            await poller.verificar_uma_vez()  # token inicial, "1:0:0"

            async def cliente_espera():
                return await poller.aguardar_mudanca(timeout=1.0)

            esperas = [asyncio.create_task(cliente_espera()) for _ in range(3)]
            await asyncio.sleep(0)  # cede o loop para os 3 entrarem em `wait()`

            mudou = await poller.verificar_uma_vez()  # única consulta do ciclo
            self.assertTrue(mudou)

            resultados = await asyncio.gather(*esperas)
            self.assertEqual(resultados, [True, True, True])
            self.assertEqual(tokens.chamadas, 2)  # 1 consulta por verificação, não por cliente

        asyncio.run(cenario())

    def test_evento_novo_nao_reacorda_quem_ja_foi_notificado(self):
        """Depois do broadcast, o `Event` é trocado — outro `verificar_uma_vez`
        sem mudança não deve reacordar quem já recebeu a notificação anterior."""

        async def cenario():
            tokens = TokenSequencial(["1:0:0"])
            poller = PollerMudanca(tokens)
            await poller.verificar_uma_vez()

            primeira_espera = poller.aguardar_mudanca(timeout=0.5)
            resultado = await primeira_espera
            self.assertFalse(resultado)  # nenhuma mudança nova, só o timeout

        asyncio.run(cenario())


class TesteHeartbeat(unittest.TestCase):
    """Requirement de manter a conexão viva: sem mudança dentro do prazo, o
    gerador emite `: ping`, no intervalo certo — testado com `timeout`
    explícito, nunca esperando o relógio de verdade.
    """

    def test_aguardar_mudanca_estoura_timeout_sem_mudanca(self):
        async def cenario():
            tokens = TokenSequencial(["1:0:0"])
            poller = PollerMudanca(tokens)
            await poller.verificar_uma_vez()

            mudou = await poller.aguardar_mudanca(timeout=0.01)
            self.assertFalse(mudou)

        asyncio.run(cenario())

    def test_gerador_emite_ping_quando_poller_nao_muda(self):
        async def cenario():
            db = FakeDatabase()

            class PollerFalsoSemMudanca:
                token_atual = "1:0:0"

                async def aguardar_mudanca(self, timeout=20.0):
                    return False

            gerador = gerador_sse(db, PollerFalsoSemMudanca(), desde_id=None)
            primeiro = await gerador.__anext__()
            self.assertEqual(primeiro, ": ping\n\n")
            await gerador.aclose()

        asyncio.run(cenario())

    def test_gerador_emite_mudanca_quando_poller_dispara(self):
        async def cenario():
            db = FakeDatabase()  # sem desde_id, conexão nova: nada a recuperar

            class PollerFalsoComMudanca:
                token_atual = "1:0:0"
                _chamou = False

                async def aguardar_mudanca(self, timeout=20.0):
                    if self._chamou:
                        return False
                    self._chamou = True
                    return True

            gerador = gerador_sse(db, PollerFalsoComMudanca(), desde_id=None)
            primeiro = await gerador.__anext__()  # "mudanca" do poller disparando
            self.assertIn("event: mudanca", primeiro)
            await gerador.aclose()

        asyncio.run(cenario())


class TesteReconexaoSemPerderMensagem(unittest.TestCase):
    """Requirement "Token de mudança como cursor", cenário "Reconexão com
    desde_id não perde eventos": o gerador entrega, antes de qualquer coisa,
    tudo que já existia depois de `desde_id`.
    """

    def test_desde_id_entrega_mensagens_perdidas_antes_do_tempo_real(self):
        async def cenario():
            db = FakeDatabase()
            conversa = db.criar_conversa()
            id1 = db.registrar_mensagem(conversa.id, "in", "primeira")
            id2 = db.registrar_mensagem(conversa.id, "out", "segunda")
            id3 = db.registrar_mensagem(conversa.id, "in", "terceira")

            class PollerFalsoSemMudanca:
                token_atual = db.token_de_mudanca()

                async def aguardar_mudanca(self, timeout=20.0):
                    return False

            gerador = gerador_sse(db, PollerFalsoSemMudanca(), desde_id=id1)

            primeira = await gerador.__anext__()
            segunda = await gerador.__anext__()
            self.assertIn("segunda", primeira)
            self.assertIn("terceira", segunda)
            self.assertIn(f"id: {id2}", primeira)
            self.assertIn(f"id: {id3}", segunda)

            terceira = await gerador.__anext__()  # nada mais pendente -> heartbeat
            self.assertEqual(terceira, ": ping\n\n")
            await gerador.aclose()

        asyncio.run(cenario())

    def test_sem_desde_id_nao_reenvia_historico(self):
        """Conexão nova (sem cursor de reconexão) não é um backfill: mensagem
        já existente antes de conectar não é reenviada."""

        async def cenario():
            db = FakeDatabase()
            conversa = db.criar_conversa()
            db.registrar_mensagem(conversa.id, "in", "antiga")

            class PollerFalsoSemMudanca:
                token_atual = db.token_de_mudanca()

                async def aguardar_mudanca(self, timeout=20.0):
                    return False

            gerador = gerador_sse(db, PollerFalsoSemMudanca(), desde_id=None)
            primeiro = await gerador.__anext__()
            self.assertEqual(primeiro, ": ping\n\n")
            self.assertNotIn("antiga", primeiro)
            await gerador.aclose()

        asyncio.run(cenario())


if __name__ == "__main__":
    unittest.main()
