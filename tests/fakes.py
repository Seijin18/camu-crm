"""Fakes compartilhados. Sem rede e sem Postgres (convenção do WhatBot).

`FakeDatabase` é um banco em memória que implementa a superfície de `Database`
usada por `pipeline` e `extraction`. Ele **imita** o CHECK de follow-up para
que os testes de fila e de rascunho possam exercitar o caminho de recusa — mas
a garantia real é a do Postgres, e está testada em
`tests/integration/test_teto_followup.py`, contra um banco de verdade. Um fake
que "garante" uma constraint prova apenas que o fake concorda consigo mesmo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from camucrm.db import Contato, Conversa, TetoFollowupError
from camucrm.rules.sinais import Mensagem
from camucrm.taxonomia import MAX_FOLLOWUPS, is_terminal, rank_estagio


class FakeDatabase:
    def __init__(self):
        self.contatos: dict[int, Contato] = {}
        self.conversas: dict[int, Conversa] = {}
        self.mensagens: dict[int, list[tuple[int, str, str, datetime]]] = {}
        self.externa_ids: set[str] = set()
        self.fatos: list[tuple[int, str, str | None, datetime]] = []
        self.eventos: list[dict[str, Any]] = []
        self.objecoes: list[dict[str, Any]] = []
        self.correcoes: list[dict[str, Any]] = []
        self.followups: dict[int, list[tuple[int, str | None, datetime]]] = {}
        self.marcos: dict[int, set[str]] = {}
        self._proximo_id = 1

    # -- helpers de montagem ---------------------------------------------

    def _novo_id(self) -> int:
        valor = self._proximo_id
        self._proximo_id += 1
        return valor

    def criar_conversa(
        self, *, funil: str = "b2c", estagio: str = "S0", nome: str = "Teste"
    ) -> Conversa:
        contato_id = self._novo_id()
        self.contatos[contato_id] = Contato(
            contato_id, nome, f"hash{contato_id}", "5511900000000", funil, None,
            datetime.now(timezone.utc),
        )
        conversa_id = self._novo_id()
        conversa = Conversa(
            id=conversa_id, contato_id=contato_id, funil=funil, estagio=estagio,
            bola_com="cliente", temperatura=None, ultimo_inbound=None,
            ultimo_outbound=None, followups_enviados=0, resultado=None,
            ultima_mensagem_processada_id=None, nome_contato=nome,
        )
        self.conversas[conversa_id] = conversa
        self.mensagens[conversa_id] = []
        self.followups[conversa_id] = []
        self.marcos[conversa_id] = set()
        return conversa

    # -- superfície de Database -------------------------------------------

    def get_conversa(self, conversa_id: int) -> Conversa | None:
        return self.conversas.get(conversa_id)

    def listar_conversas_abertas(self, limite: int = 500) -> list[Conversa]:
        return [c for c in self.conversas.values() if c.resultado is None][:limite]

    def registrar_mensagem(
        self, conversa_id, direcao, texto, enviada_em=None, *, externa_id=None
    ) -> int | None:
        enviada_em = enviada_em or datetime.now(timezone.utc)
        existentes = self.mensagens.setdefault(conversa_id, [])
        # Espelha o índice único parcial de `mensagens.externa_id`: sem isto o
        # fake aceitaria reentrega e um bug de deduplicação passaria batido.
        if externa_id and externa_id in self.externa_ids:
            return None
        identificador = self._novo_id()
        if externa_id:
            self.externa_ids.add(externa_id)
        existentes.append((identificador, direcao, texto, enviada_em))
        conversa = self.conversas[conversa_id]
        if direcao == "in":
            conversa.ultimo_inbound = enviada_em
            conversa.bola_com = "camu"
        else:
            conversa.ultimo_outbound = enviada_em
            conversa.bola_com = "cliente"
        return identificador

    def listar_mensagens(self, conversa_id: int) -> list[Mensagem]:
        return [
            Mensagem(direcao, enviada_em, texto)
            for _, direcao, texto, enviada_em in sorted(
                self.mensagens.get(conversa_id, []), key=lambda m: m[3]
            )
        ]

    def mensagens_novas(self, conversa_id: int, desde_id: int | None):
        return [
            m for m in sorted(self.mensagens.get(conversa_id, []), key=lambda m: m[0])
            if m[0] > (desde_id or 0)
        ]

    def gravar_fatos(
        self, conversa_id, fatos, evidencias, *, extraido_em=None, momentos=None
    ) -> int:
        extraido_em = extraido_em or datetime.now(timezone.utc)
        momentos = momentos or {}
        inseridos = 0
        for chave, valor in fatos.items():
            if not valor:
                continue
            evidencia = evidencias.get(chave)
            if (conversa_id, chave, evidencia) in {(c, k, e) for c, k, e, _ in self.fatos}:
                continue
            self.fatos.append(
                (conversa_id, chave, evidencia, momentos.get(chave, extraido_em))
            )
            inseridos += 1
        return inseridos

    def fatos_da_conversa(self, conversa_id: int) -> dict[str, bool]:
        return {k: True for c, k, _, _ in self.fatos if c == conversa_id}

    def fato_registrado_em(self, conversa_id, chave) -> datetime | None:
        momentos = [m for c, k, _, m in self.fatos if c == conversa_id and k == chave]
        return min(momentos) if momentos else None

    def gravar_evento_estagio(self, conversa_id, de, para, *, origem="live", motivo=None, em=None):
        self.eventos.append(
            {"conversa_id": conversa_id, "de": de, "para": para,
             "origem": origem, "motivo": motivo,
             "em": em or datetime.now(timezone.utc)}
        )

    def estagio_maximo_alcancado(self, conversa_id: int) -> str | None:
        alcancados = [
            e["para"] for e in self.eventos
            if e["conversa_id"] == conversa_id and not is_terminal(e["para"])
        ]
        return max(alcancados, key=rank_estagio) if alcancados else None

    def estagios_registrados(self, conversa_id: int) -> set[str]:
        return {e["para"] for e in self.eventos if e["conversa_id"] == conversa_id}

    def ultimo_avanco_em(self, conversa_id: int) -> datetime | None:
        momentos = [
            e["em"] for e in self.eventos
            if e["conversa_id"] == conversa_id and e["origem"] == "live"
        ]
        return max(momentos) if momentos else None

    def gravar_objecao(self, conversa_id, categoria, *, estagio=None, trecho=None, em=None):
        self.objecoes.append(
            {"conversa_id": conversa_id, "categoria": categoria, "estagio": estagio,
             "trecho": trecho, "em": em or datetime.now(timezone.utc)}
        )

    def distribuicao_objecoes(self, desde=None) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for o in self.objecoes:
            contagem[o["categoria"]] = contagem.get(o["categoria"], 0) + 1
        return contagem

    def atualizar_estado_conversa(self, conversa_id, **campos):
        conversa = self.conversas[conversa_id]
        for nome, valor in campos.items():
            if valor is not None:
                setattr(conversa, nome, valor)

    def registrar_followup(self, conversa_id, texto=None) -> int:
        enviados = self.followups.setdefault(conversa_id, [])
        numero = len(enviados) + 1
        if numero > MAX_FOLLOWUPS:
            raise TetoFollowupError(
                f"conversa {conversa_id} já atingiu o teto de {MAX_FOLLOWUPS} follow-ups"
            )
        enviados.append((numero, texto, datetime.now(timezone.utc)))
        self.conversas[conversa_id].followups_enviados = numero
        return numero

    def ultimo_followup_em(self, conversa_id: int) -> datetime | None:
        enviados = self.followups.get(conversa_id) or []
        return max((f[2] for f in enviados), default=None)

    def registrar_marco(self, conversa_id, marco, *, por=None):
        self.marcos.setdefault(conversa_id, set()).add(marco)

    def marco_em(self, conversa_id: int, marco: str):
        return (
            datetime.now(timezone.utc)
            if marco in self.marcos.get(conversa_id, set())
            else None
        )

    def marcos_da_conversa(self, conversa_id: int) -> set[str]:
        return set(self.marcos.get(conversa_id, set()))

    def registrar_correcao(self, conversa_id, campo, antes, depois, *, por=None):
        self.correcoes.append(
            {"conversa_id": conversa_id, "campo": campo, "antes": antes,
             "depois": depois, "por": por}
        )

    def upsert_contato(self, telefone, *, nome=None, tipo="b2c", origem=None) -> Contato:
        for contato in self.contatos.values():
            if contato.telefone == telefone:
                return contato
        contato_id = self._novo_id()
        contato = Contato(
            contato_id, nome, f"hash-{telefone}", telefone, tipo, origem,
            datetime.now(timezone.utc),
        )
        self.contatos[contato_id] = contato
        return contato

    def get_or_create_conversa(self, contato_id, funil=None) -> Conversa:
        for conversa in self.conversas.values():
            if conversa.contato_id == contato_id and conversa.resultado is None:
                return conversa
        contato = self.contatos[contato_id]
        conversa = self.criar_conversa(
            funil=funil or contato.tipo,
            estagio="P0" if (funil or contato.tipo) == "b2b" else "S0",
            nome=contato.nome or "",
        )
        conversa.contato_id = contato_id
        return conversa
