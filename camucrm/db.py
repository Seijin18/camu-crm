"""Camada de dados: Postgres via psycopg, seguindo o modelo da §9.

Três decisões do documento moram no schema, não no código:

1. **`fatos` separado de `conversas`** (§9): é o que permite reprocessar as
   regras sem chamar o LLM de novo. A saída bruta do modelo é preservada.
2. **`eventos_estagio.origem`** (§8): backfill recupera o estado final, não
   *quando* cada transição ocorreu. Marcar a origem é o que impede timestamps
   inventados de contaminarem a média de duração por estágio.
3. **Teto de 2 follow-ups como constraint** (§6): "O sistema deve tornar isso
   impossível de furar — não é preferência, é preservação de chip e de marca.
   Implementar como constraint no banco, não como validação de aplicação."

Adições ao modelo da §9, todas justificadas no ponto de uso: `followups`,
`marcos_manuais` e algumas colunas de idempotência. Estão marcadas com
"ADIÇÃO" no DDL.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import psycopg
from psycopg_pool import ConnectionPool

from .rules.estagio import ORIGEM_LIVE
from .rules.sinais import ENTRADA, SAIDA, Mensagem
from .taxonomia import B2B, B2C, BOLA_CLIENTE, MAX_FOLLOWUPS

logger = logging.getLogger("camucrm.db")

# §12: telefone com hash para lookup; original apenas onde necessário para
# envio. O salt precisa ser estável — trocá-lo invalida todo lookup existente.
ENV_TELEFONE_SALT = "CAMU_TELEFONE_SALT"

# Marcos que só um humano registra (§3). Fechados como os demais vocabulários.
MARCOS_MANUAIS = ("ganho", "consignacao_assinada", "primeira_reposicao", "perdido")


def hash_telefone(telefone: str, salt: str | None = None) -> str:
    """Hash estável de telefone para lookup (§12).

    O salt vem do ambiente e é obrigatório em produção: sem ele, o hash de um
    número de celular brasileiro é quebrável por força bruta em segundos (o
    espaço de busca é pequeno o bastante para enumerar), o que anularia o
    propósito de guardar o hash.
    """
    salt = salt if salt is not None else os.getenv(ENV_TELEFONE_SALT, "")
    normalizado = "".join(c for c in (telefone or "") if c.isdigit())
    return hashlib.sha256(f"{salt}:{normalizado}".encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Dataclasses de linha
# --------------------------------------------------------------------------


@dataclass
class Contato:
    id: int
    nome: str | None
    telefone_hash: str
    telefone: str | None
    tipo: str
    origem: str | None
    criado_em: datetime

    @property
    def label(self) -> str:
        return self.nome or self.telefone or self.telefone_hash[:8]


@dataclass
class Conversa:
    id: int
    contato_id: int
    funil: str
    estagio: str
    bola_com: str
    temperatura: str | None
    ultimo_inbound: datetime | None
    ultimo_outbound: datetime | None
    followups_enviados: int
    resultado: str | None
    ultima_mensagem_processada_id: int | None
    nome_contato: str | None = None


@dataclass
class Fato:
    conversa_id: int
    chave: str
    valor: bool
    evidencia: str | None
    extraido_em: datetime


@dataclass
class EventoEstagio:
    conversa_id: int
    de: str | None
    para: str
    em: datetime
    origem: str


@dataclass
class Objecao:
    conversa_id: int
    categoria: str
    estagio: str | None
    trecho: str | None
    em: datetime


# --------------------------------------------------------------------------
# Dataclasses de leitura do painel (§13, antecipado — change `painel-leitura`)
# --------------------------------------------------------------------------
#
# Sufixo `Registro` para não colidir com as dataclasses de gravação acima
# (`Fato`, `EventoEstagio`, `Objecao`, que carregam `conversa_id` e servem à
# escrita) nem com `drafts.Rascunho`. Estas são projeções de leitura — uma
# linha por consulta do painel, sem `conversa_id` repetido em toda linha
# porque o chamador já sabe de qual conversa está pedindo.


@dataclass
class FatoRegistro:
    chave: str
    valor: bool
    evidencia: str | None
    extraido_em: datetime
    mensagem_em: datetime | None


@dataclass
class EventoRegistro:
    de: str | None
    para: str
    em: datetime
    origem: str
    motivo: str | None


@dataclass
class ObjecaoRegistro:
    id: int
    categoria: str
    estagio: str | None
    trecho: str | None
    em: datetime


@dataclass
class FollowupRegistro:
    numero: int
    texto: str | None
    enviado_em: datetime


@dataclass
class MarcoRegistro:
    marco: str
    em: datetime
    por: str | None


@dataclass
class CorrecaoRegistro:
    id: int
    campo: str
    antes: str | None
    depois: str | None
    em: datetime
    por: str | None


@dataclass
class MensagemRegistro:
    id: int
    conversa_id: int
    direcao: str
    texto: str
    enviada_em: datetime


@dataclass
class ContatoResumido:
    """Resumo de contato para o painel — nunca carrega telefone em claro (§12)."""

    id: int
    nome: str | None
    tipo: str
    tem_telefone: bool
    criado_em: datetime


class TetoFollowupError(RuntimeError):
    """Tentativa de furar o teto de 2 follow-ups (§6).

    Levantada quando o banco recusa a inserção. A recusa é do banco, não
    daqui — este erro só traduz a violação de constraint para algo que o
    chamador entende.
    """


# --------------------------------------------------------------------------
# DDL
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS contatos (
    id              SERIAL PRIMARY KEY,
    nome            VARCHAR(160),
    telefone_hash   VARCHAR(64) NOT NULL UNIQUE,
    telefone        VARCHAR(32),
    tipo            VARCHAR(8) NOT NULL CHECK (tipo IN ('b2b', 'b2c')),
    origem          VARCHAR(64),
    criado_em       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversas (
    id              SERIAL PRIMARY KEY,
    contato_id      INTEGER NOT NULL REFERENCES contatos(id) ON DELETE CASCADE,
    funil           VARCHAR(8) NOT NULL CHECK (funil IN ('b2b', 'b2c')),
    estagio         VARCHAR(4) NOT NULL,
    bola_com        VARCHAR(8) NOT NULL DEFAULT 'cliente'
                    CHECK (bola_com IN ('camu', 'cliente')),
    temperatura     VARCHAR(16),
    ultimo_inbound  TIMESTAMP WITH TIME ZONE,
    ultimo_outbound TIMESTAMP WITH TIME ZONE,
    -- §6: o teto vive aqui, no banco. Um bug de aplicação que tentasse um
    -- terceiro follow-up aborta a transação em vez de queimar o chip.
    followups_enviados INTEGER NOT NULL DEFAULT 0
                    CHECK (followups_enviados >= 0 AND followups_enviados <= 2),
    resultado       VARCHAR(32),
    ultima_mensagem_processada_id INTEGER,
    criado_em       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    atualizado_em   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversas_contato_idx ON conversas (contato_id);
-- Uma conversa aberta por contato, garantido pelo banco.
--
-- `get_or_create_conversa` lê e só então insere, e o webhook processa eventos
-- em paralelo: duas mensagens do mesmo número chegando juntas viam ambas
-- "nenhuma conversa aberta" e criavam duas. O histórico se dividia entre elas,
-- o estágio parava no que cada metade sustentava, e nada nos dados denunciava
-- o problema. Como o teto de follow-ups (§6), a garantia precisa ser do banco:
-- a segunda inserção conflita e o chamador relê a que venceu.
CREATE UNIQUE INDEX IF NOT EXISTS conversas_uma_aberta_por_contato
    ON conversas (contato_id) WHERE resultado IS NULL;
CREATE INDEX IF NOT EXISTS conversas_triagem_idx
    ON conversas (temperatura, estagio);

CREATE TABLE IF NOT EXISTS mensagens (
    id              SERIAL PRIMARY KEY,
    conversa_id     INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    direcao         VARCHAR(4) NOT NULL CHECK (direcao IN ('in', 'out')),
    texto           TEXT NOT NULL DEFAULT '',
    enviada_em      TIMESTAMP WITH TIME ZONE NOT NULL,
    -- ADIÇÃO (§2, idempotência): id da mensagem no transporte. Sem ele, um
    -- webhook reentregue vira mensagem duplicada e o histórico deixa de ser
    -- reconstituível.
    externa_id      VARCHAR(128)
);
CREATE UNIQUE INDEX IF NOT EXISTS mensagens_externa_id_idx
    ON mensagens (externa_id) WHERE externa_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS mensagens_conversa_idx
    ON mensagens (conversa_id, enviada_em);

-- §9: saída bruta do LLM, preservada. Append-only: cada rodada de extração
-- acrescenta o que afirmou, e o estado corrente de um fato é "existe alguma
-- linha com valor = true". É isso que dá a monotonicidade que §2 exige de
-- graça, sem trava no estágio.
CREATE TABLE IF NOT EXISTS fatos (
    id              SERIAL PRIMARY KEY,
    conversa_id     INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    chave           VARCHAR(48) NOT NULL,
    valor           BOOLEAN NOT NULL,
    evidencia       TEXT,
    extraido_em     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    -- ADIÇÃO: momento da MENSAGEM que carrega a evidência, não o da extração.
    -- `extraido_em` é sempre posterior a todo o bloco processado; usá-lo como
    -- "quando o preço foi apresentado" tornaria S5 e P3 inalcançáveis numa
    -- única passada, porque a resposta do cliente estaria sempre antes do
    -- carimbo do fato. NULL quando a evidência não foi localizável.
    mensagem_em     TIMESTAMP WITH TIME ZONE
);
CREATE INDEX IF NOT EXISTS fatos_conversa_idx ON fatos (conversa_id, chave);
-- Idempotência (§2): reprocessar o mesmo bloco não duplica o mesmo fato com
-- a mesma evidência.
CREATE UNIQUE INDEX IF NOT EXISTS fatos_dedupe_idx
    ON fatos (conversa_id, chave, valor, md5(coalesce(evidencia, '')));

CREATE TABLE IF NOT EXISTS eventos_estagio (
    id              SERIAL PRIMARY KEY,
    conversa_id     INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    de              VARCHAR(4),
    para            VARCHAR(4) NOT NULL,
    em              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    -- §8: 'backfill' marca timestamps que não correspondem a quando a
    -- transição realmente ocorreu. Métricas de tempo DEVEM filtrar por
    -- origem = 'live'; métricas de conversão podem usar as duas.
    origem          VARCHAR(16) NOT NULL DEFAULT 'live'
                    CHECK (origem IN ('live', 'backfill')),
    motivo          VARCHAR(120)
);
CREATE INDEX IF NOT EXISTS eventos_estagio_conversa_idx
    ON eventos_estagio (conversa_id, em);
-- Cada transição acontece uma vez por conversa.
--
-- §2 exige que reprocessar não duplique evento, e `transicao()` garante isso
-- para chamadas em série. Não garante sob concorrência: três webhooks da
-- mesma conversa chegando juntos leem todos `estagio = S0`, todos derivam S1
-- e todos gravam. `eventos_estagio` é histórico permanente — `metrics` usa
-- LEAD() sobre ele, e transições duplicadas viram intervalos de zero hora que
-- afundam a mediana de tempo por estágio sem que nada pareça errado.
--
-- Custo aceito: uma conversa que reabre para o mesmo estágio duas vezes
-- (SX -> S3, esfria, SX -> S3 de novo) registra só a primeira reabertura.
-- Perder esse evento raro custa menos que sujar toda a métrica de tempo.
CREATE UNIQUE INDEX IF NOT EXISTS eventos_estagio_transicao_unica
    ON eventos_estagio (conversa_id, coalesce(de, ''), para);
CREATE INDEX IF NOT EXISTS eventos_estagio_origem_idx
    ON eventos_estagio (origem, para);

CREATE TABLE IF NOT EXISTS objecoes (
    id              SERIAL PRIMARY KEY,
    conversa_id     INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    categoria       VARCHAR(24) NOT NULL CHECK (categoria IN (
                        'preco', 'frete', 'prazo', 'confianca',
                        'momento', 'alternativa', 'sem_resposta', 'outro')),
    estagio         VARCHAR(4),
    trecho          TEXT,
    em              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS objecoes_categoria_idx ON objecoes (categoria, em);

-- §7: toda correção humana grava aqui. Alimenta o eval e, pelo padrão das
-- correções, mostra o que o prompt não está vendo. Correção que só ajusta a
-- tela e não é gravada é informação jogada fora.
CREATE TABLE IF NOT EXISTS correcoes (
    id              SERIAL PRIMARY KEY,
    conversa_id     INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    campo           VARCHAR(48) NOT NULL,
    antes           TEXT,
    depois          TEXT,
    em              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    por             VARCHAR(48)
);

-- ADIÇÃO (§6): a garantia física do teto. `conversas.followups_enviados` tem
-- CHECK, mas um contador pode ser reescrito por engano; uma linha por
-- follow-up com `numero` restrito a {1,2} e UNIQUE por conversa torna o
-- terceiro envio impossível de representar, não apenas proibido.
CREATE TABLE IF NOT EXISTS followups (
    id              SERIAL PRIMARY KEY,
    conversa_id     INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    numero          SMALLINT NOT NULL CHECK (numero IN (1, 2)),
    texto           TEXT,
    enviado_em      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    UNIQUE (conversa_id, numero)
);

-- ADIÇÃO (§3, estágios "manual"): S6, P5 e P6 não derivam de fato nenhum —
-- alguém marca. Guardar o momento é o que torna P5->P6 (§14) mensurável;
-- guardar quem marcou é o que torna a correção rastreável.
CREATE TABLE IF NOT EXISTS marcos_manuais (
    id              SERIAL PRIMARY KEY,
    conversa_id     INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    marco           VARCHAR(32) NOT NULL,
    em              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    por             VARCHAR(48),
    UNIQUE (conversa_id, marco)
);
"""


class Database:
    """Pool de conexões + as consultas que o CRM precisa."""

    def __init__(self, dsn: str, *, max_size: int = 5):
        # DIVERGÊNCIA do plano original (registrada, não silenciosa): o plano
        # de `painel-leitura` previa que o painel precisaria de um pool maior
        # que 5 conexões (uma por requisição concorrente de um operador
        # atualizando várias telas). `Database` não aceitava `max_size` antes
        # deste change; o parâmetro é novo aqui, opcional e retrocompatível —
        # todo chamador existente (`cli._db`, `webhook.get_db`) continua
        # recebendo o pool de 5 conexões de sempre.
        self._dsn = dsn
        self._max_size = max_size
        self._pool: Optional[ConnectionPool] = None
        self._logger = logger

    # -- ciclo de vida ----------------------------------------------------

    def init_pool(self) -> None:
        if self._pool is None:
            self._pool = ConnectionPool(
                conninfo=self._dsn, min_size=1, max_size=self._max_size, open=True
            )

    def close(self) -> None:
        if self._pool:
            self._pool.close()
            self._pool = None

    def _conn(self):
        self.init_pool()
        assert self._pool is not None
        return self._pool.connection()

    def ensure_schema(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA)

    # -- contatos ---------------------------------------------------------

    def upsert_contato(
        self,
        telefone: str,
        *,
        nome: str | None = None,
        tipo: str = B2C,
        origem: str | None = None,
    ) -> Contato:
        """Cria ou atualiza um contato, identificado pelo hash do telefone.

        O nome só é sobrescrito quando vem preenchido: um push_name ausente
        num evento não deve apagar o nome que alguém digitou à mão.
        """
        if tipo not in (B2B, B2C):
            raise ValueError(f"tipo inválido: {tipo!r}")
        telefone_hash = hash_telefone(telefone)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO contatos (nome, telefone_hash, telefone, tipo, origem)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (telefone_hash) DO UPDATE SET
                        nome = COALESCE(EXCLUDED.nome, contatos.nome),
                        telefone = COALESCE(EXCLUDED.telefone, contatos.telefone),
                        origem = COALESCE(contatos.origem, EXCLUDED.origem)
                    RETURNING id, nome, telefone_hash, telefone, tipo, origem, criado_em
                    """,
                    (nome, telefone_hash, telefone, tipo, origem),
                )
                return Contato(*cur.fetchone())

    def set_tipo_contato(self, contato_id: int, tipo: str) -> None:
        if tipo not in (B2B, B2C):
            raise ValueError(f"tipo inválido: {tipo!r}")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE contatos SET tipo = %s WHERE id = %s", (tipo, contato_id)
                )

    def set_funil_conversa(self, conversa_id: int, funil: str) -> None:
        """Move a conversa aberta para o outro funil.

        `conversas.funil` é copiado de `contatos.tipo` na criação e não
        acompanha mudanças depois — a conversa é a unidade de análise, e
        reescrever o funil de conversas encerradas mudaria retroativamente as
        métricas de conversão já apuradas.
        """
        if funil not in (B2B, B2C):
            raise ValueError(f"funil inválido: {funil!r}")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversas SET funil = %s, atualizado_em = now() WHERE id = %s",
                    (funil, conversa_id),
                )

    # -- conversas --------------------------------------------------------

    _CONVERSA_SELECT = """
        SELECT c.id, c.contato_id, c.funil, c.estagio, c.bola_com, c.temperatura,
               c.ultimo_inbound, c.ultimo_outbound, c.followups_enviados,
               c.resultado, c.ultima_mensagem_processada_id, ct.nome
        FROM conversas c JOIN contatos ct ON ct.id = c.contato_id
    """

    def get_or_create_conversa(self, contato_id: int, funil: str | None = None) -> Conversa:
        """Conversa aberta do contato, criando uma se não houver.

        "Aberta" é a mais recente sem `resultado`. Conversa com resultado é
        histórico fechado e não recebe mensagem nova — um cliente que volta
        depois de fechado abre outra, o que mantém as métricas de conversão
        por conversa e não por pessoa.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._CONVERSA_SELECT} WHERE c.contato_id = %s "
                    "AND c.resultado IS NULL ORDER BY c.id DESC LIMIT 1",
                    (contato_id,),
                )
                row = cur.fetchone()
                if row:
                    return Conversa(*row)

                cur.execute("SELECT tipo FROM contatos WHERE id = %s", (contato_id,))
                tipo_row = cur.fetchone()
                if tipo_row is None:
                    raise ValueError(f"contato {contato_id} não existe")
                funil_final = funil or tipo_row[0]
                estagio = "P0" if funil_final == B2B else "S0"
                # `ON CONFLICT DO NOTHING` + releitura: sob corrida, quem
                # perdeu não levanta erro, apenas relê a conversa que venceu.
                cur.execute(
                    """
                    INSERT INTO conversas (contato_id, funil, estagio, bola_com)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (contato_id, funil_final, estagio, BOLA_CLIENTE),
                )
                row = cur.fetchone()
                if row is not None:
                    cur.execute(f"{self._CONVERSA_SELECT} WHERE c.id = %s", (row[0],))
                    return Conversa(*cur.fetchone())

                cur.execute(
                    f"{self._CONVERSA_SELECT} WHERE c.contato_id = %s "
                    "AND c.resultado IS NULL ORDER BY c.id LIMIT 1",
                    (contato_id,),
                )
                perdida = cur.fetchone()
                if perdida is None:
                    raise RuntimeError(
                        f"conversa do contato {contato_id} sumiu entre o conflito "
                        "e a releitura"
                    )
                return Conversa(*perdida)

    def get_conversa(self, conversa_id: int) -> Conversa | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"{self._CONVERSA_SELECT} WHERE c.id = %s", (conversa_id,))
                row = cur.fetchone()
                return Conversa(*row) if row else None

    def listar_conversas_abertas(self, limite: int = 500) -> list[Conversa]:
        """Conversas sem resultado — as candidatas à fila do dia."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._CONVERSA_SELECT} WHERE c.resultado IS NULL "
                    "ORDER BY c.atualizado_em DESC LIMIT %s",
                    (limite,),
                )
                return [Conversa(*row) for row in cur.fetchall()]

    def atualizar_estado_conversa(
        self,
        conversa_id: int,
        *,
        estagio: str | None = None,
        temperatura: str | None = None,
        bola_com: str | None = None,
        resultado: str | None = None,
        ultima_mensagem_processada_id: int | None = None,
    ) -> None:
        """Atualiza o estado derivado da conversa (cache do que as regras dizem).

        Nada aqui é fonte de verdade: `estagio` e `temperatura` são derivados
        e podem ser recalculados de `fatos` + `mensagens` a qualquer momento.
        A coluna existe para a fila não precisar reprocessar tudo a cada
        consulta — se divergir, o replay ganha.
        """
        campos, valores = [], []
        for nome, valor in (
            ("estagio", estagio),
            ("temperatura", temperatura),
            ("bola_com", bola_com),
            ("resultado", resultado),
            ("ultima_mensagem_processada_id", ultima_mensagem_processada_id),
        ):
            if valor is not None:
                campos.append(f"{nome} = %s")
                valores.append(valor)
        if not campos:
            return
        campos.append("atualizado_em = now()")
        valores.append(conversa_id)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE conversas SET {', '.join(campos)} WHERE id = %s",
                    tuple(valores),
                )

    # -- mensagens --------------------------------------------------------

    def registrar_mensagem(
        self,
        conversa_id: int,
        direcao: str,
        texto: str,
        enviada_em: datetime | None = None,
        *,
        externa_id: str | None = None,
    ) -> int | None:
        """Grava uma mensagem e atualiza `ultimo_inbound`/`ultimo_outbound`.

        Devolve `None` quando a mensagem já existia (mesmo `externa_id`) — um
        webhook reentregue não vira mensagem nova nem move o relógio da
        conversa, que é o que faria a temperatura oscilar sem ninguém ter
        falado nada.
        """
        if direcao not in (ENTRADA, SAIDA):
            raise ValueError(f"direção inválida: {direcao!r}")
        enviada_em = enviada_em or datetime.now(timezone.utc)
        coluna = "ultimo_inbound" if direcao == ENTRADA else "ultimo_outbound"
        bola = "camu" if direcao == ENTRADA else BOLA_CLIENTE
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mensagens (conversa_id, direcao, texto, enviada_em, externa_id)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    (conversa_id, direcao, texto, enviada_em, externa_id),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                cur.execute(
                    f"""
                    UPDATE conversas
                       SET {coluna} = GREATEST(COALESCE({coluna}, %s), %s),
                           bola_com = %s,
                           atualizado_em = now()
                     WHERE id = %s
                    """,
                    (enviada_em, enviada_em, bola, conversa_id),
                )
                return row[0]

    def listar_mensagens(self, conversa_id: int) -> list[Mensagem]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT direcao, enviada_em, texto FROM mensagens "
                    "WHERE conversa_id = %s ORDER BY enviada_em, id",
                    (conversa_id,),
                )
                return [Mensagem(d, e, t) for d, e, t in cur.fetchall()]

    def mensagens_novas(self, conversa_id: int, desde_id: int | None) -> list[tuple[int, str, str, datetime]]:
        """Bloco de mensagens ainda não processado pela extração (§2, delta).

        Processar só o delta é o que mantém o custo de LLM proporcional ao que
        aconteceu, e não ao tamanho do histórico.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, direcao, texto, enviada_em FROM mensagens "
                    "WHERE conversa_id = %s AND id > %s ORDER BY id",
                    (conversa_id, desde_id or 0),
                )
                return list(cur.fetchall())

    # -- fatos ------------------------------------------------------------

    def gravar_fatos(
        self,
        conversa_id: int,
        fatos: dict[str, bool],
        evidencias: dict[str, str],
        *,
        extraido_em: datetime | None = None,
        momentos: dict[str, datetime] | None = None,
    ) -> int:
        """Grava a saída da extração. Só fatos afirmados viram linha.

        Um `false` não é gravado porque não afirma nada: a ausência de linha
        já é o estado padrão, e gravar negativas encheria a tabela de ruído
        sem tornar nada mais reconstituível. Devolve quantas linhas novas
        entraram (0 quando o bloco foi reprocessado sem novidade).
        """
        extraido_em = extraido_em or datetime.now(timezone.utc)
        afirmados = [(c, v) for c, v in fatos.items() if v]
        if not afirmados:
            return 0
        with self._conn() as conn:
            with conn.cursor() as cur:
                inseridos = 0
                for chave, _ in afirmados:
                    cur.execute(
                        """
                        INSERT INTO fatos
                            (conversa_id, chave, valor, evidencia, extraido_em, mensagem_em)
                        VALUES (%s, %s, TRUE, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """,
                        (
                            conversa_id,
                            chave,
                            evidencias.get(chave),
                            extraido_em,
                            (momentos or {}).get(chave),
                        ),
                    )
                    if cur.fetchone():
                        inseridos += 1
                return inseridos

    def fatos_da_conversa(self, conversa_id: int) -> dict[str, bool]:
        """Estado corrente dos fatos: existe alguma afirmação verdadeira."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chave FROM fatos WHERE conversa_id = %s AND valor "
                    "GROUP BY chave",
                    (conversa_id,),
                )
                return {chave: True for (chave,) in cur.fetchall()}

    def fato_registrado_em(self, conversa_id: int, chave: str) -> datetime | None:
        """Quando o fato aconteceu — o momento da mensagem que o evidencia.

        Cai para `extraido_em` só quando a evidência não pôde ser localizada
        numa mensagem. É esse timestamp que `sinais.construir_sinais` usa para
        decidir S5 e P3, então a precedência importa: o momento da extração é
        um artefato do pipeline, o da mensagem é o fato.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MIN(COALESCE(mensagem_em, extraido_em)) FROM fatos "
                    "WHERE conversa_id = %s AND chave = %s AND valor",
                    (conversa_id, chave),
                )
                return cur.fetchone()[0]

    # -- eventos de estágio -----------------------------------------------

    def gravar_evento_estagio(
        self,
        conversa_id: int,
        de: str | None,
        para: str,
        *,
        origem: str = ORIGEM_LIVE,
        motivo: str | None = None,
        em: datetime | None = None,
    ) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO eventos_estagio (conversa_id, de, para, em, origem, motivo)
                    VALUES (%s, %s, %s, COALESCE(%s, now()), %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (conversa_id, de, para, em, origem, motivo),
                )

    def estagio_maximo_alcancado(self, conversa_id: int) -> str | None:
        """Maior estágio já registrado, para reabrir conversa que voltou (§3)."""
        from .taxonomia import is_terminal, rank_estagio

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT para FROM eventos_estagio WHERE conversa_id = %s",
                    (conversa_id,),
                )
                estagios = [p for (p,) in cur.fetchall() if not is_terminal(p)]
        return max(estagios, key=rank_estagio) if estagios else None

    def estagio_corrente(self, conversa_id: int) -> str | None:
        """Estágio segundo o histórico de eventos — a fonte de verdade.

        `conversas.estagio` é cache. Sem esta consulta ele não seria
        recuperável: a regra de não-regressão (§3) protege qualquer valor que
        esteja lá, inclusive um errado para cima, e nenhum recálculo
        conseguiria baixá-lo. Um estágio inflado por um fato que depois foi
        removido — correção humana, dado de teste apagado, extração revista —
        ficaria preso para sempre, e a conversa sairia da fila pela regra
        errada.

        Ordenado por `id` e não por `em`: `em` é o momento que disparou a
        transição (a mensagem, o marco), e transições gravadas na mesma
        passada podem ter timestamps fora de ordem em relação à conclusão
        lógica. A ordem de inserção é a ordem em que o sistema concluiu.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT para FROM eventos_estagio WHERE conversa_id = %s "
                    "ORDER BY id DESC LIMIT 1",
                    (conversa_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def estagios_registrados(self, conversa_id: int) -> set[str]:
        """Estágios que já têm evento gravado — torna o backfill reexecutável."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT para FROM eventos_estagio WHERE conversa_id = %s",
                    (conversa_id,),
                )
                return {p for (p,) in cur.fetchall()}

    def ultimo_avanco_em(self, conversa_id: int) -> datetime | None:
        """Timestamp do último avanço ao vivo (§5, sinal "avançou hoje").

        Só `origem = 'live'`: um evento de backfill carrega o momento do
        backfill, não o do avanço, e o trataria como se tivesse acabado de
        acontecer — deixando quente uma conversa parada há meses.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(em) FROM eventos_estagio "
                    "WHERE conversa_id = %s AND origem = 'live'",
                    (conversa_id,),
                )
                return cur.fetchone()[0]

    # -- objeções ---------------------------------------------------------

    def gravar_objecao(
        self,
        conversa_id: int,
        categoria: str,
        *,
        estagio: str | None = None,
        trecho: str | None = None,
        em: datetime | None = None,
    ) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO objecoes (conversa_id, categoria, estagio, trecho, em)
                    VALUES (%s, %s, %s, %s, COALESCE(%s, now()))
                    """,
                    (conversa_id, categoria, estagio, trecho, em),
                )

    def distribuicao_objecoes(self, desde: datetime | None = None) -> dict[str, int]:
        """Contagem por categoria — insumo da revisão mensal da §4."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                if desde:
                    cur.execute(
                        "SELECT categoria, COUNT(*) FROM objecoes WHERE em >= %s "
                        "GROUP BY categoria",
                        (desde,),
                    )
                else:
                    cur.execute("SELECT categoria, COUNT(*) FROM objecoes GROUP BY categoria")
                return dict(cur.fetchall())

    # -- follow-ups (§6) --------------------------------------------------

    def registrar_followup(self, conversa_id: int, texto: str | None = None) -> int:
        """Registra um follow-up enviado. O banco recusa o terceiro.

        A contagem e a linha são gravadas na mesma transação: se o CHECK de
        `followups_enviados` ou o UNIQUE de `followups` recusar, nada fica
        gravado e nenhuma mensagem é dada como enviada.
        """
        with self._conn() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT followups_enviados FROM conversas WHERE id = %s FOR UPDATE",
                        (conversa_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise ValueError(f"conversa {conversa_id} não existe")
                    numero = row[0] + 1
                    cur.execute(
                        "INSERT INTO followups (conversa_id, numero, texto) VALUES (%s, %s, %s)",
                        (conversa_id, numero, texto),
                    )
                    cur.execute(
                        "UPDATE conversas SET followups_enviados = %s, atualizado_em = now() "
                        "WHERE id = %s",
                        (numero, conversa_id),
                    )
                    return numero
            except psycopg.errors.CheckViolation as exc:
                raise TetoFollowupError(
                    f"conversa {conversa_id} já atingiu o teto de {MAX_FOLLOWUPS} follow-ups"
                ) from exc
            except psycopg.errors.UniqueViolation as exc:
                raise TetoFollowupError(
                    f"follow-up duplicado na conversa {conversa_id}"
                ) from exc

    def ultimo_followup_em(self, conversa_id: int) -> datetime | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(enviado_em) FROM followups WHERE conversa_id = %s",
                    (conversa_id,),
                )
                return cur.fetchone()[0]

    # -- marcos manuais (§3) ----------------------------------------------

    def registrar_marco(
        self, conversa_id: int, marco: str, *, por: str | None = None
    ) -> None:
        if marco not in MARCOS_MANUAIS:
            raise ValueError(f"marco inválido: {marco!r} (use {MARCOS_MANUAIS})")
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO marcos_manuais (conversa_id, marco, por) VALUES (%s, %s, %s) "
                    "ON CONFLICT (conversa_id, marco) DO NOTHING",
                    (conversa_id, marco, por),
                )

    def marco_em(self, conversa_id: int, marco: str) -> datetime | None:
        """Quando o marco manual foi registrado — carimba o evento de S6/P5/P6."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT em FROM marcos_manuais WHERE conversa_id = %s AND marco = %s",
                    (conversa_id, marco),
                )
                row = cur.fetchone()
                return row[0] if row else None

    def marcos_da_conversa(self, conversa_id: int) -> set[str]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT marco FROM marcos_manuais WHERE conversa_id = %s",
                    (conversa_id,),
                )
                return {m for (m,) in cur.fetchall()}

    # -- correções (§7) ---------------------------------------------------

    def registrar_correcao(
        self,
        conversa_id: int,
        campo: str,
        antes: Any,
        depois: Any,
        *,
        por: str | None = None,
    ) -> None:
        """Grava uma correção humana. Chamada por *toda* correção, sem exceção.

        §7: correção que só ajusta a tela e não é gravada é informação jogada
        fora. As duas funções são alimentar o eval e revelar, pelo padrão, o
        que o prompt não está vendo.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO correcoes (conversa_id, campo, antes, depois, por) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (conversa_id, campo, _texto(antes), _texto(depois), por),
                )

    def listar_correcoes(self, limite: int = 200) -> list[tuple[int, str, str, str, datetime]]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT conversa_id, campo, antes, depois, em FROM correcoes "
                    "ORDER BY em DESC LIMIT %s",
                    (limite,),
                )
                return list(cur.fetchall())

    # -- leitura do painel (§13, antecipado) -------------------------------
    #
    # Todo método abaixo é só-leitura, sem N+1 escondido dentro dele: cada um
    # é uma única consulta. O N+1 entre conversas do painel (uma chamada
    # destas por card) é aceito conscientemente no lado do chamador
    # (`camucrm.painel.api`), não escondido aqui.

    def fatos_detalhados(self, conversa_id: int) -> list[FatoRegistro]:
        """Fatos com evidência, linha a linha — não agregados por chave.

        Diferente de `fatos_da_conversa` (que faz `GROUP BY` e devolve só o
        booleano corrente), o painel precisa mostrar a evidência literal que
        sustenta cada afirmação (invariante 1 do CLAUDE.md), e a mesma chave
        pode ter mais de uma linha ao longo do tempo.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chave, valor, evidencia, extraido_em, mensagem_em "
                    "FROM fatos WHERE conversa_id = %s AND valor "
                    "ORDER BY extraido_em",
                    (conversa_id,),
                )
                return [FatoRegistro(*row) for row in cur.fetchall()]

    def eventos_da_conversa(self, conversa_id: int) -> list[EventoRegistro]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT de, para, em, origem, motivo FROM eventos_estagio "
                    "WHERE conversa_id = %s ORDER BY id",
                    (conversa_id,),
                )
                return [EventoRegistro(*row) for row in cur.fetchall()]

    def objecoes_da_conversa(self, conversa_id: int) -> list[ObjecaoRegistro]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, categoria, estagio, trecho, em FROM objecoes "
                    "WHERE conversa_id = %s ORDER BY em",
                    (conversa_id,),
                )
                return [ObjecaoRegistro(*row) for row in cur.fetchall()]

    def followups_da_conversa(self, conversa_id: int) -> list[FollowupRegistro]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT numero, texto, enviado_em FROM followups "
                    "WHERE conversa_id = %s ORDER BY numero",
                    (conversa_id,),
                )
                return [FollowupRegistro(*row) for row in cur.fetchall()]

    def marcos_detalhados(self, conversa_id: int) -> list[MarcoRegistro]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT marco, em, por FROM marcos_manuais "
                    "WHERE conversa_id = %s ORDER BY em",
                    (conversa_id,),
                )
                return [MarcoRegistro(*row) for row in cur.fetchall()]

    def correcoes_da_conversa(self, conversa_id: int) -> list[CorrecaoRegistro]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, campo, antes, depois, em, por FROM correcoes "
                    "WHERE conversa_id = %s ORDER BY em DESC",
                    (conversa_id,),
                )
                return [CorrecaoRegistro(*row) for row in cur.fetchall()]

    def listar_mensagens_registradas(
        self,
        *,
        conversa_id: int | None = None,
        desde_id: int | None = None,
        limite: int = 200,
    ) -> list[MensagemRegistro]:
        """Mensagens gravadas, opcionalmente restritas a uma conversa.

        Usada tanto por `GET /api/conversas/{id}/mensagens` (com
        `conversa_id`) quanto, futuramente, por um feed global — mesma
        consulta, um parâmetro a mais, para não duplicar SQL entre os dois
        usos.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                if conversa_id is not None:
                    cur.execute(
                        "SELECT id, conversa_id, direcao, texto, enviada_em "
                        "FROM mensagens WHERE conversa_id = %s AND id > %s "
                        "ORDER BY id LIMIT %s",
                        (conversa_id, desde_id or 0, limite),
                    )
                else:
                    cur.execute(
                        "SELECT id, conversa_id, direcao, texto, enviada_em "
                        "FROM mensagens WHERE id > %s ORDER BY id LIMIT %s",
                        (desde_id or 0, limite),
                    )
                return [MensagemRegistro(*row) for row in cur.fetchall()]

    def ultimas_mensagens_globais(
        self, limite: int = 8
    ) -> list[tuple[str, str, datetime, str]]:
        """As últimas mensagens de toda a base, mais nova por último.

        Movido de `cli._ultimas_mensagens` para cá: era o único SQL cru fora
        de `db.py` (CLAUDE.md: "db.py é o único lugar do repo com SQL"). O
        formato de retorno é o que `cli._desenhar` já consumia — mantido para
        não duplicar a normalização (strip de quebra de linha, fuso local,
        ordem cronológica) entre `cli.acompanhar` e o painel.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT m.direcao, m.texto, m.enviada_em, COALESCE(t.nome, '')
                      FROM mensagens m
                      JOIN conversas c ON c.id = m.conversa_id
                      JOIN contatos t ON t.id = c.contato_id
                     ORDER BY m.enviada_em DESC LIMIT %s
                    """,
                    (limite,),
                )
                linhas = cur.fetchall()
        return [
            (d, (t or "").replace("\n", " "), q.astimezone(), n)
            for d, t, q, n in reversed(linhas)
        ]

    def contato_resumido(self, conversa_id: int) -> ContatoResumido | None:
        """Resumo do contato de uma conversa — telefone NUNCA em claro (§12).

        `tem_telefone` é o único jeito de o painel saber que o número existe:
        suficiente para o operador confirmar que dá para enviar por
        `camucrm enviar`, sem que o número em si trafegue pela API de leitura.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ct.id, ct.nome, ct.tipo,
                           (ct.telefone IS NOT NULL) AS tem_telefone, ct.criado_em
                      FROM conversas c
                      JOIN contatos ct ON ct.id = c.contato_id
                     WHERE c.id = %s
                    """,
                    (conversa_id,),
                )
                row = cur.fetchone()
                return ContatoResumido(*row) if row else None

    # -- retenção (§12) ---------------------------------------------------

    def purgar_mensagens_antigas(self, meses: int = 12) -> int:
        """Descarta `mensagens` de conversas encerradas há mais de `meses`.

        §12: mantém `fatos`, `objecoes` e `eventos_estagio` — que é o que
        serve para análise e não guarda conteúdo pessoal. O telefone em claro
        também sai, porque o motivo de guardá-lo era poder enviar, e uma
        conversa encerrada há um ano não recebe envio.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM mensagens m USING conversas c
                     WHERE m.conversa_id = c.id
                       AND c.resultado IS NOT NULL
                       AND c.atualizado_em < now() - make_interval(months => %s)
                    """,
                    (meses,),
                )
                apagadas = cur.rowcount
                cur.execute(
                    """
                    UPDATE contatos SET telefone = NULL
                     WHERE telefone IS NOT NULL
                       AND id IN (
                           SELECT contato_id FROM conversas
                            GROUP BY contato_id
                           HAVING bool_and(resultado IS NOT NULL)
                              AND max(atualizado_em) < now() - make_interval(months => %s)
                       )
                    """,
                    (meses,),
                )
                return apagadas


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    return valor if isinstance(valor, str) else str(valor)
