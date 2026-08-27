"""Fakes compartilhados. Sem rede e sem Postgres (convenção do WhatBot).

`FakeDatabase` é um banco em memória que implementa a superfície de `Database`
usada por `pipeline` e `extraction`. Ele **imita** o CHECK de follow-up para
que os testes de fila e de rascunho possam exercitar o caminho de recusa — mas
a garantia real é a do Postgres, e está testada em
`tests/integration/test_teto_followup.py`, contra um banco de verdade. Um fake
que "garante" uma constraint prova apenas que o fake concorda consigo mesmo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from camucrm.db import (
    Contato,
    Conversa,
    ContatoResumido,
    CorrecaoRegistro,
    EventoRegistro,
    FatoRegistro,
    FollowupRegistro,
    MarcoRegistro,
    MensagemRegistro,
    ObjecaoRegistro,
    RascunhoRegistro,
    RascunhoVinculadoRegistro,
    ResumoConversa,
    TetoFollowupError,
    _normalizar_texto,
)
from camucrm.rules.sinais import Mensagem
from camucrm.taxonomia import MAX_FOLLOWUPS, is_terminal, rank_estagio


class _FakeCursor:
    """Emula só as 3 formas de consulta que `camucrm.metrics` roda direto em
    SQL (`conversao` e `tempo_por_estagio`), sem passar por método de
    `Database`.

    `metrics.py` é código existente que já não respeita "db.py é o único
    lugar com SQL" — não é este change que resolve isso. Esta classe existe
    só para que `GET /api/metricas` do painel tenha um smoke test sem
    Postgres; ela reconhece as consultas pelo texto, não interpreta SQL de
    verdade, e levanta se vir uma forma que não conhece.
    """

    def __init__(self, db: "FakeDatabase"):
        self._db = db
        self._resultado: list[tuple] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def execute(self, query: str, params: tuple = ()) -> None:
        q = " ".join(query.split())
        if "COUNT(DISTINCT conversa_id) FROM eventos_estagio" in q:
            de = params[0]
            desde = params[1] if len(params) > 1 else None
            ids = {
                e["conversa_id"] for e in self._db.eventos
                if e["para"] == de and (desde is None or e["em"] >= desde)
            }
            self._resultado = [(len(ids),)]
        elif "COUNT(DISTINCT a.conversa_id)" in q:
            de, para = params[0], params[1]
            desde = params[2] if len(params) > 2 else None
            ids_de = {e["conversa_id"] for e in self._db.eventos if e["para"] == de}
            ids_para = {
                e["conversa_id"] for e in self._db.eventos
                if e["para"] == para and (desde is None or e["em"] >= desde)
            }
            self._resultado = [(len(ids_de & ids_para),)]
        elif "WITH transicoes AS" in q:
            por_conversa: dict[int, list[dict]] = {}
            for e in self._db.eventos:
                if e["origem"] != "live":
                    continue
                por_conversa.setdefault(e["conversa_id"], []).append(e)
            pares: dict[str, list[float]] = {}
            for eventos in por_conversa.values():
                ordenados = sorted(eventos, key=lambda e: e["em"])
                for atual, prox in zip(ordenados, ordenados[1:]):
                    horas = (prox["em"] - atual["em"]).total_seconds() / 3600.0
                    pares.setdefault(atual["para"], []).append(horas)
            linhas = []
            for estagio in sorted(pares):
                valores = sorted(pares[estagio])
                n = len(valores)
                meio = n // 2
                mediana = (
                    valores[meio] if n % 2
                    else (valores[meio - 1] + valores[meio]) / 2
                )
                linhas.append((estagio, n, mediana))
            self._resultado = linhas
        else:
            raise NotImplementedError(
                f"FakeDatabase._conn não sabe emular esta consulta: {q[:80]!r}"
            )

    def fetchone(self):
        return self._resultado[0] if self._resultado else None

    def fetchall(self):
        return list(self._resultado)


class _FakeConn:
    def __init__(self, db: "FakeDatabase"):
        self._db = db

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._db)


class FakeDatabase:
    def __init__(self):
        self.contatos: dict[int, Contato] = {}
        self.conversas: dict[int, Conversa] = {}
        self.mensagens: dict[int, list[tuple[int, str, str, datetime]]] = {}
        self.externa_ids: set[str] = set()
        # (conversa_id, chave, evidencia, extraido_em, mensagem_em)
        self.fatos: list[tuple[int, str, str | None, datetime, datetime | None]] = []
        self.eventos: list[dict[str, Any]] = []
        self.objecoes: list[dict[str, Any]] = []
        self.correcoes: list[dict[str, Any]] = []
        self.followups: dict[int, list[tuple[int, str | None, datetime]]] = {}
        self.marcos: dict[int, set[str]] = {}
        # `marco -> (em, por)`, por conversa — precisa de `por` e `em` reais
        # para `marcos_detalhados`, que `self.marcos` (só o set) não guarda.
        self.marcos_por: dict[int, dict[str, tuple[datetime, str | None]]] = {}
        # change `rascunho-registrado`.
        self.rascunhos: dict[int, RascunhoRegistro] = {}
        # change `resumo-conversa`: chave é a fronteira (conversa_id,
        # ultima_mensagem_id, prompt_versao) — mesma coisa que o índice único
        # de `resumos_conversa` protege no banco real.
        self.resumos: dict[int, ResumoConversa] = {}
        self._proximo_id = 1
        # Proxy de `conversas.atualizado_em` para `token_de_mudanca` (change
        # `painel-tempo-real`): o fake não guarda timestamp de atualização
        # por conversa, então usa um contador que só cresce, nos mesmos
        # pontos que a `Database` real toca a coluna (registrar mensagem,
        # atualizar estado, gravar evento de estágio).
        self._toques_conversa = 0

    # -- helpers de montagem ---------------------------------------------

    def _novo_id(self) -> int:
        valor = self._proximo_id
        self._proximo_id += 1
        return valor

    def criar_conversa(
        self,
        *,
        funil: str = "b2c",
        estagio: str = "S0",
        nome: str = "Teste",
        telefone: str | None = "5511900000000",
    ) -> Conversa:
        contato_id = self._novo_id()
        self.contatos[contato_id] = Contato(
            contato_id, nome, f"hash{contato_id}", telefone, funil, None,
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

    def set_tipo_contato(self, contato_id: int, tipo: str) -> None:
        contato = self.contatos.get(contato_id)
        if contato is not None:
            contato.tipo = tipo

    def set_funil_conversa(self, conversa_id: int, funil: str) -> None:
        conversa = self.conversas.get(conversa_id)
        if conversa is not None:
            conversa.funil = funil

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
        self._toques_conversa += 1
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
            existentes = {(c, k, e) for c, k, e, _, _ in self.fatos}
            if (conversa_id, chave, evidencia) in existentes:
                continue
            self.fatos.append(
                (conversa_id, chave, evidencia, extraido_em, momentos.get(chave))
            )
            inseridos += 1
        return inseridos

    def fatos_da_conversa(self, conversa_id: int) -> dict[str, bool]:
        return {k: True for c, k, _, _, _ in self.fatos if c == conversa_id}

    def fato_registrado_em(self, conversa_id, chave) -> datetime | None:
        momentos = [
            (mensagem_em or extraido_em)
            for c, k, _, extraido_em, mensagem_em in self.fatos
            if c == conversa_id and k == chave
        ]
        return min(momentos) if momentos else None

    def fatos_detalhados(self, conversa_id: int) -> list[FatoRegistro]:
        linhas = [
            FatoRegistro(k, True, e, extraido_em, mensagem_em)
            for c, k, e, extraido_em, mensagem_em in self.fatos
            if c == conversa_id
        ]
        return sorted(linhas, key=lambda f: f.extraido_em)

    def gravar_evento_estagio(self, conversa_id, de, para, *, origem="live", motivo=None, em=None):
        self.eventos.append(
            {"conversa_id": conversa_id, "de": de, "para": para,
             "origem": origem, "motivo": motivo,
             "em": em or datetime.now(timezone.utc)}
        )
        self._toques_conversa += 1

    def eventos_da_conversa(self, conversa_id: int) -> list[EventoRegistro]:
        eventos = [e for e in self.eventos if e["conversa_id"] == conversa_id]
        return [
            EventoRegistro(e["de"], e["para"], e["em"], e["origem"], e["motivo"])
            for e in eventos
        ]

    def estagio_maximo_alcancado(self, conversa_id: int) -> str | None:
        alcancados = [
            e["para"] for e in self.eventos
            if e["conversa_id"] == conversa_id and not is_terminal(e["para"])
        ]
        return max(alcancados, key=rank_estagio) if alcancados else None

    def estagio_corrente(self, conversa_id: int) -> str | None:
        eventos = [e for e in self.eventos if e["conversa_id"] == conversa_id]
        return eventos[-1]["para"] if eventos else None

    def estagios_de_conversas_encerradas(self) -> dict[int, list[str]]:
        """Change `analise-desempenho`: espelha `db.py`, eventos crus de
        conversas com `resultado IS NOT NULL` — a ordenação por rank fica em
        `metrics.onde_morrem`, não aqui."""
        encerradas = {
            c.id for c in self.conversas.values() if c.resultado is not None
        }
        resultado: dict[int, list[str]] = {}
        for e in self.eventos:
            if e["conversa_id"] in encerradas:
                resultado.setdefault(e["conversa_id"], []).append(e["para"])
        return resultado

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
            {"id": self._novo_id(), "conversa_id": conversa_id, "categoria": categoria,
             "estagio": estagio, "trecho": trecho, "em": em or datetime.now(timezone.utc)}
        )

    def distribuicao_objecoes(self, desde=None) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for o in self.objecoes:
            if desde is not None and o["em"] < desde:
                continue
            contagem[o["categoria"]] = contagem.get(o["categoria"], 0) + 1
        return contagem

    def distribuicao_objecoes_por_estagio(
        self, desde=None
    ) -> dict[tuple[str | None, str], int]:
        contagem: dict[tuple[str | None, str], int] = {}
        for o in self.objecoes:
            if desde is not None and o["em"] < desde:
                continue
            chave = (o["estagio"], o["categoria"])
            contagem[chave] = contagem.get(chave, 0) + 1
        return contagem

    def _conn(self) -> _FakeConn:
        """Só para `camucrm.metrics`, que fala SQL direto com `Database`.

        Ver `_FakeCursor` no topo do módulo — emulação estreita, não um
        motor de SQL de verdade.
        """
        return _FakeConn(self)

    def objecoes_da_conversa(self, conversa_id: int) -> list[ObjecaoRegistro]:
        linhas = [
            ObjecaoRegistro(o["id"], o["categoria"], o["estagio"], o["trecho"], o["em"])
            for o in self.objecoes
            if o["conversa_id"] == conversa_id
        ]
        return sorted(linhas, key=lambda o: o.em)

    def atualizar_estado_conversa(self, conversa_id, **campos):
        conversa = self.conversas[conversa_id]
        for nome, valor in campos.items():
            if valor is not None:
                setattr(conversa, nome, valor)
        self._toques_conversa += 1

    def token_de_mudanca(self) -> str:
        """Espelho pobre de `Database.token_de_mudanca` — três números que só
        crescem, um por motivo de mudança (mensagem, evento de estágio,
        toque em conversa). Não é a mesma fórmula da `Database` real (não há
        `atualizado_em` guardado no fake), só a mesma propriedade que os
        testes de `stream.py` precisam: muda sempre que um dos três motivos
        acontece, nunca de outro jeito.
        """
        max_mensagem = 0
        for linhas in self.mensagens.values():
            for identificador, *_ in linhas:
                max_mensagem = max(max_mensagem, identificador)
        return f"{max_mensagem}:{len(self.eventos)}:{self._toques_conversa}"

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

    def followups_da_conversa(self, conversa_id: int) -> list[FollowupRegistro]:
        enviados = self.followups.get(conversa_id) or []
        return [
            FollowupRegistro(numero, texto, enviado_em)
            for numero, texto, enviado_em in sorted(enviados, key=lambda f: f[0])
        ]

    def retorno_por_numero_followup(self) -> dict[int, tuple[int, int]]:
        """Change `analise-desempenho`: qualquer mensagem `in` na mesma
        conversa depois de `enviado_em` conta como retorno — mesmo critério
        de `db.py`."""
        contagem: dict[int, list[int]] = {}
        for conversa_id, enviados in self.followups.items():
            mensagens_in = [
                enviada_em
                for _, direcao, _, enviada_em in self.mensagens.get(conversa_id, [])
                if direcao == "in"
            ]
            for numero, _, enviado_em in enviados:
                total, com_retorno = contagem.setdefault(numero, [0, 0])
                total += 1
                teve_retorno = any(m > enviado_em for m in mensagens_in)
                contagem[numero] = [total, com_retorno + (1 if teve_retorno else 0)]
        return {numero: tuple(v) for numero, v in contagem.items()}

    def registrar_marco(self, conversa_id, marco, *, por=None):
        self.marcos.setdefault(conversa_id, set()).add(marco)
        self.marcos_por.setdefault(conversa_id, {})[marco] = (
            datetime.now(timezone.utc), por,
        )

    def marco_em(self, conversa_id: int, marco: str):
        registro = self.marcos_por.get(conversa_id, {}).get(marco)
        if registro:
            return registro[0]
        return (
            datetime.now(timezone.utc)
            if marco in self.marcos.get(conversa_id, set())
            else None
        )

    def marcos_da_conversa(self, conversa_id: int) -> set[str]:
        return set(self.marcos.get(conversa_id, set()))

    def marcos_detalhados(self, conversa_id: int) -> list[MarcoRegistro]:
        registros = self.marcos_por.get(conversa_id, {})
        linhas = [
            MarcoRegistro(marco, em, por) for marco, (em, por) in registros.items()
        ]
        return sorted(linhas, key=lambda m: m.em)

    def registrar_correcao(self, conversa_id, campo, antes, depois, *, por=None):
        self.correcoes.append(
            {"id": self._novo_id(), "conversa_id": conversa_id, "campo": campo,
             "antes": antes, "depois": depois, "por": por,
             "em": datetime.now(timezone.utc)}
        )

    def correcoes_da_conversa(self, conversa_id: int) -> list[CorrecaoRegistro]:
        linhas = [
            CorrecaoRegistro(
                c["id"], c["campo"], c["antes"], c["depois"], c["em"], c["por"]
            )
            for c in self.correcoes
            if c["conversa_id"] == conversa_id
        ]
        return sorted(linhas, key=lambda c: c.em, reverse=True)

    def padrao_correcoes(
        self, desde=None
    ) -> list[tuple[str, str | None, str | None, int]]:
        contagem: dict[tuple[str, str | None, str | None], int] = {}
        for c in self.correcoes:
            if desde is not None and c["em"] < desde:
                continue
            chave = (c["campo"], c["antes"], c["depois"])
            contagem[chave] = contagem.get(chave, 0) + 1
        linhas = [(campo, antes, depois, n) for (campo, antes, depois), n in contagem.items()]
        return sorted(linhas, key=lambda l: -l[3])

    def listar_mensagens_registradas(
        self, *, conversa_id: int | None = None, desde_id: int | None = None,
        limite: int = 200,
    ) -> list[MensagemRegistro]:
        todas = []
        for c_id, linhas in self.mensagens.items():
            if conversa_id is not None and c_id != conversa_id:
                continue
            for identificador, direcao, texto, enviada_em in linhas:
                if identificador > (desde_id or 0):
                    todas.append(
                        MensagemRegistro(identificador, c_id, direcao, texto, enviada_em)
                    )
        todas.sort(key=lambda m: m.id)
        return todas[:limite]

    def ultimas_mensagens_globais(
        self, limite: int = 8
    ) -> list[tuple[str, str, datetime, str]]:
        todas = []
        for conversa_id, linhas in self.mensagens.items():
            conversa = self.conversas.get(conversa_id)
            nome = (conversa.nome_contato if conversa else "") or ""
            for _, direcao, texto, enviada_em in linhas:
                todas.append((direcao, texto, enviada_em, nome))
        todas.sort(key=lambda item: item[2])
        recentes = todas[-limite:]
        return [
            (d, (t or "").replace("\n", " "), q, n) for d, t, q, n in recentes
        ]

    def contato_resumido(self, conversa_id: int) -> ContatoResumido | None:
        conversa = self.conversas.get(conversa_id)
        if conversa is None:
            return None
        contato = self.contatos.get(conversa.contato_id)
        if contato is None:
            return None
        return ContatoResumido(
            contato.id, contato.nome, contato.tipo,
            contato.telefone is not None, contato.criado_em,
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

    # -- rascunhos (§10, change `rascunho-registrado`) --------------------

    def gravar_rascunho(
        self,
        conversa_id,
        *,
        estagio,
        temperatura,
        funil,
        objecao=None,
        followups_enviados=0,
        opcoes=None,
        avisos=(),
        encerrar=False,
        motivo=None,
        modelo=None,
        prompt_versao=None,
        gerado_por=None,
    ) -> int:
        if bool(encerrar) == bool(opcoes):
            raise ValueError(
                "rascunho é geração (opcoes) OU recusa (encerrar=True), nunca os dois"
            )
        rascunho_id = self._novo_id()
        self.rascunhos[rascunho_id] = RascunhoRegistro(
            id=rascunho_id,
            conversa_id=conversa_id,
            estagio=estagio,
            temperatura=temperatura,
            funil=funil,
            objecao=objecao,
            followups_enviados=followups_enviados,
            opcao_1=opcoes[0] if opcoes else None,
            opcao_2=opcoes[1] if opcoes else None,
            avisos="; ".join(avisos) if avisos else None,
            encerrar=encerrar,
            motivo=motivo,
            modelo=modelo,
            prompt_versao=prompt_versao,
            gerado_em=datetime.now(timezone.utc),
            gerado_por=gerado_por,
            escolhida=None,
            texto_final=None,
            escolhido_em=None,
            escolhido_por=None,
            mensagem_id=None,
            estagio_no_envio=None,
        )
        return rascunho_id

    def registrar_escolha_rascunho(
        self, rascunho_id, *, escolhida=None, texto_final=None, por=None
    ) -> None:
        if escolhida is not None and escolhida not in (1, 2):
            raise ValueError(f"escolhida inválida: {escolhida!r} (use 1, 2 ou None)")
        if escolhida is None and not texto_final:
            raise ValueError(
                "escolha precisa de `escolhida` (1 ou 2) ou de `texto_final`"
            )
        registro = self.rascunhos.get(rascunho_id)
        if registro is None:
            return
        registro.escolhida = escolhida
        registro.texto_final = texto_final
        registro.escolhido_em = datetime.now(timezone.utc)
        registro.escolhido_por = por

    def vincular_rascunho(self, rascunho_id, mensagem_id, *, estagio_no_envio=None) -> bool:
        registro = self.rascunhos.get(rascunho_id)
        if registro is None:
            return False
        # Espelha o índice único parcial `rascunhos_mensagem_unica`: recusa
        # reivindicar uma mensagem já vinculada a outro rascunho.
        for outro in self.rascunhos.values():
            if outro.id != rascunho_id and outro.mensagem_id == mensagem_id:
                raise ValueError(
                    f"mensagem {mensagem_id} já vinculada ao rascunho {outro.id}"
                )
        registro.mensagem_id = mensagem_id
        if estagio_no_envio is not None:
            registro.estagio_no_envio = estagio_no_envio
        return True

    def rascunho_pendente_por_texto(self, conversa_id, texto, *, janela_horas=48):
        alvo = _normalizar_texto(texto)
        if not alvo:
            return None
        agora = datetime.now(timezone.utc)
        candidatos = [
            r
            for r in self.rascunhos.values()
            if r.conversa_id == conversa_id
            and r.mensagem_id is None
            and (agora - r.gerado_em) <= timedelta(hours=janela_horas)
        ]
        candidatos.sort(key=lambda r: r.gerado_em, reverse=True)
        for registro in candidatos:
            if alvo in (
                _normalizar_texto(registro.opcao_1),
                _normalizar_texto(registro.opcao_2),
                _normalizar_texto(registro.texto_final),
            ):
                return registro.id
        return None

    def rascunho(self, rascunho_id: int) -> RascunhoRegistro | None:
        return self.rascunhos.get(rascunho_id)

    def rascunhos_da_conversa(self, conversa_id: int, limite: int = 5):
        linhas = [r for r in self.rascunhos.values() if r.conversa_id == conversa_id]
        linhas.sort(key=lambda r: r.gerado_em, reverse=True)
        return linhas[:limite]

    def rascunhos_vinculados_para_analise(self) -> list[RascunhoVinculadoRegistro]:
        """Change `analise-desempenho`: mesma janela de 72h de `db.py`,
        calculada em Python sobre as mensagens em memória."""
        # mensagem_id -> (conversa_id, enviada_em)
        por_mensagem: dict[int, tuple[int, datetime]] = {}
        for conversa_id, linhas in self.mensagens.items():
            for mensagem_id, _direcao, _texto, enviada_em in linhas:
                por_mensagem[mensagem_id] = (conversa_id, enviada_em)

        resultado = []
        for r in self.rascunhos.values():
            if r.mensagem_id is None:
                continue
            info = por_mensagem.get(r.mensagem_id)
            if info is None:
                continue
            conversa_id, enviada_em = info
            janela = enviada_em + timedelta(hours=72)
            estagios_72h = []
            for e in self.eventos:
                if (
                    e["conversa_id"] == conversa_id
                    and enviada_em < e["em"] <= janela
                    and e["para"] not in estagios_72h
                ):
                    estagios_72h.append(e["para"])
            resultado.append(
                RascunhoVinculadoRegistro(
                    rascunho_id=r.id,
                    escolhida=r.escolhida,
                    editado=r.texto_final is not None,
                    estagio_no_envio=r.estagio_no_envio,
                    estagios_72h=estagios_72h,
                )
            )
        return sorted(resultado, key=lambda r: r.rascunho_id)

    # -- resumos (change `resumo-conversa`) --------------------------------

    def gravar_resumo(
        self,
        conversa_id,
        *,
        resumo,
        proximo_passo,
        ultima_mensagem_id,
        estagio,
        temperatura,
        prompt_versao,
        modelo=None,
        gerado_por=None,
    ) -> int:
        """Espelha o `ON CONFLICT (conversa_id, coalesce(ultima_mensagem_id,
        0), prompt_versao) DO UPDATE` do banco real: mesma fronteira
        atualiza a linha existente em vez de duplicar.
        """
        fronteira = (conversa_id, ultima_mensagem_id or 0, prompt_versao)
        for existente in self.resumos.values():
            if (
                existente.conversa_id,
                existente.ultima_mensagem_id or 0,
                existente.prompt_versao,
            ) == fronteira:
                existente.resumo = resumo
                existente.proximo_passo = proximo_passo
                existente.estagio = estagio
                existente.temperatura = temperatura
                existente.modelo = modelo
                existente.gerado_em = datetime.now(timezone.utc)
                existente.gerado_por = gerado_por
                return existente.id
        resumo_id = self._novo_id()
        self.resumos[resumo_id] = ResumoConversa(
            id=resumo_id,
            conversa_id=conversa_id,
            resumo=resumo,
            proximo_passo=proximo_passo,
            ultima_mensagem_id=ultima_mensagem_id,
            estagio=estagio,
            temperatura=temperatura,
            prompt_versao=prompt_versao,
            modelo=modelo,
            gerado_em=datetime.now(timezone.utc),
            gerado_por=gerado_por,
        )
        return resumo_id

    def resumo_vigente(self, conversa_id: int, prompt_versao: str) -> ResumoConversa | None:
        candidatos = [
            r
            for r in self.resumos.values()
            if r.conversa_id == conversa_id and r.prompt_versao == prompt_versao
        ]
        if not candidatos:
            return None
        candidatos.sort(key=lambda r: ((r.ultima_mensagem_id or 0), r.gerado_em), reverse=True)
        return candidatos[0]

    def mensagens_desde(self, conversa_id: int, mensagem_id: int | None) -> int:
        linhas = self.mensagens.get(conversa_id, [])
        limite = mensagem_id or 0
        return sum(1 for identificador, *_ in linhas if identificador > limite)
