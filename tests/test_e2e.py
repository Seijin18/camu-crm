"""Teste end-to-end único do ciclo completo.

    mensagem -> extração (LLM) -> fatos -> estágio -> temperatura -> fila -> rascunho

Change `resumo-conversa` estende este arquivo com `TesteResumoNaoMudaEstado`:
gerar um resumo (terceira superfície de LLM, `camucrm/summaries.py`) é um
apêndice de leitura sobre o mesmo ciclo, não um ramo novo — a prova de que
`resumos_conversa` é FOLHA do grafo (§1 do CLAUDE.md) é rodar o ciclo, gerar
o resumo, rodar de novo, e checar que nada mudou.

Convenção do repositório (herdada do WhatBot): toda mudança que altera esse
ciclo **estende este arquivo**, nunca duplica um E2E paralelo. Um segundo
arquivo E2E sempre acaba testando uma versão diferente do mesmo caminho, e a
divergência entre os dois passa despercebida até produção.

Sem rede e sem Postgres: `FakeLlm` e `FakeDatabase`.
"""

import json
import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeDatabase  # noqa: E402

from camucrm import acoes  # noqa: E402
from camucrm.drafts import gerar as gerar_rascunho  # noqa: E402
from camucrm.extraction.extractor import Extrator  # noqa: E402
from camucrm.llm import FakeLlm, LlmIndisponivelError  # noqa: E402
from camucrm.pipeline import recalcular  # noqa: E402
from camucrm.rules.fila import Candidato, montar_fila  # noqa: E402
from camucrm.summaries import ContextoResumo, PROMPT_VERSAO_RESUMO  # noqa: E402
from camucrm.summaries import gerar as gerar_resumo  # noqa: E402
from camucrm.taxonomia import ESFRIANDO, QUENTE  # noqa: E402

AGORA = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def resposta(**campos):
    payload = {"objecao": None, "evidencias": {}}
    payload.update(campos)
    return json.dumps(payload)


class TesteCicloB2C(unittest.TestCase):
    def setUp(self):
        self.db = FakeDatabase()
        self.conversa = self.db.criar_conversa(nome="Ana")

    def _mensagens(self):
        self.db.registrar_mensagem(
            self.conversa.id, "in", "oi, vi o insta de voces", AGORA - timedelta(hours=6)
        )
        self.db.registrar_mensagem(
            self.conversa.id, "out", "Oi! Me manda uma foto do seu pet?",
            AGORA - timedelta(hours=5),
        )
        self.db.registrar_mensagem(
            self.conversa.id, "in", "aqui ele, o nome dele e Thor",
            AGORA - timedelta(hours=4),
        )

    def test_ciclo_completo_ate_a_fila(self):
        self._mensagens()
        llm = FakeLlm([
            resposta(
                foto_pet_recebida=True,
                evidencias={"foto_pet_recebida": "aqui ele, o nome dele e Thor"},
            )
        ])
        extrator = Extrator(self.db, llm)
        resultado = extrator.processar_conversa(self.conversa.id, agora=AGORA)

        # Fato extraído com evidência literal.
        self.assertEqual(resultado.mensagens_processadas, 3)
        self.assertTrue(self.db.fatos_da_conversa(self.conversa.id)["foto_pet_recebida"])

        # Estágio derivado por regra, não pelo LLM.
        self.assertEqual(resultado.estado.estagio, "S2")
        self.assertEqual(resultado.estado.temperatura, QUENTE)

        # Evento de estágio gravado com origem ao vivo.
        eventos = [e for e in self.db.eventos if e["conversa_id"] == self.conversa.id]
        self.assertEqual(eventos[-1]["para"], "S2")
        self.assertEqual(eventos[-1]["origem"], "live")

        # E a conversa entra na fila com prioridade 1 (bola com a Camu).
        fila = montar_fila([
            Candidato(self.conversa.id, "Ana", "b2c", resultado.estado.estagio,
                      resultado.estado.classificacao, resultado.estado.sinais)
        ])
        self.assertEqual(fila[0].prioridade, 1)

    def test_reprocessar_e_idempotente(self):
        """§2: reprocessar não duplica evento nem regride estágio."""
        self._mensagens()
        extracao = resposta(
            foto_pet_recebida=True,
            evidencias={"foto_pet_recebida": "aqui ele, o nome dele e Thor"},
        )
        extrator = Extrator(self.db, FakeLlm([extracao, extracao]))
        extrator.processar_conversa(self.conversa.id, agora=AGORA)
        eventos_apos_primeira = len(self.db.eventos)
        fatos_apos_primeira = len(self.db.fatos)

        extrator.processar_conversa(self.conversa.id, agora=AGORA, forcar=True)
        self.assertEqual(len(self.db.eventos), eventos_apos_primeira)
        self.assertEqual(len(self.db.fatos), fatos_apos_primeira)

    def test_bloco_novo_avanca_e_registra_objecao(self):
        self._mensagens()
        extrator = Extrator(self.db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui ele, o nome dele e Thor"}),
            resposta(preco_apresentado=True, objecao="frete",
                     evidencias={"preco_apresentado": "a peca sai R$ 149 e o frete R$ 28",
                                 "objecao": "o frete ficou salgado"}),
        ]))
        extrator.processar_conversa(self.conversa.id, agora=AGORA)

        self.db.registrar_mensagem(
            self.conversa.id, "out", "a peca sai R$ 149 e o frete R$ 28",
            AGORA - timedelta(hours=3),
        )
        self.db.registrar_mensagem(
            self.conversa.id, "in", "o frete ficou salgado", AGORA - timedelta(hours=2)
        )
        resultado = extrator.processar_conversa(self.conversa.id, agora=AGORA)

        # S5, não S4: o cliente respondeu ao preço sem recusar (§3). O momento
        # do fato é o da mensagem que o evidencia, então a resposta posterior
        # caracteriza negociação já nesta passada.
        self.assertEqual(resultado.estado.estagio, "S5")
        self.assertEqual(self.db.objecoes[-1]["categoria"], "frete")
        # A objeção guarda o estágio em que apareceu (§4).
        self.assertEqual(self.db.objecoes[-1]["estagio"], "S2")

    def test_fato_sem_evidencia_nao_avanca_estagio(self):
        """A trava de §7: falso positivo de avanço = 0."""
        self._mensagens()
        extrator = Extrator(self.db, FakeLlm([
            resposta(foto_pet_recebida=True, previa_enviada=True,
                     evidencias={"foto_pet_recebida": "aqui ele, o nome dele e Thor"})
        ]))
        resultado = extrator.processar_conversa(self.conversa.id, agora=AGORA)
        self.assertEqual(resultado.estado.estagio, "S2")
        self.assertEqual(resultado.democoes[0].campo, "previa_enviada")

    def test_llm_indisponivel_deixa_o_estagio_onde_estava(self):
        self._mensagens()
        # A falha é o objeto do teste; o log dela na saída da suíte só confunde.
        self.addCleanup(logging.disable, logging.NOTSET)
        logging.disable(logging.WARNING)

        class LlmQuebrado:
            nome = "quebrado"

            def completar(self, system, user, *, json_estrito=False):
                raise LlmIndisponivelError("cota esgotada")

        resultado = Extrator(self.db, LlmQuebrado()).processar_conversa(
            self.conversa.id, agora=AGORA
        )
        self.assertIsNotNone(resultado.erro)
        self.assertEqual(resultado.estado.estagio, "S1")  # avançou só pelo inbound
        self.assertIsNone(self.db.conversas[self.conversa.id].ultima_mensagem_processada_id)

    def test_sem_mensagem_nova_recalcula_e_esfria(self):
        """Esfriar é o que acontece quando nada acontece.

        Com a bola do lado do cliente: enquanto a bola está com a Camu, a
        conversa fica QUENTE por mais dias que passem — é dívida, não
        follow-up (§5, §6 prioridade 1), e envelhecer não a torna menos
        urgente.
        """
        self._mensagens()
        self.db.registrar_mensagem(
            self.conversa.id, "out", "Ficou pronto, olha so", AGORA - timedelta(hours=3)
        )
        extrator = Extrator(self.db, FakeLlm([resposta(), resposta()]))
        extrator.processar_conversa(self.conversa.id, agora=AGORA)

        depois = AGORA + timedelta(days=4)
        resultado = extrator.processar_conversa(self.conversa.id, agora=depois)
        self.assertEqual(resultado.mensagens_processadas, 0)
        self.assertEqual(resultado.estado.temperatura, ESFRIANDO)

    def test_bola_com_a_camu_permanece_quente_mesmo_dias_depois(self):
        self._mensagens()
        extrator = Extrator(self.db, FakeLlm([resposta()]))
        extrator.processar_conversa(self.conversa.id, agora=AGORA)
        resultado = extrator.processar_conversa(
            self.conversa.id, agora=AGORA + timedelta(days=4)
        )
        self.assertEqual(resultado.estado.temperatura, QUENTE)


class TesteTrilhaAoVivo(unittest.TestCase):
    """Um bloco pode cruzar vários estágios, e todos precisam virar evento.

    Sem isto, `S1→S2` e `S4→S6` — as métricas que §14 diz justificarem o
    sistema — ficam permanentemente "sem amostra", porque a conversa salta de
    S0 direto para o estágio final numa única extração.
    """

    def setUp(self):
        self.db = FakeDatabase()
        self.conversa = self.db.criar_conversa(nome="Ana")
        for direcao, texto, horas in (
            ("in", "oi, vi o insta de voces", 8),
            ("out", "Oi! Manda uma foto do seu pet?", 7),
            ("in", "aqui esta a foto do Thor", 6),
            ("out", "Ficou assim, olha so. A peca sai R$ 149 e o frete R$ 28", 4),
            ("in", "o frete ficou salgado", 2),
        ):
            self.db.registrar_mensagem(
                self.conversa.id, direcao, texto, AGORA - timedelta(hours=horas)
            )
        self.extracao = resposta(
            foto_pet_recebida=True, previa_enviada=True, preco_apresentado=True,
            objecao="frete",
            evidencias={
                "foto_pet_recebida": "aqui esta a foto do Thor",
                "previa_enviada": "Ficou assim, olha so",
                "preco_apresentado": "A peca sai R$ 149 e o frete R$ 28",
                "objecao": "o frete ficou salgado",
            },
        )

    def _eventos(self):
        return [e for e in self.db.eventos if e["conversa_id"] == self.conversa.id]

    def test_grava_um_evento_por_estagio_percorrido(self):
        resultado = Extrator(self.db, FakeLlm([self.extracao])).processar_conversa(
            self.conversa.id, agora=AGORA
        )
        self.assertEqual(resultado.estado.estagio, "S5")
        self.assertEqual(
            [e["para"] for e in self._eventos()], ["S1", "S2", "S3", "S4", "S5"]
        )

    def test_cada_evento_leva_o_momento_do_que_o_disparou(self):
        """Não o momento do processamento — senão o tempo por estágio dá zero."""
        Extrator(self.db, FakeLlm([self.extracao])).processar_conversa(
            self.conversa.id, agora=AGORA
        )
        por_estagio = {e["para"]: e["em"] for e in self._eventos()}
        self.assertEqual(por_estagio["S1"], AGORA - timedelta(hours=8))
        self.assertEqual(por_estagio["S2"], AGORA - timedelta(hours=6))
        self.assertEqual(por_estagio["S4"], AGORA - timedelta(hours=4))
        self.assertLess(por_estagio["S1"], por_estagio["S2"])
        self.assertTrue(all(m < AGORA for m in por_estagio.values()))

    def test_reprocessar_nao_duplica_a_trilha(self):
        extrator = Extrator(self.db, FakeLlm([self.extracao, self.extracao]))
        extrator.processar_conversa(self.conversa.id, agora=AGORA)
        antes = len(self._eventos())
        extrator.processar_conversa(self.conversa.id, agora=AGORA, forcar=True)
        self.assertEqual(len(self._eventos()), antes)


class TesteAvancoNaoEsquentaConversaVelha(unittest.TestCase):
    def test_extracao_em_lote_de_avanco_antigo_nao_deixa_quente(self):
        """§5 conta quando o avanço ACONTECEU, não quando foi percebido.

        Sem isso, rodar `make extrair` sobre a base atrasada deixaria tudo
        QUENTE e a fila do dia perderia o sentido.
        """
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Bruno")
        db.registrar_mensagem(conversa.id, "in", "segue foto da Mimi",
                              AGORA - timedelta(days=3, hours=2))
        db.registrar_mensagem(conversa.id, "out", "Que linda! Ja te mando a previa",
                              AGORA - timedelta(days=3))

        resultado = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "segue foto da Mimi"})
        ])).processar_conversa(conversa.id, agora=AGORA)

        self.assertEqual(resultado.estado.estagio, "S2")
        self.assertEqual(resultado.estado.temperatura, ESFRIANDO)

    def test_estado_devolvido_bate_com_o_recalculo_seguinte(self):
        """A conversa não pode oscilar entre duas temperaturas sozinha."""
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Bruno")
        db.registrar_mensagem(conversa.id, "in", "segue foto da Mimi",
                              AGORA - timedelta(days=3, hours=2))
        db.registrar_mensagem(conversa.id, "out", "ja te mando",
                              AGORA - timedelta(days=3))
        primeiro = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "segue foto da Mimi"})
        ])).processar_conversa(conversa.id, agora=AGORA)
        segundo = recalcular(db, db.get_conversa(conversa.id), agora=AGORA)
        self.assertEqual(primeiro.estado.temperatura, segundo.temperatura)
        self.assertEqual(primeiro.estado.estagio, segundo.estagio)


class TesteCicloB2B(unittest.TestCase):
    def test_petshop_autoriza_e_recebe_proposta(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(funil="b2b", estagio="P0", nome="PetX")
        db.registrar_mensagem(conversa.id, "out", "Posso mandar uma foto de uma peca?",
                              AGORA - timedelta(days=2))
        db.registrar_mensagem(conversa.id, "in", "pode mandar sim",
                              AGORA - timedelta(days=1, hours=20))

        extrator = Extrator(db, FakeLlm([
            resposta(autorizou_envio_material=True,
                     evidencias={"autorizou_envio_material": "pode mandar sim"})
        ]))
        resultado = extrator.processar_conversa(conversa.id, agora=AGORA)
        self.assertEqual(resultado.estado.estagio, "P2")

        db.registrar_mensagem(conversa.id, "out", "Segue. Trabalhamos com consignacao.",
                              AGORA - timedelta(days=1, hours=19))
        estado = recalcular(db, db.get_conversa(conversa.id), agora=AGORA)
        self.assertEqual(estado.estagio, "P3")


class TesteRascunhoNoFimDoCiclo(unittest.TestCase):
    def test_rascunho_usa_o_estagio_derivado(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")
        db.registrar_mensagem(conversa.id, "in", "oi", AGORA - timedelta(hours=1))
        estado = recalcular(db, conversa, agora=AGORA)

        rascunho = gerar_rascunho(
            FakeLlm([json.dumps({"opcoes": [
                "Manda uma foto do seu pet?\nTe mostro como fica.",
                "Consegue mandar uma foto dele?\nJá te envio a prévia.",
            ]})]),
            [("in", "oi")],
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            funil="b2c",
            followups_enviados=0,
        )
        self.assertEqual(len(rascunho.opcoes), 2)
        # S1: nenhuma opção pode abrir com preço.
        self.assertFalse(any("R$" in o for o in rascunho.opcoes))


class TesteCicloAteVinculoDoRascunho(unittest.TestCase):
    """Change `rascunho-registrado` (§10): estende o E2E único do ciclo —

        gera -> escolhe -> registra outbound -> reconcilia -> mensagem_id
        vinculado; inbound de resposta -> extração -> avanço de estágio, com
        o rascunho vinculado ao evento que veio DEPOIS dele.

    CLAUDE.md: "toda mudança que altera esse ciclo estende este arquivo,
    nunca duplica um E2E paralelo" — por isso mora aqui, não num arquivo
    `test_rascunhos_e2e.py` à parte.
    """

    def test_ciclo_ate_o_vinculo_do_rascunho(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")
        db.registrar_mensagem(
            conversa.id, "in", "oi, vi o insta de voces", AGORA - timedelta(hours=2)
        )
        estado = recalcular(db, conversa, agora=AGORA)
        self.assertEqual(estado.estagio, "S1")

        # Gera duas opções (LLM) e persiste — não descarta como antes deste
        # change.
        opcoes_geradas = (
            "Manda uma foto do seu pet?\nTe mostro como fica.",
            "Consegue mandar uma foto dele?\nJá te envio a prévia.",
        )
        rascunho = gerar_rascunho(
            FakeLlm([json.dumps({"opcoes": list(opcoes_geradas)})]),
            [("in", "oi, vi o insta de voces")],
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            funil="b2c",
            followups_enviados=0,
        )
        self.assertFalse(rascunho.encerrar)
        rascunho_id = db.gravar_rascunho(
            conversa.id,
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            funil="b2c",
            followups_enviados=0,
            opcoes=rascunho.opcoes,
            avisos=rascunho.avisos,
        )

        # Escolhe a opção 1, tal como veio (caminho de escolha humana).
        db.registrar_escolha_rascunho(rascunho_id, escolhida=1, por="Marcos")
        self.assertEqual(db.rascunho(rascunho_id).opcao_2, opcoes_geradas[1])  # não descartada

        # Registra a mensagem outbound de verdade e reconcilia pelo eco
        # (caminho 2, design.md) — texto exato após normalização.
        mensagem_out_id = db.registrar_mensagem(
            conversa.id, "out", opcoes_geradas[0], AGORA - timedelta(hours=1, minutes=50)
        )
        vinculado = acoes.reconciliar_rascunho(
            db, conversa.id, mensagem_out_id, opcoes_geradas[0]
        )
        self.assertEqual(vinculado, rascunho_id)
        self.assertEqual(db.rascunho(rascunho_id).mensagem_id, mensagem_out_id)

        # O cliente responde com a foto: extração avança S1 -> S2, num
        # evento que acontece DEPOIS do envio ao qual o rascunho já está
        # vinculado.
        db.registrar_mensagem(
            conversa.id, "in", "aqui ele, o nome dele e Thor", AGORA - timedelta(hours=1)
        )
        extrator = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui ele, o nome dele e Thor"})
        ]))
        resultado = extrator.processar_conversa(conversa.id, agora=AGORA)
        self.assertEqual(resultado.estado.estagio, "S2")

        evento_avanco = [
            e for e in db.eventos if e["conversa_id"] == conversa.id and e["para"] == "S2"
        ][0]
        mensagem_out = [
            m for m in db.listar_mensagens(conversa.id) if m.texto == opcoes_geradas[0]
        ][0]
        self.assertLess(mensagem_out.enviada_em, evento_avanco["em"])
        # O vínculo do rascunho sobreviveu ao recálculo de estágio.
        self.assertEqual(db.rascunho(rascunho_id).mensagem_id, mensagem_out_id)


if __name__ == "__main__":
    unittest.main()


class TesteEstagioSeRendeAoHistorico(unittest.TestCase):
    """`conversas.estagio` é cache; `eventos_estagio` é o que aconteceu.

    Sem isso, um estágio inflado (fato removido por correção humana, dado de
    teste apagado, extração revista) ficaria preso para sempre: a regra de
    não-regressão protege qualquer valor que esteja no cache, inclusive um
    errado para cima, e a conversa sairia da fila pela regra errada.
    """

    def test_cache_alto_demais_e_corrigido_pelo_historico(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")
        db.registrar_mensagem(conversa.id, "in", "oi", AGORA - timedelta(hours=2))
        db.gravar_evento_estagio(conversa.id, "S0", "S1", motivo="inbound")
        # Cache inflado sem nenhum evento ou fato que o sustente.
        conversa.estagio = "S4"

        estado = recalcular(db, db.get_conversa(conversa.id), agora=AGORA)
        self.assertEqual(estado.estagio, "S1")

    def test_sem_historico_o_cache_e_o_ponto_de_partida(self):
        """Conversa recém-criada ainda não tem evento nenhum."""
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana", estagio="S0")
        estado = recalcular(db, conversa, agora=AGORA, persistir=False)
        self.assertEqual(estado.estagio, "S0")

    def test_avanco_legitimo_continua_valendo(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")
        db.registrar_mensagem(conversa.id, "in", "aqui a foto", AGORA - timedelta(hours=2))
        db.gravar_fatos(conversa.id, {"foto_pet_recebida": True}, {"foto_pet_recebida": "aqui a foto"})
        estado = recalcular(db, db.get_conversa(conversa.id), agora=AGORA)
        self.assertEqual(estado.estagio, "S2")


RESUMO_JSON_OK = json.dumps({
    "resumo": "Ana mandou a foto do pet Thor e recebeu a prévia.\n"
              "Ainda não respondeu depois disso.",
    "proximo_passo": "Perguntar se ela viu a prévia enviada.",
})


class TesteResumoNaoMudaEstado(unittest.TestCase):
    """Change `resumo-conversa`: estende o E2E único do ciclo com o passo

        ... -> fila -> resumo (LLM, terceira superfície, `camucrm/summaries.py`)

    A prova que este teste faz é a mesma que sustenta a divergência de §1
    registrada no CLAUDE.md: `resumos_conversa` é FOLHA do grafo — gerar um
    resumo não pode mudar `(estagio, temperatura, fila)`. Mora aqui, não num
    `test_summaries_e2e.py` à parte, pela mesma regra que já vale para
    `TesteCicloAteVinculoDoRascunho`.
    """

    def _estado_e_fila(self, db, conversa):
        estado = recalcular(db, db.get_conversa(conversa.id), agora=AGORA)
        candidato = Candidato(
            conversa_id=conversa.id, nome="Ana", funil=conversa.funil,
            estagio=estado.estagio, classificacao=estado.classificacao,
            sinais=estado.sinais,
        )
        fila = montar_fila([candidato])
        return (estado.estagio, estado.temperatura, [i.conversa_id for i in fila])

    def test_ciclo_completo_com_resumo_no_fim(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")
        db.registrar_mensagem(
            conversa.id, "in", "oi, vi o insta de voces", AGORA - timedelta(hours=6)
        )
        db.registrar_mensagem(
            conversa.id, "out", "Oi! Me manda uma foto do seu pet?",
            AGORA - timedelta(hours=5),
        )
        extrator = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui ele, o nome dele e Thor"})
        ]))
        db.registrar_mensagem(
            conversa.id, "in", "aqui ele, o nome dele e Thor", AGORA - timedelta(hours=4)
        )
        resultado = extrator.processar_conversa(conversa.id, agora=AGORA)
        self.assertEqual(resultado.estado.estagio, "S2")

        antes = self._estado_e_fila(db, conversa)

        estado = recalcular(db, db.get_conversa(conversa.id), agora=AGORA)
        contexto = ContextoResumo(
            funil=conversa.funil,
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            sinal=estado.classificacao.sinal,
            fatos=db.fatos_detalhados(conversa.id),
            eventos=db.eventos_da_conversa(conversa.id),
            objecoes=db.objecoes_da_conversa(conversa.id),
            correcoes=db.correcoes_da_conversa(conversa.id),
            followups=db.followups_da_conversa(conversa.id),
            historico=[(m.direcao, m.texto) for m in db.listar_mensagens(conversa.id)],
        )
        resumo = gerar_resumo(FakeLlm([RESUMO_JSON_OK]), contexto)
        db.gravar_resumo(
            conversa.id,
            resumo=resumo.resumo,
            proximo_passo=resumo.proximo_passo,
            ultima_mensagem_id=None,
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            prompt_versao=PROMPT_VERSAO_RESUMO,
        )

        depois = self._estado_e_fila(db, conversa)
        self.assertEqual(antes, depois)

        # Apagar `resumos_conversa` inteira também não muda nada — é cache.
        db.resumos.clear()
        self.assertEqual(self._estado_e_fila(db, conversa), depois)


class TesteReaberturaManualDeRecusa(unittest.TestCase):
    """Change `estagio-reabertura-manual-e-relogio`: estende o E2E único com
    o ciclo completo de recusa falso-positiva.

        ... -> S2 -> (recusa_explicita, falso positivo) -> SX
            -> desconsideração manual (acoes.desconsiderar_recusa)
            -> reabre em S2 -> mensagem nova do cliente -> S5

    Mora aqui, não num `test_reabertura_e2e.py` à parte, pela mesma regra já
    registrada para `TesteCicloAteVinculoDoRascunho`/`TesteResumoNaoMudaEstado`:
    dois E2E acabam testando versões diferentes do mesmo caminho.
    """

    def test_ciclo_completo_de_recusa_falso_positiva_ate_reabertura(self):
        db = FakeDatabase()
        conversa = db.criar_conversa(nome="Ana")

        db.registrar_mensagem(
            conversa.id, "in", "oi, vi o insta de voces", AGORA - timedelta(hours=10)
        )
        db.registrar_mensagem(
            conversa.id, "out", "Oi! Me manda uma foto do seu pet?",
            AGORA - timedelta(hours=9),
        )
        db.registrar_mensagem(
            conversa.id, "in", "aqui ele, o nome dele e Thor", AGORA - timedelta(hours=8)
        )
        extrator = Extrator(db, FakeLlm([
            resposta(foto_pet_recebida=True,
                     evidencias={"foto_pet_recebida": "aqui ele, o nome dele e Thor"}),
        ]))
        resultado = extrator.processar_conversa(conversa.id, agora=AGORA)
        self.assertEqual(resultado.estado.estagio, "S2")

        # Bloco novo: a extração erra e marca recusa_explicita — falso
        # positivo (§7: o pior caso, lead quente abandonado por engano).
        db.registrar_mensagem(
            conversa.id, "in", "ah, esquece, nao quero mais nao",
            AGORA - timedelta(hours=7),
        )
        extrator2 = Extrator(db, FakeLlm([
            resposta(recusa_explicita=True,
                     evidencias={"recusa_explicita": "nao quero mais nao"}),
        ]))
        resultado = extrator2.processar_conversa(conversa.id, agora=AGORA)
        self.assertEqual(resultado.estado.estagio, "SX")
        # O fato de recusa_explicita agora é o que trava a conversa —
        # confirmando que o teste está testando o caminho certo.
        self.assertTrue(db.fatos_da_conversa(conversa.id)["recusa_explicita"])

        # Sem desconsiderar, um recálculo simples (sem LLM) confirma que a
        # conversa continua travada (regressão: "recusa é fechamento duro").
        preso = recalcular(db, db.get_conversa(conversa.id), agora=AGORA)
        self.assertEqual(preso.estagio, "SX")

        # Operador desconsidera a recusa (design.md) — o fato original
        # continua intacto em `fatos`.
        estado_apos_desconsiderar = acoes.desconsiderar_recusa(
            db, conversa.id, por="marcos"
        )
        self.assertTrue(db.recusa_desconsiderada(conversa.id))
        self.assertTrue(db.fatos_da_conversa(conversa.id)["recusa_explicita"])
        # Reabre no maior estágio já alcançado (S2), nunca em S0/S1.
        self.assertEqual(estado_apos_desconsiderar.estagio, "S2")

        # Mensagem nova do cliente: a Camu manda prévia e preço, e o cliente
        # responde sem recusar — a conversa volta a avançar de verdade.
        db.registrar_mensagem(
            conversa.id, "out", "Aqui esta a previa do produto!",
            AGORA - timedelta(hours=2),
        )
        db.registrar_mensagem(
            conversa.id, "out", "Fica R$ 149 com frete gratis",
            AGORA - timedelta(hours=1, minutes=30),
        )
        db.registrar_mensagem(
            conversa.id, "in", "fechado, pode mandar!",
            AGORA - timedelta(hours=1),
        )
        extrator3 = Extrator(db, FakeLlm([
            resposta(previa_enviada=True, preco_apresentado=True,
                     evidencias={"previa_enviada": "Aqui esta a previa do produto!",
                                 "preco_apresentado": "Fica R$ 149 com frete gratis"}),
        ]))
        final = extrator3.processar_conversa(conversa.id, agora=AGORA)
        self.assertEqual(final.estado.estagio, "S5")
        self.assertNotEqual(final.estado.estagio, "SX")
