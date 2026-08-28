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
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

import psycopg
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from .prospeccao import normalizar_telefone_br
from .rules.estagio import ORIGEM_LIVE
from .rules.sinais import ENTRADA, SAIDA, Mensagem
from .taxonomia import B2B, B2C, BOLA_CLIENTE, MAX_FOLLOWUPS

logger = logging.getLogger("camucrm.db")

# §12: telefone com hash para lookup; original apenas onde necessário para
# envio. O salt precisa ser estável — trocá-lo invalida todo lookup existente.
ENV_TELEFONE_SALT = "CAMU_TELEFONE_SALT"

# Marcos que só um humano registra (§3). Fechados como os demais vocabulários.
MARCOS_MANUAIS = ("ganho", "consignacao_assinada", "primeira_reposicao", "perdido")

# Change `ingestao-a-prova-de-falha`, design.md: janela de retenção da caixa
# de reprocessamento (`eventos_recebidos_bruto`) — só para linhas já
# `processado = TRUE`. Linhas com falha pendente (`processado = FALSE`)
# NUNCA são apagadas automaticamente, não importa a idade (ver
# `Database.purgar_eventos_brutos_antigos`); apagar uma falha não resolvida
# repetiria exatamente o bug que este change corrige.
RETENCAO_EVENTOS_BRUTOS_DIAS = 14


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


def _normalizar_texto(texto: str | None) -> str:
    """strip + colapso de espaço + casefold — a ÚNICA definição de igualdade
    que a reconciliação pelo eco (change `rascunho-registrado`, design.md)
    usa. Vive aqui, não duplicada em `acoes.py`, para as duas pontas da
    comparação (o texto vindo do eco da Evolution e o texto gravado em
    `rascunhos.opcao_1/opcao_2/texto_final`) nunca divergirem por acidente.

    Deliberadamente sem fuzzy matching: `None`/string vazia normaliza para
    `""`, que nunca casa com nada (ver o guard em `rascunho_pendente_por_texto`).
    """
    if not texto:
        return ""
    return " ".join(texto.split()).casefold()


def _condicao_teste(coluna: str, *, incluir_teste: bool, apenas_teste: bool) -> str:
    """Fragmento SQL (começando com `AND`) que decide quais contatos entram
    numa leitura agregada (change `contatos-de-teste-isolados`).

    `coluna` é a referência qualificada de `contatos.e_teste` na consulta
    (ex. `"ct.e_teste"`). Três modos, nunca dois juntos:

    - Padrão (os dois `False`): exclui teste — kanban/fila/métricas reais.
    - `apenas_teste=True`: mostra só teste — painel com "Modo teste" ligado.
    - `incluir_teste=True` (sem `apenas_teste`): mostra os dois juntos, sem
      filtro nenhum — só a CLI expõe isso (`camucrm fila --incluir-teste`),
      para depuração via terminal; o painel nunca usa este modo, porque o
      requirement "Modo teste nunca mistura as duas visões na mesma tela"
      proíbe uma tela com os dois ao mesmo tempo.

    Os literais `TRUE`/`FALSE` vão embutidos no SQL, não como parâmetro
    (`%s`) — não há dado de usuário aqui, só um enum interno de dois
    valores, e isso evita threading um parâmetro extra por toda consulta
    que usa este fragmento.
    """
    if incluir_teste and apenas_teste:
        raise ValueError(
            "incluir_teste e apenas_teste não podem ser usados ao mesmo tempo "
            "(nunca misturar as duas visões — requirement do change "
            "`contatos-de-teste-isolados`)"
        )
    if apenas_teste:
        return f"AND {coluna} = TRUE"
    if incluir_teste:
        return ""
    return f"AND {coluna} = FALSE"


def _float_ou_none(valor: Any) -> float | None:
    """Change `prospeccao-b2b-shortlist`: campo numérico da planilha
    (`nota`) tolerante a célula vazia/malformada — só o telefone ilegível
    reprova a linha inteira (requirement "Importação nunca descarta linha em
    silêncio"); um `nota`/`avaliacoes` estranho vira `NULL`, não erro.
    """
    texto = (str(valor) if valor is not None else "").strip()
    if not texto:
        return None
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        return None


def _int_ou_none(valor: Any) -> int | None:
    texto = (str(valor) if valor is not None else "").strip()
    if not texto:
        return None
    try:
        return int(float(texto))
    except ValueError:
        return None


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
    # ADIÇÃO (change `contatos-de-teste-isolados`): marca por CONTATO, não
    # por conversa — toda conversa passada e futura do contato fica de teste
    # junto. `default=False` para não quebrar construções posicionais
    # antigas deste dataclass que ainda não conhecem a coluna.
    e_teste: bool = False

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
    # ADIÇÃO (change `estagio-reabertura-manual-e-relogio`): quem causou o
    # avanço — "cliente" ou "camu" (`rules.estagio.Transicao.causada_por`).
    # Default preserva a assinatura posicional já usada em testes existentes.
    causada_por: str = "cliente"


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
    # ADIÇÃO (change `contatos-de-teste-isolados`): o botão "marcar/desmarcar
    # contato de teste" do detalhe da conversa precisa saber o estado atual.
    e_teste: bool = False


@dataclass
class RascunhoRegistro:
    """Uma linha de `rascunhos` (change `rascunho-registrado`, §10).

    Sufixo `Registro` para não colidir com `drafts.Rascunho` (o resultado em
    memória de `drafts.gerar`, sem id nem persistência) nem com as
    dataclasses de escrita do topo do arquivo. `NULL` em `escolhida` é
    resultado — rascunho gerado e ainda não usado — não lacuna de dado.
    """

    id: int
    conversa_id: int
    estagio: str
    temperatura: str
    funil: str
    objecao: str | None
    followups_enviados: int
    opcao_1: str | None
    opcao_2: str | None
    avisos: str | None
    encerrar: bool
    motivo: str | None
    modelo: str | None
    prompt_versao: str | None
    gerado_em: datetime
    gerado_por: str | None
    escolhida: int | None
    texto_final: str | None
    escolhido_em: datetime | None
    escolhido_por: str | None
    mensagem_id: int | None
    estagio_no_envio: str | None


@dataclass
class RascunhoVinculadoRegistro:
    """Insumo cru do A/B natural de rascunho (change `analise-desempenho`).

    Uma linha por rascunho com `mensagem_id` vinculado. `estagios_72h` é a
    lista (sem duplicar) de estágios alcançados pela conversa na janela de
    72h após o envio da mensagem vinculada — cabe a `metrics.py`, não a este
    módulo, decidir se algum deles representa avanço (comparar rank é regra
    de domínio, `taxonomia.rank_estagio`, não SQL).
    """

    rascunho_id: int
    escolhida: int | None
    editado: bool
    estagio_no_envio: str | None
    estagios_72h: list[str]


@dataclass
class ResumoConversa:
    """Uma linha de `resumos_conversa` (change `resumo-conversa`).

    FOLHA do grafo — ver docstring de `camucrm/summaries.py`. `estagio` e
    `temperatura` são copiados no momento da geração (mesma convenção de
    `RascunhoRegistro`): o que a tela mostra é "o resumo foi escrito a
    partir DESTE estado", não o estado atual, que pode já ter mudado.
    """

    id: int
    conversa_id: int
    resumo: str | None
    proximo_passo: str | None
    ultima_mensagem_id: int | None
    estagio: str
    temperatura: str
    prompt_versao: str
    modelo: str | None
    gerado_em: datetime
    gerado_por: str | None


@dataclass
class EventoBrutoRegistro:
    """Uma linha de `eventos_recebidos_bruto` (change `ingestao-a-prova-de-
    falha`, design.md). Staging do payload cru do webhook, gravado ANTES de
    qualquer parsing/`ingerir()` — ver docstring de `Database.
    registrar_evento_bruto`.
    """

    id: int
    payload: Any
    recebido_em: datetime
    processado: bool
    processado_em: datetime | None
    erro: str | None
    tentativas: int


@dataclass
class LinhaInvalida:
    """Uma linha da planilha de prospecção que não virou linha de
    `prospeccoes` (change `prospeccao-b2b-shortlist`, requirement
    "Importação nunca descarta linha em silêncio"). `linha` é a posição
    (1-based, sem contar o cabeçalho) dentro do arquivo importado — o que
    permite ao operador achar a linha na planilha original."""

    linha: int
    petshop: str | None
    motivo: str


@dataclass
class ResumoImportacao:
    """Resultado de `Database.importar_prospeccoes` — nunca descarta linha
    em silêncio: toda linha da planilha vira `novos`/`atualizados` OU uma
    entrada em `invalidas`, nunca some sem indicação."""

    novos: int
    atualizados: int
    invalidas: list[LinhaInvalida]


@dataclass
class ProspeccaoRegistro:
    """Uma linha de `prospeccoes` (change `prospeccao-b2b-shortlist`),
    já com a detecção de conversão resolvida pelo `LEFT JOIN` de
    `Database.listar_prospeccoes` (design.md: "sem estado próprio").
    `contato_id`/`conversa_id` são `None` quando a linha ainda não virou
    conversa real, OU quando o registro veio de
    `Database.prospeccao_por_telefone_hash` (que não faz o join — quem
    chama, `ingest.ingerir`, só precisa saber se a linha existe, não do
    estado de conversão)."""

    id: int
    nome: str
    telefone: str
    bairro: str | None
    zona: str | None
    nota: float | None
    avaliacoes: int | None
    site: str | None
    tier_origem: str | None
    status_origem: str | None
    aberto_em: datetime | None
    aberto_por: str | None
    criado_em: datetime
    # ADIÇÃO (change `envio-prospeccao-pela-evolution-api`): resultado da
    # última tentativa de envio pela API — ver comentário do schema. Vêm
    # ANTES de contato_id/conversa_id na ordem posicional de propósito: os
    # dois SELECTs que constroem este dataclass (`_PROSPECCAO_SELECT` e o de
    # `listar_prospeccoes`) usam `ProspeccaoRegistro(*row)` — mudar a ordem
    # aqui exige mudar a ordem das colunas nos dois SELECTs, não só num.
    enviado_em: datetime | None = None
    enviado_por: str | None = None
    enviado_erro: str | None = None
    contato_id: int | None = None
    conversa_id: int | None = None


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
    criado_em       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    -- ADIÇÃO (change `contatos-de-teste-isolados`): marca por CONTATO — toda
    -- conversa passada e futura do contato fica de teste junto, sem marcar
    -- conversa por conversa. Só manual (§1, mesmo princípio estendido):
    -- nenhuma heurística decide isso sozinha, e por isso não há default
    -- calculado, só `FALSE` fixo.
    e_teste         BOOLEAN NOT NULL DEFAULT FALSE
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
    motivo          VARCHAR(120),
    -- ADIÇÃO (change `estagio-reabertura-manual-e-relogio`): quem causou o
    -- avanço — "cliente" (respondeu, mandou foto, autorizou, pagou) ou
    -- "camu" (mandou prévia, apresentou preço, entregou proposta B2B sem
    -- resposta ainda). §5: "avançou hoje" só esquenta quando é reciprocidade
    -- do cliente, não atividade nossa — `metrics`/`rules.temperatura`
    -- consultam esta coluna via `ultimo_avanco_causada_por`.
    causada_por     VARCHAR(16) NOT NULL DEFAULT 'cliente'
                    CHECK (causada_por IN ('cliente', 'camu'))
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
-- ADIÇÃO (§2, idempotência): sem isto, reprocessamento — a regressão do
-- watermark de `ultima_mensagem_processada_id` acima, OU qualquer
-- `forcar=True` (`camucrm extrair --forcar`, `make backfill` reexecutado) —
-- duplica a linha de objeção a cada rodada, poluindo permanentemente
-- `distribuicao_objecoes` (a revisão mensal da §4). Mesma família de solução
-- já usada em `fatos_dedupe_idx`: `md5(coalesce(trecho, ''))` porque duas
-- ocorrências da mesma objeção sem trecho (`trecho IS NULL`) também devem
-- deduplicar, e `NULL != NULL` em SQL não bloquearia isso sem o coalesce.
CREATE UNIQUE INDEX IF NOT EXISTS objecoes_dedupe_idx
    ON objecoes (conversa_id, categoria, coalesce(estagio, ''), md5(coalesce(trecho, '')));

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

-- ADIÇÃO (§10, change `rascunho-registrado`): `drafts.gerar` produz duas
-- opções e, até este change, tudo era descartado depois de impresso na CLI.
-- Sem registro, aprendizado agregado (opção 1 vs opção 2, aceito sem edição
-- vs editado) é impossível com ou sem modelo — ver design.md do change.
--
-- Contexto (estagio/temperatura/funil/objecao/followups_enviados) é
-- COPIADO no momento da geração, não referenciado: a pergunta que a tabela
-- responde é "o que esta abordagem produziu a partir DAQUI", e o estado da
-- conversa muda depois. `NULL` em `escolhida` é resultado (gerado e
-- descartado é o sinal mais barato de que o prompt errou), não lacuna.
CREATE TABLE IF NOT EXISTS rascunhos (
    id                  SERIAL PRIMARY KEY,
    conversa_id         INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    estagio             VARCHAR(4) NOT NULL,
    temperatura         VARCHAR(16) NOT NULL,
    funil               VARCHAR(8) NOT NULL CHECK (funil IN ('b2b', 'b2c')),
    objecao             VARCHAR(24),
    followups_enviados  INTEGER NOT NULL DEFAULT 0,
    opcao_1             TEXT,
    opcao_2             TEXT,
    avisos              TEXT,
    -- §10: recusar-se a rascunhar (teto de follow-up, FRIO já tocado) é
    -- resposta legítima, não falha. `motivo` some quando não há recusa.
    encerrar            BOOLEAN NOT NULL DEFAULT FALSE,
    motivo              VARCHAR(160),
    modelo              VARCHAR(64),
    prompt_versao       VARCHAR(16),
    gerado_em           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    gerado_por          VARCHAR(48),
    -- Escolha humana: 1/2 quando usou uma das opções tal como veio;
    -- `texto_final` quando editou (junto com `escolhida`) ou escreveu do
    -- zero (sozinho). Ver `rascunhos_escolha` abaixo.
    escolhida           SMALLINT CHECK (escolhida IN (1, 2)),
    texto_final         TEXT,
    escolhido_em        TIMESTAMP WITH TIME ZONE,
    escolhido_por       VARCHAR(48),
    -- Vínculo com a mensagem realmente enviada (design.md: três caminhos,
    -- do mais confiável ao menos — flag da CLI, reconciliação pelo eco,
    -- registro manual sem vínculo). `estagio_no_envio` é o estágio da
    -- conversa no momento do envio, não no momento da geração — os dois
    -- podem divergir quando o rascunho fica pendente por um tempo.
    mensagem_id         INTEGER REFERENCES mensagens(id) ON DELETE SET NULL,
    estagio_no_envio    VARCHAR(4),
    -- Nunca meia geração: ou as duas opções, ou recusa com motivo.
    CONSTRAINT rascunhos_forma CHECK (
        (NOT encerrar AND opcao_1 IS NOT NULL AND opcao_2 IS NOT NULL)
        OR (encerrar AND motivo IS NOT NULL AND opcao_1 IS NULL AND opcao_2 IS NULL)
    ),
    -- Escolha registrada sempre tem `escolhido_em`; `escolhida IS NULL` com
    -- `texto_final` preenchido é válido — o humano escreveu do zero, sem
    -- usar nenhuma das duas opções.
    CONSTRAINT rascunhos_escolha CHECK (
        (escolhido_em IS NULL AND escolhida IS NULL AND texto_final IS NULL)
        OR (escolhido_em IS NOT NULL AND (escolhida IS NOT NULL OR texto_final IS NOT NULL))
    )
);
CREATE INDEX IF NOT EXISTS rascunhos_conversa_idx ON rascunhos (conversa_id, gerado_em);
-- Nenhum mensagem_id reivindicado por dois rascunhos — dois rascunhos
-- "vencedores" da mesma mensagem dobrariam a contagem de "esta abordagem
-- avançou o estágio" de um jeito plausível e errado.
CREATE UNIQUE INDEX IF NOT EXISTS rascunhos_mensagem_unica
    ON rascunhos (mensagem_id) WHERE mensagem_id IS NOT NULL;

-- ADIÇÃO (change `resumo-conversa`): terceira superfície de LLM do sistema
-- (ver docstring de `camucrm/summaries.py` e a seção "A divisão que não pode
-- ser quebrada (§1)" do CLAUDE.md para a divergência registrada). FOLHA do
-- grafo: nenhuma regra de `camucrm/rules/` lê esta tabela, e apagá-la
-- inteira não muda `estagio`/`temperatura`/fila de nenhuma conversa — é
-- cache de leitura humana, não fonte de verdade.
--
-- `ultima_mensagem_id` é a fronteira do que o resumo viu: staleness é
-- CONTAGEM de mensagens acima deste id (`Database.mensagens_desde`), não
-- diferença de timestamp — uma conversa pode ficar dias sem mensagem nova
-- sem que o resumo fique desatualizado por isso.
CREATE TABLE IF NOT EXISTS resumos_conversa (
    id                  SERIAL PRIMARY KEY,
    conversa_id         INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    resumo              TEXT,
    proximo_passo       TEXT,
    ultima_mensagem_id  INTEGER REFERENCES mensagens(id) ON DELETE SET NULL,
    estagio             VARCHAR(4) NOT NULL,
    temperatura         VARCHAR(16) NOT NULL,
    prompt_versao       VARCHAR(16) NOT NULL,
    modelo              VARCHAR(64),
    gerado_em           TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    gerado_por          VARCHAR(48)
);
CREATE INDEX IF NOT EXISTS resumos_conversa_conversa_idx
    ON resumos_conversa (conversa_id, gerado_em);
-- Clicar "gerar"/"regerar" duas vezes na mesma fronteira (mesma última
-- mensagem vista, mesma versão de prompt) não duplica linha — vira
-- `ON CONFLICT ... DO UPDATE` em `Database.gravar_resumo`.
-- `coalesce(ultima_mensagem_id, 0)` porque NULL != NULL em SQL não
-- bloquearia duas conversas ainda sem mensagem nenhuma.
CREATE UNIQUE INDEX IF NOT EXISTS resumos_conversa_cursor_idx
    ON resumos_conversa (conversa_id, coalesce(ultima_mensagem_id, 0), prompt_versao);

-- ADIÇÃO (change `ingestao-a-prova-de-falha`, design.md): staging do payload
-- bruto do webhook, gravado ANTES de qualquer parsing/`ingerir()`. Uma
-- exceção dentro de `ingerir()` não perde o evento — a linha permanece
-- `processado = FALSE` com `erro` preenchido, disponível para
-- `camucrm reprocessar-falhas`. NÃO é histórico permanente (fora de escopo
-- do proposal.md): só linhas `processado = TRUE` saem, depois de
-- `RETENCAO_EVENTOS_BRUTOS_DIAS`, via `purgar_eventos_brutos_antigos`; uma
-- linha `processado = FALSE` nunca é apagada automaticamente.
CREATE TABLE IF NOT EXISTS eventos_recebidos_bruto (
    id              SERIAL PRIMARY KEY,
    recebido_em     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    payload         JSONB NOT NULL,
    processado      BOOLEAN NOT NULL DEFAULT FALSE,
    processado_em   TIMESTAMP WITH TIME ZONE,
    erro            TEXT,
    tentativas      INTEGER NOT NULL DEFAULT 0
);
-- Índice parcial: só as linhas que `listar_eventos_brutos_pendentes` lê.
CREATE INDEX IF NOT EXISTS eventos_recebidos_bruto_pendentes_idx
    ON eventos_recebidos_bruto (id) WHERE NOT processado;

-- ADIÇÃO (change `prospeccao-b2b-shortlist`, design.md): shortlist B2B
-- levantada externamente (petshops, base legal = legítimo interesse B2B,
-- §12 do documento — ver `openspec/project.md`), inteiramente separada de
-- `contatos`/`conversas`. `telefone_hash` reaproveita `hash_telefone` (mesmo
-- salt/normalização de `contatos`) para dedupe na reimportação E para a
-- detecção de conversão via `LEFT JOIN` em `Database.listar_prospeccoes` —
-- sem coluna própria, sem job de sincronização (design.md: "sempre correto
-- no momento da leitura"). Tabela inteiramente NOVA: `CREATE TABLE IF NOT
-- EXISTS` já é suficiente para um banco de desenvolvimento existente ganhar
-- a tabela sem precisar recriar o volume — não há coluna sendo retrofitada
-- numa tabela antiga (diferente de `contatos.e_teste`, que precisou de
-- `ALTER TABLE`).
CREATE TABLE IF NOT EXISTS prospeccoes (
    id              SERIAL PRIMARY KEY,
    nome            VARCHAR(200) NOT NULL,
    telefone        VARCHAR(32) NOT NULL,
    telefone_hash   VARCHAR(64) NOT NULL UNIQUE,
    bairro          VARCHAR(120),
    zona            VARCHAR(60),
    nota            NUMERIC(2,1),
    avaliacoes      INTEGER,
    site            VARCHAR(300),
    tier_origem     VARCHAR(8),
    status_origem   VARCHAR(60),
    aberto_em       TIMESTAMP WITH TIME ZONE,
    aberto_por      VARCHAR(48),
    criado_em       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    atualizado_em   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prospeccoes_zona_idx ON prospeccoes (zona, bairro);
-- ADIÇÃO (change `envio-prospeccao-pela-evolution-api`): resultado da ÚLTIMA
-- tentativa de envio pela Evolution API — distinto em espécie de
-- aberto_em/aberto_por acima. Aquele é intenção (clicou no link `wa.me`,
-- sem confirmação de que a mensagem saiu); estas colunas só são gravadas
-- depois que a Evolution API respondeu com sucesso, ou registram o erro de
-- uma tentativa que falhou. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
-- porque `prospeccoes` já existe em banco de desenvolvimento (diferente da
-- tabela em si, que nasceu com `CREATE TABLE IF NOT EXISTS` acima).
ALTER TABLE prospeccoes ADD COLUMN IF NOT EXISTS enviado_em TIMESTAMP WITH TIME ZONE;
ALTER TABLE prospeccoes ADD COLUMN IF NOT EXISTS enviado_por VARCHAR(48);
ALTER TABLE prospeccoes ADD COLUMN IF NOT EXISTS enviado_erro TEXT;

-- ADIÇÃO (change `backfill-cobertura-por-prompt`): até onde CADA versão de
-- prompt de extração já leu uma conversa. Existe para `forcar=True`
-- (backfill/`camucrm extrair --forcar`) não relere do zero uma conversa que
-- a versão de prompt ATUAL já cobriu inteira — ver design.md do change para
-- o porquê de ser por versão, não um watermark único: uma versão nova nunca
-- pode "herdar" cobertura de uma versão antiga, ou uma releitura de verdade
-- (o motivo de `forcar` existir) seria pulada por engano.
--
-- PK composta em vez de SERIAL: não existe "a linha de cobertura de uma
-- conversa", só "a cobertura de uma conversa SOB uma versão" — a mesma
-- lógica de `resumos_conversa_cursor_idx` acima, aqui como chave primária
-- porque não há necessidade de mais de uma linha por par.
CREATE TABLE IF NOT EXISTS cobertura_extracao (
    conversa_id         INTEGER NOT NULL REFERENCES conversas(id) ON DELETE CASCADE,
    prompt_versao       VARCHAR(16) NOT NULL,
    -- Mesmo watermark de `conversas.ultima_mensagem_processada_id`, só que
    -- por versão — `Database.registrar_cobertura_extracao` aplica o mesmo
    -- `GREATEST` contra regressão que `atualizar_estado_conversa` já aplica
    -- lá, pela mesma razão (processamento concorrente não pode regredir).
    ultima_mensagem_id  INTEGER NOT NULL,
    atualizado_em       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    PRIMARY KEY (conversa_id, prompt_versao)
);
"""

# §12, extensão da purga (change `rascunho-registrado`): texto escrito para
# aquele cliente é conteúdo pessoal, mesmo depois de purgada a mensagem que
# o carregava. Um placeholder — não NULL — porque `rascunhos_forma` exige
# `opcao_1`/`opcao_2` preenchidos quando `encerrar = FALSE`: apagar para
# `NULL` violaria a própria constraint que garante que a geração nunca ficou
# pela metade.
TEXTO_RASCUNHO_PURGADO = "[texto apagado — retenção §12]"


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
                conninfo=self._dsn,
                min_size=1,
                max_size=self._max_size,
                open=True,
                # Migração para o pooler do Supabase (Supavisor, change de
                # 2026-08-28): a hipótese inicial aqui foi "conexão ociosa
                # derrubada em silêncio" — INVESTIGADA E DESCARTADA. Uma
                # conexão isolada nova é rápida (~1s) e uma reaproveitada do
                # pool também é, individualmente; o que trava é a SOMA de
                # muitas operações sequenciais na mesma requisição (o padrão
                # N+1 de `camucrm/painel/api.py::_carregar_candidatos`, ~20
                # idas ao banco por conversa) — cada round-trip paga a
                # latência de rede até o Supabase (~0,5-0,7s medidos, contra
                # frações de ms no Postgres local de antes da migração), e
                # 4 conversas × ~20 chamadas ficam na casa de 40-50s. Não é
                # travamento: uma espera de ~120s confirmou que a rota SEMPRE
                # termina, só muito devagar — devagar o bastante para parecer
                # "não carrega" num navegador com timeout padrão. A correção
                # de fundo é reduzir o número de idas ao banco por conversa
                # (fora do escopo desta sessão); `keepalives_*` fica como
                # prática padrão para um pool de longa duração contra um
                # banco remoto, não como a correção do problema medido.
                kwargs={
                    "connect_timeout": 10,
                    "keepalives": 1,
                    "keepalives_idle": 30,
                    "keepalives_interval": 10,
                    "keepalives_count": 3,
                },
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
                # DIVERGÊNCIA registrada (CLAUDE.md): `CREATE TABLE IF NOT
                # EXISTS` não adiciona coluna a uma tabela `contatos` já
                # existente — todo o resto deste arquivo assume banco novo
                # (nenhum `ALTER TABLE` no repo até este change). Um banco de
                # desenvolvimento já em uso (com contatos/conversas reais de
                # teste manual, como os desta sessão) perderia dado se
                # `make init` exigisse recriar o volume a cada change de
                # schema. `ADD COLUMN IF NOT EXISTS` é idempotente e não
                # apaga nada — única forma de `make init` cumprir o que a
                # instrução de execução pede ("aplica a coluna nova") sem
                # destruir uma base já populada.
                cur.execute(
                    "ALTER TABLE contatos ADD COLUMN IF NOT EXISTS "
                    "e_teste BOOLEAN NOT NULL DEFAULT FALSE"
                )
                # Mesma divergência registrada acima, agora para
                # `eventos_estagio.causada_por` (change
                # `estagio-reabertura-manual-e-relogio`): `ADD COLUMN IF NOT
                # EXISTS` idempotente, sem apagar histórico já gravado.
                cur.execute(
                    "ALTER TABLE eventos_estagio ADD COLUMN IF NOT EXISTS "
                    "causada_por VARCHAR(16) NOT NULL DEFAULT 'cliente'"
                )

    def _conn_ou(self, conn):
        """Devolve `conn` (via `nullcontext`, sem abrir/fechar nada) quando já
        veio de fora, ou uma conexão nova de `self._conn()` quando não veio.

        Suporta o parâmetro opcional `conn=` de `upsert_contato`/
        `get_or_create_conversa`/`registrar_mensagem`: chamado sem `conn`
        (todo caller pré-existente, ex. `backfill.py`), cada método continua
        abrindo e commitando sua própria transação, como sempre. Chamado com
        `conn=` (só `ingest.ingerir`, via `Database.transacao`), os três
        rodam dentro da MESMA transação Postgres — commit/rollback é de quem
        abriu `transacao()`, nunca daqui.
        """
        return nullcontext(conn) if conn is not None else self._conn()

    @contextmanager
    def transacao(self):
        """Uma única transação Postgres para operações que precisam ser
        atômicas juntas (change `ingestao-a-prova-de-falha`, spec.md
        "Cadeia de ingestão é transacional"): `ingest.ingerir` encadeia
        `upsert_contato` -> `get_or_create_conversa` -> `registrar_mensagem`
        dentro de um `with db.transacao() as conn:`, passando `conn=conn`
        para os três. Uma falha em qualquer ponto do meio propaga para fora
        deste `with`, e o `with self._conn()` abaixo reverte tudo — psycopg 3
        commita ao sair sem exceção, reverte ao sair com exceção.
        """
        with self._conn() as conn:
            yield conn

    # -- contatos ---------------------------------------------------------

    def upsert_contato(
        self,
        telefone: str,
        *,
        nome: str | None = None,
        tipo: str = B2C,
        origem: str | None = None,
        conn=None,
    ) -> Contato:
        """Cria ou atualiza um contato, identificado pelo hash do telefone.

        O nome só é sobrescrito quando vem preenchido: um push_name ausente
        num evento não deve apagar o nome que alguém digitou à mão.

        `conn=` opcional (change `ingestao-a-prova-de-falha`): ver
        `Database._conn_ou`.
        """
        if tipo not in (B2B, B2C):
            raise ValueError(f"tipo inválido: {tipo!r}")
        telefone_hash = hash_telefone(telefone)
        with self._conn_ou(conn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO contatos (nome, telefone_hash, telefone, tipo, origem)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (telefone_hash) DO UPDATE SET
                        nome = COALESCE(EXCLUDED.nome, contatos.nome),
                        telefone = COALESCE(EXCLUDED.telefone, contatos.telefone),
                        origem = COALESCE(contatos.origem, EXCLUDED.origem)
                    RETURNING id, nome, telefone_hash, telefone, tipo, origem, criado_em,
                              e_teste
                    """,
                    (nome, telefone_hash, telefone, tipo, origem),
                )
                return Contato(*cur.fetchone())

    def contato_por_telefone_hash(self, telefone_hash: str) -> Contato | None:
        """Leitura pura, sem upsert — mesmo padrão de
        `prospeccao_por_telefone_hash`. Usado por `ingest.ingerir` (change
        `ingestao-restrita-por-instancia`) para decidir se um telefone JÁ é
        contato conhecido ANTES de decidir se cria um novo — uma instância
        restrita não pode upsertar (que sempre cria se não existir) para
        descobrir se já existia.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, nome, telefone_hash, telefone, tipo, origem, "
                    "criado_em, e_teste FROM contatos WHERE telefone_hash = %s",
                    (telefone_hash,),
                )
                row = cur.fetchone()
                return Contato(*row) if row else None

    def set_tipo_contato(self, contato_id: int, tipo: str, *, conn=None) -> None:
        """`conn=` opcional (change `painel-mensagens-recentes-e-acoes-
        seguras`): ver `Database._conn_ou` — usado por
        `acoes.mudar_funil_conversa` para rodar dentro da mesma transação
        travada por `get_conversa_for_update`.
        """
        if tipo not in (B2B, B2C):
            raise ValueError(f"tipo inválido: {tipo!r}")
        with self._conn_ou(conn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE contatos SET tipo = %s WHERE id = %s", (tipo, contato_id)
                )

    def marcar_contato_teste(
        self, contato_id: int, e_teste: bool, *, por: str | None = None
    ) -> None:
        """Marca/desmarca um contato como teste (change
        `contatos-de-teste-isolados`).

        A marca é por CONTATO — toda conversa passada e futura do contato
        fica de teste junto, sem precisar marcar conversa por conversa
        (requirement "Marca de teste é por contato"). Só ação manual chama
        isto: nenhuma heurística de `camucrm/` decide sozinha que um contato
        é de teste (§1, mesmo princípio estendido).

        Grava em `correcoes` (§7), nunca em `marcos_manuais` — "teste" não é
        o conceito de S6/P5/P6/perdido que `marcos_manuais` existe para
        guardar (requirement "Marcação de teste é sempre manual e
        registrada"). `correcoes.conversa_id` é NOT NULL (a tabela é sobre
        correção de UMA conversa); como a marca aqui é por contato, usamos a
        conversa mais recente do contato — a aberta, se houver, senão a mais
        recente encerrada — só para satisfazer essa FK. Um contato sem
        nenhuma conversa ainda (nunca trocou mensagem) não tem onde gravar a
        correção; a marca em `contatos.e_teste` acontece de qualquer forma
        (não há nada para filtrar ainda), só a correção fica sem registro
        nesse caso raro — divergência aceita, não silenciosa.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT e_teste FROM contatos WHERE id = %s FOR UPDATE",
                    (contato_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"contato {contato_id} não existe")
                antes = row[0]
                cur.execute(
                    "UPDATE contatos SET e_teste = %s WHERE id = %s",
                    (e_teste, contato_id),
                )
                cur.execute(
                    "SELECT id FROM conversas WHERE contato_id = %s "
                    "ORDER BY (resultado IS NULL) DESC, id DESC LIMIT 1",
                    (contato_id,),
                )
                conversa_row = cur.fetchone()
                if conversa_row is not None:
                    cur.execute(
                        "INSERT INTO correcoes (conversa_id, campo, antes, depois, por) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (conversa_row[0], "e_teste", _texto(antes), _texto(e_teste), por),
                    )

    def set_funil_conversa(self, conversa_id: int, funil: str, *, conn=None) -> None:
        """Move a conversa aberta para o outro funil.

        `conversas.funil` é copiado de `contatos.tipo` na criação e não
        acompanha mudanças depois — a conversa é a unidade de análise, e
        reescrever o funil de conversas encerradas mudaria retroativamente as
        métricas de conversão já apuradas.

        `conn=` opcional (change `painel-mensagens-recentes-e-acoes-
        seguras`): ver `Database._conn_ou` — usado por
        `acoes.mudar_funil_conversa` para rodar dentro da mesma transação
        travada por `get_conversa_for_update`.
        """
        if funil not in (B2B, B2C):
            raise ValueError(f"funil inválido: {funil!r}")
        with self._conn_ou(conn) as conn:
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

    def get_or_create_conversa(
        self, contato_id: int, funil: str | None = None, *, conn=None
    ) -> Conversa:
        """Conversa aberta do contato, criando uma se não houver.

        "Aberta" é a mais recente sem `resultado`. Conversa com resultado é
        histórico fechado e não recebe mensagem nova — um cliente que volta
        depois de fechado abre outra, o que mantém as métricas de conversão
        por conversa e não por pessoa.

        `conn=` opcional (change `ingestao-a-prova-de-falha`): ver
        `Database._conn_ou`.
        """
        with self._conn_ou(conn) as conn:
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

    def get_conversa(self, conversa_id: int, *, conn=None) -> Conversa | None:
        """`conn=` opcional (change `painel-mensagens-recentes-e-acoes-
        seguras`): ver `Database._conn_ou` — precisa ler a mesma transação
        quando chamado depois de uma escrita ainda não commitada (ex.
        `acoes.mudar_funil_conversa` relendo a conversa logo após
        `set_funil_conversa(conn=conn)`, dentro da mesma transação travada).
        """
        with self._conn_ou(conn) as conn:
            with conn.cursor() as cur:
                cur.execute(f"{self._CONVERSA_SELECT} WHERE c.id = %s", (conversa_id,))
                row = cur.fetchone()
                return Conversa(*row) if row else None

    def get_conversa_for_update(self, conversa_id: int, conn) -> Conversa | None:
        """`SELECT ... FOR UPDATE OF c` na linha de `conversas` (change
        `painel-mensagens-recentes-e-acoes-seguras`, requirement "Ações
        concorrentes no mesmo card não corrompem marcos_manuais").

        `conn` é OBRIGATÓRIO (vem de `with db.transacao() as conn:`) — a
        trava só vale enquanto a transação que a tomou está aberta; chamar
        isto fora de uma transação explícita não trava nada de útil, e por
        isso não há um default `None` com `_conn_ou` aqui, diferente do
        resto do arquivo.

        `FOR UPDATE OF c` (não `FOR UPDATE` puro): `_CONVERSA_SELECT` faz
        `JOIN` com `contatos`, e `FOR UPDATE` sem `OF` tentaria travar a
        linha de `contatos` também — travar só `conversas` é o que o
        requirement pede, e é a única tabela que
        `acoes.marcar_marco`/`acoes.mudar_funil_conversa` escrevem sob esta
        trava.

        `acoes.marcar_marco`/`acoes.mudar_funil_conversa` chamam isto
        primeiro, dentro de `db.transacao()`: uma segunda chamada quase
        simultânea para a MESMA conversa bloqueia neste `SELECT ... FOR
        UPDATE` até a primeira transação commitar (ou reverter), serializando
        as duas em vez de intercalar leitura e escrita — é isso que impede
        `marcos_manuais` contraditório (duas gravações "ganho"/"perdido" na
        mesma conversa) sem recusar a segunda ação de cara.
        """
        with conn.cursor() as cur:
            cur.execute(
                f"{self._CONVERSA_SELECT} WHERE c.id = %s FOR UPDATE OF c", (conversa_id,)
            )
            row = cur.fetchone()
            return Conversa(*row) if row else None

    def listar_conversas_abertas(
        self,
        limite: int = 500,
        *,
        incluir_teste: bool = False,
        apenas_teste: bool = False,
    ) -> list[Conversa]:
        """Conversas sem resultado — as candidatas à fila do dia.

        Change `contatos-de-teste-isolados`: exclui contato de teste por
        padrão (kanban/fila reais). Ver `_condicao_teste` para os três modos.

        Change `painel-mensagens-recentes-e-acoes-seguras`: `ORDER BY
        atualizado_em ASC` (não mais `DESC`) — quando há mais conversas
        abertas que `limite`, o corte precisa cair nas conversas TOCADAS
        recentemente (pelo painel ou por mensagem nova), não nas mais
        NEGLIGENCIADAS. `atualizado_em DESC` fazia o oposto: cortava
        primeiro exatamente as conversas que nunca foram tocadas, as que
        mais precisam de atenção. A ordem de exibição final (kanban/fila) é
        recalculada por `views.ordenar_conversas`/`rules.fila.montar_fila` —
        esta ordem aqui só decide o que sobrevive ao corte, não o que a
        tela mostra primeiro. Ver `contar_conversas_abertas` para o `total`
        real, que a rota expõe mesmo quando o corte acontece.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._CONVERSA_SELECT} WHERE c.resultado IS NULL {condicao} "
                    "ORDER BY c.atualizado_em ASC LIMIT %s",
                    (limite,),
                )
                return [Conversa(*row) for row in cur.fetchall()]

    def listar_conversas_fechadas(
        self,
        limite: int = 500,
        *,
        incluir_teste: bool = False,
        apenas_teste: bool = False,
    ) -> list[Conversa]:
        """Conversas COM resultado — fechadas por marco manual (ganho/perdido).

        Change `marco-manual-visivel-na-aba-conversas`: existe só para a aba
        Conversas do painel (`GET /api/conversas`), nunca para kanban nem
        fila — as duas continuam batendo só em `listar_conversas_abertas`
        (requirement "Kanban e fila continuam mostrando só conversas
        abertas"). `ORDER BY atualizado_em DESC` aqui é o oposto de
        `listar_conversas_abertas` de propósito: não existe "conversa fechada
        negligenciada" a proteger do corte — a mais recente é a mais
        relevante pra quem está conferindo o que fechou.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._CONVERSA_SELECT} WHERE c.resultado IS NOT NULL {condicao} "
                    "ORDER BY c.atualizado_em DESC LIMIT %s",
                    (limite,),
                )
                return [Conversa(*row) for row in cur.fetchall()]

    def contar_conversas_abertas(
        self, *, incluir_teste: bool = False, apenas_teste: bool = False
    ) -> int:
        """Total real de conversas abertas, sem o corte de `limite` — o que
        `listar_conversas_abertas` sozinha não expõe (change
        `painel-mensagens-recentes-e-acoes-seguras`, requirement "Kanban e
        fila expõem contagem real"). Mesmo filtro de teste que
        `listar_conversas_abertas` usa, para os dois números (total exibido
        vs. total real) falarem da mesma população.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM conversas c "
                    "JOIN contatos ct ON ct.id = c.contato_id "
                    f"WHERE c.resultado IS NULL {condicao}"
                )
                return cur.fetchone()[0]

    def token_de_mudanca(self) -> str:
        """Cursor barato de "algo mudou" para o SSE do painel (change
        `painel-tempo-real`, design.md).

        Três subselects escalares — `MAX(mensagens.id)`,
        `MAX(eventos_estagio.id)`, `epoch(MAX(conversas.atualizado_em))` —
        concatenados em `"m:e:c"`. Três partes porque são três motivos
        distintos de a tela estar desatualizada, e só o primeiro move
        `mensagens.id`: uma correção manual ou uma mudança de resultado pode
        tocar `conversas.atualizado_em` sem gerar mensagem nem evento de
        estágio novo.

        O mesmo valor serve de cursor de reconexão (`?desde_id=N` no stream):
        comparar strings anteriores/posteriores é suficiente para o poller de
        `painel/stream.py` decidir se dispara o evento — nenhuma parte deste
        método interpreta o conteúdo do token além de igualdade.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT "
                    "(SELECT MAX(id) FROM mensagens), "
                    "(SELECT MAX(id) FROM eventos_estagio), "
                    "(SELECT extract(epoch FROM MAX(atualizado_em)) FROM conversas)"
                )
                max_mensagem, max_evento, max_atualizado = cur.fetchone()
        return f"{max_mensagem or 0}:{max_evento or 0}:{max_atualizado or 0}"

    def atualizar_estado_conversa(
        self,
        conversa_id: int,
        *,
        estagio: str | None = None,
        temperatura: str | None = None,
        bola_com: str | None = None,
        resultado: str | None = None,
        ultima_mensagem_processada_id: int | None = None,
        conn=None,
    ) -> None:
        """Atualiza o estado derivado da conversa (cache do que as regras dizem).

        Nada aqui é fonte de verdade: `estagio` e `temperatura` são derivados
        e podem ser recalculados de `fatos` + `mensagens` a qualquer momento.
        A coluna existe para a fila não precisar reprocessar tudo a cada
        consulta — se divergir, o replay ganha.

        `ultima_mensagem_processada_id` (§2, watermark de idempotência) usa
        `GREATEST` contra o valor já gravado — mesmo padrão de
        `ultimo_inbound`/`ultimo_outbound` em `registrar_mensagem`. Sem isso,
        dois processamentos quase simultâneos da mesma conversa (webhook e
        `camucrm extrair` juntos, ou dois webhooks) podem gravar o menor dos
        dois valores por último, regredindo o watermark e reapresentando ao
        LLM um bloco de mensagens já processado.

        `conn=` opcional (change `painel-mensagens-recentes-e-acoes-
        seguras`): ver `Database._conn_ou` — usado por
        `acoes.marcar_marco`/`acoes.mudar_funil_conversa` para escrever
        dentro da mesma transação travada por `get_conversa_for_update`.
        """
        campos, valores = [], []
        for nome, valor in (
            ("estagio", estagio),
            ("temperatura", temperatura),
            ("bola_com", bola_com),
            ("resultado", resultado),
        ):
            if valor is not None:
                campos.append(f"{nome} = %s")
                valores.append(valor)
        if ultima_mensagem_processada_id is not None:
            campos.append(
                "ultima_mensagem_processada_id = "
                "GREATEST(COALESCE(ultima_mensagem_processada_id, %s), %s)"
            )
            valores.append(ultima_mensagem_processada_id)
            valores.append(ultima_mensagem_processada_id)
        if not campos:
            return
        campos.append("atualizado_em = now()")
        valores.append(conversa_id)
        with self._conn_ou(conn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE conversas SET {', '.join(campos)} WHERE id = %s",
                    tuple(valores),
                )

    # -- cobertura de extração por versão de prompt (change
    # `backfill-cobertura-por-prompt`) -------------------------------------

    def cobertura_extracao(self, conversa_id: int, prompt_versao: str) -> int | None:
        """Até qual mensagem a versão de prompt `prompt_versao` já leu nesta
        conversa, ou `None` se essa versão nunca a tocou.

        `None` é o sinal para `Extrator.processar_conversa` (com
        `somente_desatualizados=True`) reler a conversa inteira desde o
        início — não existe cobertura parcial "confiável o bastante": ou a
        versão já viu até um ponto, ou não viu nada.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ultima_mensagem_id FROM cobertura_extracao "
                    "WHERE conversa_id = %s AND prompt_versao = %s",
                    (conversa_id, prompt_versao),
                )
                linha = cur.fetchone()
        return linha[0] if linha else None

    def registrar_cobertura_extracao(
        self,
        conversa_id: int,
        prompt_versao: str,
        ultima_mensagem_id: int,
        *,
        conn=None,
    ) -> None:
        """Marca que `prompt_versao` já leu esta conversa até
        `ultima_mensagem_id`.

        `GREATEST` contra o valor já gravado — mesmo padrão e mesmo motivo de
        `atualizar_estado_conversa` para `ultima_mensagem_processada_id`:
        dois processamentos concorrentes da mesma conversa e versão (webhook
        e `camucrm extrair` juntos, backfill retomado duas vezes) não podem
        regredir a cobertura e fazer uma extração real de mensagem nova ser
        pulada por engano na próxima consulta.
        """
        with self._conn_ou(conn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO cobertura_extracao "
                    "(conversa_id, prompt_versao, ultima_mensagem_id) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (conversa_id, prompt_versao) DO UPDATE SET "
                    "ultima_mensagem_id = GREATEST("
                    "cobertura_extracao.ultima_mensagem_id, excluded.ultima_mensagem_id"
                    "), atualizado_em = now()",
                    (conversa_id, prompt_versao, ultima_mensagem_id),
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
        conn=None,
    ) -> int | None:
        """Grava uma mensagem e atualiza `ultimo_inbound`/`ultimo_outbound`.

        Devolve `None` quando a mensagem já existia (mesmo `externa_id`) — um
        webhook reentregue não vira mensagem nova nem move o relógio da
        conversa, que é o que faria a temperatura oscilar sem ninguém ter
        falado nada.

        `conn=` opcional (change `ingestao-a-prova-de-falha`): ver
        `Database._conn_ou`.
        """
        if direcao not in (ENTRADA, SAIDA):
            raise ValueError(f"direção inválida: {direcao!r}")
        enviada_em = enviada_em or datetime.now(timezone.utc)
        coluna = "ultimo_inbound" if direcao == ENTRADA else "ultimo_outbound"
        bola = "camu" if direcao == ENTRADA else BOLA_CLIENTE
        with self._conn_ou(conn) as conn:
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

        Change `backfill-seguro-para-reexecucao`, §8: ordenado por
        `enviada_em` (com `id` como desempate), não só por `id` de inserção.
        Em dumps não estritamente ordenados/multi-fonte, `id` (ordem de
        inserção) e `enviada_em` (ordem real) podem divergir — `id` continua
        decidindo o que é "novo" (o corte do delta), mas a ORDEM em que o
        bloco chega ao LLM precisa bater com a cronologia real, mesma
        convenção que `Database.listar_mensagens`/`construir_sinais` já
        usam. Ordenar só por `id` faria o LLM ler a conversa fora de ordem
        sempre que o dump de origem não for estritamente cronológico.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, direcao, texto, enviada_em FROM mensagens "
                    "WHERE conversa_id = %s AND id > %s ORDER BY enviada_em, id",
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
        causada_por: str = "cliente",
        conn=None,
    ) -> None:
        """`conn=` opcional (change `painel-mensagens-recentes-e-acoes-
        seguras`): ver `Database._conn_ou` — usado por
        `acoes.mudar_funil_conversa` para gravar dentro da mesma transação
        travada por `get_conversa_for_update`.
        """
        with self._conn_ou(conn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO eventos_estagio
                        (conversa_id, de, para, em, origem, motivo, causada_por)
                    VALUES (%s, %s, %s, COALESCE(%s, now()), %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (conversa_id, de, para, em, origem, motivo, causada_por),
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

    def estagios_de_conversas_encerradas(
        self, *, incluir_teste: bool = False, apenas_teste: bool = False
    ) -> dict[int, list[str]]:
        """`conversa_id -> estágios (`para`) alcançados`, só para conversas
        com `resultado IS NOT NULL` (change `analise-desempenho`).

        Insumo de "onde as conversas morrem" (o plano: "o número mais
        acionável no dia um"). Devolve os eventos crus; `metrics.py` decide o
        estágio máximo com `taxonomia.rank_estagio`, mesma regra de
        `estagio_maximo_alcancado` — a ordenação de estágio não é duplicada
        aqui em SQL.

        Change `contatos-de-teste-isolados`: exclui contato de teste por
        padrão — ver `_condicao_teste`.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT ee.conversa_id, ee.para
                      FROM eventos_estagio ee
                      JOIN conversas c ON c.id = ee.conversa_id
                      JOIN contatos ct ON ct.id = c.contato_id
                     WHERE c.resultado IS NOT NULL {condicao}
                    """
                )
                resultado: dict[int, list[str]] = {}
                for conversa_id, para in cur.fetchall():
                    resultado.setdefault(conversa_id, []).append(para)
                return resultado

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

    def trilhas_registradas(self, conversa_id: int) -> set[tuple[str | None, str]]:
        """Pares `(de, para)` já gravados (change `backfill-seguro-para-reexecucao`).

        `estagios_registrados` (só `para`) é o que `_trilha_de_backfill`
        usava antes: pular uma transição por já existir QUALQUER evento com
        aquele destino, não necessariamente com a mesma origem. Canto raro
        (funil trocado + backfill reexecutado, ou um dump reprocessado com
        fatos diferentes) pode produzir uma transição legítima com o mesmo
        `para` mas `de` diferente da já registrada — pular por `para` sozinho
        descartaria essa trilha distinta como se já estivesse contabilizada.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT de, para FROM eventos_estagio WHERE conversa_id = %s",
                    (conversa_id,),
                )
                return {(d, p) for (d, p) in cur.fetchall()}

    def ultimo_avanco_em(self, conversa_id: int) -> datetime | None:
        """Timestamp do último avanço ao vivo (§5, sinal "avançou hoje").

        Só `origem = 'live'`: um evento de backfill carrega o momento do
        backfill, não o do avanço, e o trataria como se tivesse acabado de
        acontecer — deixando quente uma conversa parada há meses.
        """
        row = self._ultimo_evento_estagio_live(conversa_id)
        return row[0] if row else None

    def ultimo_avanco_causada_por(self, conversa_id: int) -> str | None:
        """Quem causou o último avanço ao vivo — "cliente" ou "camu".

        Change `estagio-reabertura-manual-e-relogio`: mesma linha que
        `ultimo_avanco_em` (por isso a query compartilhada em
        `_ultimo_evento_estagio_live`) — sem isto, `rules.temperatura.
        classificar` não teria como saber, numa passada em que nada avançou
        AGORA, se o avanço de <24h que ainda conta veio do cliente ou da
        própria Camu.
        """
        row = self._ultimo_evento_estagio_live(conversa_id)
        return row[1] if row else None

    def _ultimo_evento_estagio_live(
        self, conversa_id: int
    ) -> tuple[datetime, str] | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT em, causada_por FROM eventos_estagio "
                    "WHERE conversa_id = %s AND origem = 'live' "
                    "ORDER BY em DESC LIMIT 1",
                    (conversa_id,),
                )
                return cur.fetchone()

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
        """Grava uma ocorrência de objeção (§2, §4).

        Idempotente via `objecoes_dedupe_idx`: a mesma (conversa, categoria,
        estágio, trecho) gravada de novo — por reprocessamento concorrente ou
        por `forcar=True` — não duplica linha. `ON CONFLICT DO NOTHING` é a
        mesma família de solução já usada em `fatos`.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO objecoes (conversa_id, categoria, estagio, trecho, em)
                    VALUES (%s, %s, %s, %s, COALESCE(%s, now()))
                    ON CONFLICT DO NOTHING
                    """,
                    (conversa_id, categoria, estagio, trecho, em),
                )

    def distribuicao_objecoes(
        self,
        desde: datetime | None = None,
        *,
        incluir_teste: bool = False,
        apenas_teste: bool = False,
    ) -> dict[str, int]:
        """Contagem por categoria — insumo da revisão mensal da §4.

        Change `contatos-de-teste-isolados`: exclui contato de teste por
        padrão — junta até `contatos` para aplicar `_condicao_teste`, já que
        `objecoes` só carrega `conversa_id`.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        base = (
            "SELECT o.categoria, COUNT(*) FROM objecoes o "
            "JOIN conversas c ON c.id = o.conversa_id "
            "JOIN contatos ct ON ct.id = c.contato_id "
            f"WHERE 1=1 {condicao} "
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                if desde:
                    cur.execute(base + "AND o.em >= %s GROUP BY o.categoria", (desde,))
                else:
                    cur.execute(base + "GROUP BY o.categoria")
                return dict(cur.fetchall())

    def distribuicao_objecoes_por_estagio(
        self,
        desde: datetime | None = None,
        *,
        incluir_teste: bool = False,
        apenas_teste: bool = False,
    ) -> dict[tuple[str | None, str], int]:
        """Contagem por (estagio, categoria) — `objecoes.estagio` já é
        coletado desde o início mas `distribuicao_objecoes` o descarta
        (change `analise-desempenho`: "frete concentrado em S4" é mudança de
        playbook, não de código).

        Change `contatos-de-teste-isolados`: exclui contato de teste por
        padrão — mesmo join de `distribuicao_objecoes`.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        base = (
            "SELECT o.estagio, o.categoria, COUNT(*) FROM objecoes o "
            "JOIN conversas c ON c.id = o.conversa_id "
            "JOIN contatos ct ON ct.id = c.contato_id "
            f"WHERE 1=1 {condicao} "
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                if desde:
                    cur.execute(
                        base + "AND o.em >= %s GROUP BY o.estagio, o.categoria", (desde,)
                    )
                else:
                    cur.execute(base + "GROUP BY o.estagio, o.categoria")
                return {(estagio, categoria): n for estagio, categoria, n in cur.fetchall()}

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

    def retorno_por_numero_followup(
        self, *, incluir_teste: bool = False, apenas_teste: bool = False
    ) -> dict[int, tuple[int, int]]:
        """`numero (1 ou 2) -> (total_enviados, com_resposta_depois)`.

        "Com resposta" é qualquer mensagem `in` na mesma conversa depois do
        `enviado_em` do follow-up — responde "o 2º toque funciona alguma
        vez" (plano: "decide se o teto devia ser 1"), change
        `analise-desempenho`.

        Change `contatos-de-teste-isolados`: exclui contato de teste por
        padrão — ver `_condicao_teste`.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT f.numero,
                           COUNT(*),
                           COUNT(*) FILTER (
                               WHERE EXISTS (
                                   SELECT 1 FROM mensagens m
                                    WHERE m.conversa_id = f.conversa_id
                                      AND m.direcao = 'in'
                                      AND m.enviada_em > f.enviado_em
                               )
                           )
                      FROM followups f
                      JOIN conversas c ON c.id = f.conversa_id
                      JOIN contatos ct ON ct.id = c.contato_id
                     WHERE 1=1 {condicao}
                     GROUP BY f.numero
                     ORDER BY f.numero
                    """
                )
                return {numero: (total, com_retorno) for numero, total, com_retorno in cur.fetchall()}

    # -- marcos manuais (§3) ----------------------------------------------

    def registrar_marco(
        self, conversa_id: int, marco: str, *, por: str | None = None, conn=None
    ) -> None:
        """`conn=` opcional (change `painel-mensagens-recentes-e-acoes-
        seguras`): ver `Database._conn_ou` — usado por `acoes.marcar_marco`
        para gravar dentro da mesma transação travada por
        `get_conversa_for_update`.
        """
        if marco not in MARCOS_MANUAIS:
            raise ValueError(f"marco inválido: {marco!r} (use {MARCOS_MANUAIS})")
        with self._conn_ou(conn) as conn:
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

    def marcos_da_conversa(self, conversa_id: int, *, conn=None) -> set[str]:
        """`conn=` opcional (change `painel-mensagens-recentes-e-acoes-
        seguras`): ver `Database._conn_ou`. Crítico para `acoes.marcar_marco`
        chamar isto com `conn=conn` — sem isso, a checagem de conflito
        pedia uma SEGUNDA conexão do pool enquanto a primeira (a da
        transação, travada por `get_conversa_for_update`) continuava
        aberta; sob concorrência real (N chamadas simultâneas na mesma
        conversa >= `max_size` do pool), isso esgotava o pool e travava
        (`PoolTimeout`) em vez de simplesmente serializar as chamadas.
        """
        with self._conn_ou(conn) as conn:
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
        conn=None,
    ) -> None:
        """Grava uma correção humana. Chamada por *toda* correção, sem exceção.

        §7: correção que só ajusta a tela e não é gravada é informação jogada
        fora. As duas funções são alimentar o eval e revelar, pelo padrão, o
        que o prompt não está vendo.

        `conn=` opcional (change `painel-mensagens-recentes-e-acoes-
        seguras`): ver `Database._conn_ou` — usado por
        `acoes.mudar_funil_conversa` para gravar dentro da mesma transação
        travada por `get_conversa_for_update`.
        """
        with self._conn_ou(conn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO correcoes (conversa_id, campo, antes, depois, por) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (conversa_id, campo, _texto(antes), _texto(depois), por),
                )

    # -- reabertura manual de recusa (change
    #    `estagio-reabertura-manual-e-relogio`) -----------------------------

    def registrar_desconsideracao_recusa(self, conversa_id: int, *, por: str) -> None:
        """Registra que um `recusa_explicita=true` está sendo desconsiderado
        para fins de decisão de estágio (design.md, change
        `estagio-reabertura-manual-e-relogio`).

        NÃO apaga nem reescreve `fatos.recusa_explicita` — grava em
        `correcoes` (campo `"recusa_explicita"`, `antes="true"`,
        `depois="desconsiderado"`), a mesma tabela e o mesmo padrão que
        qualquer outra correção humana (§7). `rules.estagio._derive_b2c`/
        `_derive_b2b` (via `SinaisConversa.recusa_desconsiderada`) passam a
        ignorar o fato como terminal para esta conversa, sem que a linha
        original em `fatos` mude uma vírgula — preserva a evidência de que a
        extração errou ali, sinal que `make eval` precisa.
        """
        if not por or not por.strip():
            raise ValueError(
                "desconsiderar recusa exige identificação de quem decidiu (por)"
            )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO correcoes (conversa_id, campo, antes, depois, por) "
                    "VALUES (%s, 'recusa_explicita', 'true', 'desconsiderado', %s)",
                    (conversa_id, por),
                )

    def recusa_desconsiderada(self, conversa_id: int) -> bool:
        """Se existe desconsideração ativa de `recusa_explicita` para a
        conversa — consultado por `rules.estagio` a cada recálculo."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM correcoes WHERE conversa_id = %s "
                    "AND campo = 'recusa_explicita' AND depois = 'desconsiderado' "
                    "LIMIT 1",
                    (conversa_id,),
                )
                return cur.fetchone() is not None

    def listar_correcoes(self, limite: int = 200) -> list[tuple[int, str, str, str, datetime]]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT conversa_id, campo, antes, depois, em FROM correcoes "
                    "ORDER BY em DESC LIMIT %s",
                    (limite,),
                )
                return list(cur.fetchall())

    def padrao_correcoes(
        self,
        desde: datetime | None = None,
        *,
        incluir_teste: bool = False,
        apenas_teste: bool = False,
    ) -> list[tuple[str, str | None, str | None, int]]:
        """Contagem por campo + par (antes, depois) (change
        `analise-desempenho`). Plano: "funil corrigido 9× de b2c para b2b"
        diz que a classificação B2B falha na ingestão — o padrão, não a
        correção isolada, é o que aponta o defeito.

        Change `contatos-de-teste-isolados`: exclui contato de teste por
        padrão. Efeito colateral aceito: a própria correção que
        `marcar_contato_teste` grava (`campo='e_teste'`) some do padrão
        assim que o contato é marcado, porque a conversa que carrega essa
        correção já pertence a um contato agora `e_teste=TRUE` — o que é
        correto, não um bug: a marcação de teste não devia poluir o padrão
        de correções de negócio.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        base = (
            "SELECT co.campo, co.antes, co.depois, COUNT(*) FROM correcoes co "
            "JOIN conversas c ON c.id = co.conversa_id "
            "JOIN contatos ct ON ct.id = c.contato_id "
            f"WHERE 1=1 {condicao} "
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                if desde:
                    cur.execute(
                        base + "AND co.em >= %s GROUP BY co.campo, co.antes, co.depois "
                        "ORDER BY COUNT(*) DESC",
                        (desde,),
                    )
                else:
                    cur.execute(
                        base + "GROUP BY co.campo, co.antes, co.depois "
                        "ORDER BY COUNT(*) DESC"
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
                    "SELECT de, para, em, origem, motivo, causada_por "
                    "FROM eventos_estagio WHERE conversa_id = %s ORDER BY id",
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
        antes_de: int | None = None,
        limite: int = 200,
        mais_recentes: bool = True,
    ) -> list[MensagemRegistro]:
        """Mensagens gravadas, opcionalmente restritas a uma conversa.

        Três modos (change `painel-mensagens-recentes-e-acoes-seguras` —
        antes deste change havia só um: `ORDER BY id ASC` sempre, o que fazia
        `GET /api/conversas/{id}/mensagens` mostrar as MAIS ANTIGAS de uma
        conversa longa, não o estado atual):

        - **`desde_id` informado**: catch-up incremental — `id > desde_id`,
          `ORDER BY id ASC`. Nunca muda: é o cursor de reconexão que
          `painel/stream.py::gerador_sse` usa (change `painel-tempo-real`)
          para não perder mensagem entre reconexões, sempre em ORDEM
          CRESCENTE a partir de um id conhecido. `mais_recentes`/`antes_de`
          são ignorados neste modo.
        - **sem `desde_id`, `mais_recentes=True` (novo padrão)**: as
          `limite` mensagens MAIS RECENTES — opcionalmente antes de
          `antes_de` (cursor "antes de X" para paginar para trás no
          histórico). A consulta busca por `id DESC` e o resultado é
          revertido antes de devolver, para sair sempre em ordem
          cronológica ascendente (a mais antiga da janela primeiro) — quem
          consome (tela, `serializar_mensagens`) não precisa reordenar.
        - **`mais_recentes=False`**: comportamento antigo, `ORDER BY id ASC
          LIMIT limite` desde o início. Preservado para quem precisa da
          conversa inteira desde o começo, não só a cauda recente — ver
          `api._mensagens_de_conversa_para_bruto` (ground truth/eval:
          "a conversa inteira, não as últimas 200", `limite` ali é só rede
          de segurança contra uma conversa anormalmente longa, não um corte
          intencional).
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                if desde_id is not None:
                    if conversa_id is not None:
                        cur.execute(
                            "SELECT id, conversa_id, direcao, texto, enviada_em "
                            "FROM mensagens WHERE conversa_id = %s AND id > %s "
                            "ORDER BY id LIMIT %s",
                            (conversa_id, desde_id, limite),
                        )
                    else:
                        cur.execute(
                            "SELECT id, conversa_id, direcao, texto, enviada_em "
                            "FROM mensagens WHERE id > %s ORDER BY id LIMIT %s",
                            (desde_id, limite),
                        )
                    return [MensagemRegistro(*row) for row in cur.fetchall()]

                if not mais_recentes:
                    if conversa_id is not None:
                        cur.execute(
                            "SELECT id, conversa_id, direcao, texto, enviada_em "
                            "FROM mensagens WHERE conversa_id = %s "
                            "ORDER BY id LIMIT %s",
                            (conversa_id, limite),
                        )
                    else:
                        cur.execute(
                            "SELECT id, conversa_id, direcao, texto, enviada_em "
                            "FROM mensagens ORDER BY id LIMIT %s",
                            (limite,),
                        )
                    return [MensagemRegistro(*row) for row in cur.fetchall()]

                condicoes = []
                params: list[Any] = []
                if conversa_id is not None:
                    condicoes.append("conversa_id = %s")
                    params.append(conversa_id)
                if antes_de is not None:
                    condicoes.append("id < %s")
                    params.append(antes_de)
                where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""
                params.append(limite)
                cur.execute(
                    f"SELECT id, conversa_id, direcao, texto, enviada_em "
                    f"FROM mensagens {where} ORDER BY id DESC LIMIT %s",
                    tuple(params),
                )
                linhas = list(reversed(cur.fetchall()))
                return [MensagemRegistro(*row) for row in linhas]

    def contar_mensagens(self, conversa_id: int) -> int:
        """Total real de mensagens de uma conversa — o que
        `listar_mensagens_registradas` sozinha não expõe quando corta pelo
        `limite` (change `painel-mensagens-recentes-e-acoes-seguras`,
        requirement "Mensagens recentes aparecem por padrão").
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM mensagens WHERE conversa_id = %s",
                    (conversa_id,),
                )
                return cur.fetchone()[0]

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
                           (ct.telefone IS NOT NULL) AS tem_telefone, ct.criado_em,
                           ct.e_teste
                      FROM conversas c
                      JOIN contatos ct ON ct.id = c.contato_id
                     WHERE c.id = %s
                    """,
                    (conversa_id,),
                )
                row = cur.fetchone()
                return ContatoResumido(*row) if row else None

    # -- rascunhos (§10, change `rascunho-registrado`) ---------------------

    _RASCUNHO_SELECT = """
        SELECT id, conversa_id, estagio, temperatura, funil, objecao,
               followups_enviados, opcao_1, opcao_2, avisos, encerrar, motivo,
               modelo, prompt_versao, gerado_em, gerado_por, escolhida,
               texto_final, escolhido_em, escolhido_por, mensagem_id,
               estagio_no_envio
          FROM rascunhos
    """

    def gravar_rascunho(
        self,
        conversa_id: int,
        *,
        estagio: str,
        temperatura: str,
        funil: str,
        objecao: str | None = None,
        followups_enviados: int = 0,
        opcoes: tuple[str, str] | Sequence[str] | None = None,
        avisos: Sequence[str] = (),
        encerrar: bool = False,
        motivo: str | None = None,
        modelo: str | None = None,
        prompt_versao: str | None = None,
        gerado_por: str | None = None,
    ) -> int:
        """Grava o que `drafts.gerar` produziu — geração OU recusa, nunca as
        duas (constraint `rascunhos_forma`). Devolve o id da linha.

        Todo campo de contexto é copiado no momento da chamada — o chamador
        (hoje só `camucrm.painel.api`) já resolveu `estado.estagio` etc. via
        `pipeline.recalcular`; este método não recalcula nada.
        """
        if bool(encerrar) == bool(opcoes):
            raise ValueError(
                "rascunho é geração (opcoes) OU recusa (encerrar=True), nunca os dois"
            )
        opcao_1 = opcoes[0] if opcoes else None
        opcao_2 = opcoes[1] if opcoes else None
        avisos_texto = "; ".join(avisos) if avisos else None
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rascunhos (
                        conversa_id, estagio, temperatura, funil, objecao,
                        followups_enviados, opcao_1, opcao_2, avisos,
                        encerrar, motivo, modelo, prompt_versao, gerado_por
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        conversa_id, estagio, temperatura, funil, objecao,
                        followups_enviados, opcao_1, opcao_2, avisos_texto,
                        encerrar, motivo, modelo, prompt_versao, gerado_por,
                    ),
                )
                return cur.fetchone()[0]

    def registrar_escolha_rascunho(
        self,
        rascunho_id: int,
        *,
        escolhida: int | None = None,
        texto_final: str | None = None,
        por: str | None = None,
    ) -> None:
        """Registra a escolha humana. Nunca apaga a opção não escolhida — as
        duas continuam na linha (requirement "Opção não escolhida não é
        descartada"); só `escolhida`/`texto_final`/`escolhido_em/por` mudam.

        Três formas válidas, todas aceitas aqui: escolheu uma opção
        (`escolhida`), escolheu e editou (`escolhida` + `texto_final`), ou
        escreveu do zero (só `texto_final`).
        """
        if escolhida is not None and escolhida not in (1, 2):
            raise ValueError(f"escolhida inválida: {escolhida!r} (use 1, 2 ou None)")
        if escolhida is None and not texto_final:
            raise ValueError(
                "escolha precisa de `escolhida` (1 ou 2) ou de `texto_final`"
            )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE rascunhos
                       SET escolhida = %s, texto_final = %s,
                           escolhido_em = now(), escolhido_por = %s
                     WHERE id = %s
                    """,
                    (escolhida, texto_final, por, rascunho_id),
                )

    def vincular_rascunho(
        self, rascunho_id: int, mensagem_id: int, *, estagio_no_envio: str | None = None
    ) -> bool:
        """Vincula rascunho -> mensagem realmente enviada (caminho 1 ou 2 do
        design.md). A garantia de que `mensagem_id` não é reivindicado duas
        vezes é do índice único parcial `rascunhos_mensagem_unica` — uma
        segunda tentativa de vincular a mesma mensagem propaga
        `psycopg.errors.UniqueViolation`, não é traduzida aqui.

        `WHERE mensagem_id IS NULL` (change
        `painel-mensagens-recentes-e-acoes-seguras`): sem isto, uma corrida
        entre duas reconciliações do MESMO rascunho (`acoes.
        reconciliar_rascunho` chamado duas vezes quase juntas, ou uma
        reconciliação automática cruzando com um registro manual) podia
        sobrescrever um vínculo já feito, silenciosamente — a segunda escrita
        vencia sem erro nenhum. Agora a segunda tentativa não afeta a linha
        (rowcount 0), e é isso — não uma exceção — que `Database.rowcount >
        0` abaixo traduz para `False`.

        Devolve `False` quando `rascunho_id` não existe OU quando já tinha um
        `mensagem_id` vinculado (nada mudou); `True` quando a linha foi
        encontrada, ainda sem vínculo, e foi atualizada.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE rascunhos
                       SET mensagem_id = %s,
                           estagio_no_envio = COALESCE(%s, estagio_no_envio)
                     WHERE id = %s AND mensagem_id IS NULL
                    """,
                    (mensagem_id, estagio_no_envio, rascunho_id),
                )
                return cur.rowcount > 0

    def rascunho_pendente_por_texto(
        self, conversa_id: int, texto: str, *, janela_horas: int = 48
    ) -> int | None:
        """Reconciliação pelo eco (design.md, caminho 2): casamento EXATO de
        texto normalizado (`_normalizar_texto`) contra `opcao_1`/`opcao_2`/
        `texto_final` de um rascunho pendente (`mensagem_id IS NULL`) das
        últimas `janela_horas`. Sem fuzzy, sem LLM — texto editado no envio
        não casa, e a função devolve `None` (requirement "Reconciliação pelo
        eco não usa casamento aproximado").

        A comparação é feita em Python, não em SQL: `_normalizar_texto` é a
        única definição de igualdade, e mantê-la fora do SQL evita que a
        regra de normalização precise ser reescrita (e possivelmente
        divergir) em dialeto de banco.
        """
        alvo = _normalizar_texto(texto)
        if not alvo:
            return None
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, opcao_1, opcao_2, texto_final FROM rascunhos
                     WHERE conversa_id = %s
                       AND mensagem_id IS NULL
                       AND gerado_em >= now() - make_interval(hours => %s)
                     ORDER BY gerado_em DESC
                    """,
                    (conversa_id, janela_horas),
                )
                candidatos = cur.fetchall()
        for rascunho_id, opcao_1, opcao_2, texto_final in candidatos:
            if alvo in (
                _normalizar_texto(opcao_1),
                _normalizar_texto(opcao_2),
                _normalizar_texto(texto_final),
            ):
                return rascunho_id
        return None

    def rascunho(self, rascunho_id: int) -> RascunhoRegistro | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"{self._RASCUNHO_SELECT} WHERE id = %s", (rascunho_id,))
                row = cur.fetchone()
                return RascunhoRegistro(*row) if row else None

    def rascunhos_da_conversa(
        self, conversa_id: int, limite: int = 5
    ) -> list[RascunhoRegistro]:
        """Histórico de rascunhos da conversa, mais recente primeiro — sem
        chamar LLM (leitura pura, `GET /api/conversas/{id}/rascunhos`)."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._RASCUNHO_SELECT} WHERE conversa_id = %s "
                    "ORDER BY gerado_em DESC LIMIT %s",
                    (conversa_id, limite),
                )
                return [RascunhoRegistro(*row) for row in cur.fetchall()]

    def rascunhos_vinculados_para_analise(
        self, *, incluir_teste: bool = False, apenas_teste: bool = False
    ) -> list[RascunhoVinculadoRegistro]:
        """Insumo do A/B natural de rascunho (§10, change `analise-desempenho`
        — bloqueado por `rascunho-registrado`).

        Só rascunhos com `mensagem_id` vinculado (o restante não corresponde
        a envio real). A janela de 72h é filtrada aqui no SQL; decidir se os
        estágios alcançados nela representam AVANÇO (comparar rank) é regra
        de domínio e fica em `metrics.py` via `taxonomia.rank_estagio` — não
        duplicamos a ordenação de estágio em SQL.

        Change `contatos-de-teste-isolados`: exclui contato de teste por
        padrão — ver `_condicao_teste`.
        """
        condicao = _condicao_teste(
            "ct.e_teste", incluir_teste=incluir_teste, apenas_teste=apenas_teste
        )
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT r.id, r.escolhida, r.texto_final IS NOT NULL,
                           r.estagio_no_envio,
                           ee.para
                      FROM rascunhos r
                      JOIN mensagens m ON m.id = r.mensagem_id
                      JOIN conversas c ON c.id = r.conversa_id
                      JOIN contatos ct ON ct.id = c.contato_id
                      LEFT JOIN eventos_estagio ee
                        ON ee.conversa_id = r.conversa_id
                       AND ee.em > m.enviada_em
                       AND ee.em <= m.enviada_em + interval '72 hours'
                     WHERE r.mensagem_id IS NOT NULL {condicao}
                     ORDER BY r.id
                    """
                )
                por_rascunho: dict[int, RascunhoVinculadoRegistro] = {}
                for rascunho_id, escolhida, editado, estagio_no_envio, para in cur.fetchall():
                    registro = por_rascunho.get(rascunho_id)
                    if registro is None:
                        registro = RascunhoVinculadoRegistro(
                            rascunho_id=rascunho_id,
                            escolhida=escolhida,
                            editado=editado,
                            estagio_no_envio=estagio_no_envio,
                            estagios_72h=[],
                        )
                        por_rascunho[rascunho_id] = registro
                    if para is not None and para not in registro.estagios_72h:
                        registro.estagios_72h.append(para)
                return list(por_rascunho.values())

    # -- resumos (§1 divergência registrada, change `resumo-conversa`) ----

    _RESUMO_SELECT = """
        SELECT id, conversa_id, resumo, proximo_passo, ultima_mensagem_id,
               estagio, temperatura, prompt_versao, modelo, gerado_em,
               gerado_por
          FROM resumos_conversa
    """

    def gravar_resumo(
        self,
        conversa_id: int,
        *,
        resumo: str | None,
        proximo_passo: str | None,
        ultima_mensagem_id: int | None,
        estagio: str,
        temperatura: str,
        prompt_versao: str,
        modelo: str | None = None,
        gerado_por: str | None = None,
    ) -> int:
        """Grava (ou substitui) o resumo na fronteira `(conversa_id,
        ultima_mensagem_id, prompt_versao)`. `ON CONFLICT ... DO UPDATE`
        cobre os dois casos que a rota precisa: clicar "gerar" duas vezes
        sem mensagem nova (mesma fronteira, no-op de conteúdo) e
        `?forcar=true` (mesma fronteira, conteúdo substituído de propósito).
        Fronteira nova (mensagem nova chegou) sempre insere linha nova — não
        há conflito de índice para apagar.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO resumos_conversa (
                        conversa_id, resumo, proximo_passo, ultima_mensagem_id,
                        estagio, temperatura, prompt_versao, modelo, gerado_por
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (conversa_id, coalesce(ultima_mensagem_id, 0), prompt_versao)
                    DO UPDATE SET
                        resumo = EXCLUDED.resumo,
                        proximo_passo = EXCLUDED.proximo_passo,
                        estagio = EXCLUDED.estagio,
                        temperatura = EXCLUDED.temperatura,
                        modelo = EXCLUDED.modelo,
                        gerado_em = now(),
                        gerado_por = EXCLUDED.gerado_por
                    RETURNING id
                    """,
                    (
                        conversa_id, resumo, proximo_passo, ultima_mensagem_id,
                        estagio, temperatura, prompt_versao, modelo, gerado_por,
                    ),
                )
                return cur.fetchone()[0]

    def resumo_vigente(self, conversa_id: int, prompt_versao: str) -> ResumoConversa | None:
        """O resumo mais recente para esta conversa e versão de prompt —
        cache que a rota `POST /api/conversas/{id}/resumo` confere ANTES de
        chamar o LLM (requirement "Cache por versão de prompt e mensagem").
        Nunca chama LLM: leitura pura.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._RESUMO_SELECT} WHERE conversa_id = %s AND prompt_versao = %s "
                    "ORDER BY coalesce(ultima_mensagem_id, 0) DESC, gerado_em DESC LIMIT 1",
                    (conversa_id, prompt_versao),
                )
                row = cur.fetchone()
                return ResumoConversa(*row) if row else None

    def mensagens_desde(self, conversa_id: int, mensagem_id: int | None) -> int:
        """Quantas mensagens da conversa têm id maior que `mensagem_id` —
        a staleness do resumo (§8 mesma lógica: contagem, não timestamp).
        `mensagem_id=None` conta todas as mensagens da conversa.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM mensagens WHERE conversa_id = %s AND id > %s",
                    (conversa_id, mensagem_id or 0),
                )
                return cur.fetchone()[0]

    def primeira_mensagem_pendente_em(
        self, conversa_id: int, desde_id: int | None
    ) -> datetime | None:
        """`enviada_em` da mensagem pendente mais antiga (change
        `extracao-em-lote-por-janela`): há quanto tempo a fila de extração
        desta conversa está esperando, para o gatilho híbrido de
        `webhook._deve_extrair_agora` decidir se vale extrair na hora.
        `None` quando não há mensagem pendente.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT min(enviada_em) FROM mensagens "
                    "WHERE conversa_id = %s AND id > %s",
                    (conversa_id, desde_id or 0),
                )
                linha = cur.fetchone()
        return linha[0] if linha else None

    # -- retenção (§12) ---------------------------------------------------

    def purgar_mensagens_antigas(self, meses: int = 12) -> int:
        """Descarta `mensagens` de conversas encerradas há mais de `meses`.

        §12: mantém `fatos`, `objecoes` e `eventos_estagio` — que é o que
        serve para análise e não guarda conteúdo pessoal. O telefone em claro
        também sai, porque o motivo de guardá-lo era poder enviar, e uma
        conversa encerrada há um ano não recebe envio.

        Extensão do change `rascunho-registrado`: `rascunhos.opcao_1`/
        `opcao_2`/`texto_final` de todo rascunho de uma conversa encerrada
        há mais de `meses` também é conteúdo pessoal (texto escrito para
        aquele cliente) e sai — a linha em si (contexto, escolha,
        timestamps) permanece. O `UPDATE` roda ANTES do `DELETE` de
        `mensagens`, mas isso hoje é só sequência dentro da mesma
        transação, não uma dependência de dado: ver `purga-cobre-
        rascunhos-sem-vinculo` (§12) — o join é direto por
        `r.conversa_id = c.id`, não mais via `mensagens`, porque a maioria
        dos rascunhos reais nunca chega a ser vinculada a uma mensagem
        (`mensagem_id IS NULL`: gerado e editado antes de enviar, ou
        escolhido manualmente) e um join via `mensagens` simplesmente não
        os alcança — o texto pessoal sobrevivia à purga em claro.

        Extensão do change `resumo-conversa`: `resumos_conversa.resumo`/
        `proximo_passo` é prosa DERIVADA das mensagens do cliente — mesmo
        conteúdo pessoal, mesmo critério de idade. Mesma correção do
        `purga-cobre-rascunhos-sem-vinculo` (§12): o join é direto por
        `r.conversa_id = c.id`, não mais via `mensagens.ultima_mensagem_id`
        — um resumo alcança a purga mesmo com `ultima_mensagem_id` `NULL`
        ou já apontando para uma mensagem que uma purga anterior apagou
        (o `ON DELETE SET NULL` do FK zera a coluna, mas o resumo em si
        continua precisando ser anonimizado). Diferente de `rascunhos` não
        há constraint de forma exigindo texto não-nulo aqui, então o valor
        vira `NULL` direto — sem placeholder.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE rascunhos r
                       SET opcao_1 = CASE WHEN r.opcao_1 IS NOT NULL
                                          THEN %(marca)s ELSE NULL END,
                           opcao_2 = CASE WHEN r.opcao_2 IS NOT NULL
                                          THEN %(marca)s ELSE NULL END,
                           texto_final = CASE WHEN r.texto_final IS NOT NULL
                                              THEN %(marca)s ELSE NULL END
                      FROM conversas c
                     WHERE r.conversa_id = c.id
                       AND c.resultado IS NOT NULL
                       AND c.atualizado_em < now() - make_interval(months => %(meses)s)
                    """,
                    {"marca": TEXTO_RASCUNHO_PURGADO, "meses": meses},
                )
                cur.execute(
                    """
                    UPDATE resumos_conversa r
                       SET resumo = NULL,
                           proximo_passo = NULL
                      FROM conversas c
                     WHERE r.conversa_id = c.id
                       AND c.resultado IS NOT NULL
                       AND c.atualizado_em < now() - make_interval(months => %(meses)s)
                    """,
                    {"meses": meses},
                )
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

    # -- eventos brutos (staging, change `ingestao-a-prova-de-falha`) -----

    def registrar_evento_bruto(self, payload: Any) -> int:
        """Grava o payload cru do webhook ANTES de qualquer parsing/
        `ingerir()` — design.md do change `ingestao-a-prova-de-falha`.

        Chamado por `webhook.py::_processar`, sempre, mesmo que o evento
        acabe sendo benigno. Nunca por `cmd_ingerir`: ali há um operador
        olhando a saída no terminal, o staging existe para o caminho
        automático, sem ninguém olhando.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO eventos_recebidos_bruto (payload) VALUES (%s) "
                    "RETURNING id",
                    (Json(payload),),
                )
                return cur.fetchone()[0]

    def marcar_evento_bruto_processado(self, evento_id: int) -> None:
        """`ingerir()` terminou sem exceção — a linha sai da lista de
        pendentes de `listar_eventos_brutos_pendentes` e passa a ser
        candidata de `purgar_eventos_brutos_antigos`."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE eventos_recebidos_bruto SET processado = TRUE, "
                    "processado_em = now(), erro = NULL WHERE id = %s",
                    (evento_id,),
                )

    def marcar_evento_bruto_falhou(self, evento_id: int, erro: str) -> None:
        """`ingerir()` levantou uma exceção — a linha permanece
        `processado = FALSE` (spec.md, "Falha de ingestão deixa rastro
        reprocessável"), com o erro registrado e a tentativa contada.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE eventos_recebidos_bruto SET erro = %s, "
                    "tentativas = tentativas + 1 WHERE id = %s",
                    (erro[:2000] if erro else erro, evento_id),
                )

    def excluir_evento_bruto(self, evento_id: int) -> None:
        """Remove a linha imediatamente — não espera
        `purgar_eventos_brutos_antigos` (change
        `ingestao-restrita-por-instancia`, revisão da Decisão 2 do
        `design.md`).

        Chamado por `webhook.py::_processar` só quando `ingerir()` decide
        que o evento é de uma instância restrita e telefone desconhecido:
        o payload já serviu ao único propósito que tinha (deixar a decisão
        reprocessável em caso de falha), a decisão foi tomada com sucesso
        (nenhuma exceção), e não há razão de negócio pra guardar o
        conteúdo de mensagem de alguém que nunca teve relação nenhuma com
        a Camu — nem que seja pelos poucos dias de `RETENCAO_EVENTOS_
        BRUTOS_DIAS`.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM eventos_recebidos_bruto WHERE id = %s", (evento_id,)
                )

    def listar_eventos_brutos_pendentes(self, limite: int = 200) -> list[EventoBrutoRegistro]:
        """Linhas `processado = FALSE`, em ordem de chegada — o que
        `camucrm reprocessar-falhas` tenta reingerir."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, payload, recebido_em, processado, "
                    "processado_em, erro, tentativas "
                    "FROM eventos_recebidos_bruto WHERE NOT processado "
                    "ORDER BY id LIMIT %s",
                    (limite,),
                )
                return [EventoBrutoRegistro(*row) for row in cur.fetchall()]

    def purgar_eventos_brutos_antigos(
        self, dias: int = RETENCAO_EVENTOS_BRUTOS_DIAS
    ) -> int:
        """Remove só linhas `processado = TRUE` mais antigas que `dias`
        (design.md: retenção de curto prazo, não histórico permanente).

        `processado = FALSE` nunca é candidato, para nenhuma idade — apagar
        uma falha ainda não resolvida repetiria exatamente o bug que este
        change corrige (spec.md, "Retenção da caixa de reprocessamento não
        apaga falha pendente").
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM eventos_recebidos_bruto WHERE processado "
                    "AND recebido_em < now() - make_interval(days => %s)",
                    (dias,),
                )
                return cur.rowcount

    # -- prospecção B2B (change `prospeccao-b2b-shortlist`) ----------------
    #
    # Tabela inteiramente separada de `contatos`/`conversas` (design.md,
    # requirement "Shortlist separada de contatos/conversas"): nenhum método
    # abaixo é chamado por `listar_conversas_abertas`/`metrics.py`/qualquer
    # leitura de kanban, fila, conversas ou métricas — o único ponto de
    # contato com o resto do sistema é a leitura de `prospeccao_por_
    # telefone_hash` que `ingest.ingerir` faz para decidir o `tipo` do
    # contato novo.

    _PROSPECCAO_SELECT = """
        SELECT p.id, p.nome, p.telefone, p.bairro, p.zona, p.nota,
               p.avaliacoes, p.site, p.tier_origem, p.status_origem,
               p.aberto_em, p.aberto_por, p.criado_em,
               p.enviado_em, p.enviado_por, p.enviado_erro
          FROM prospeccoes p
    """

    def importar_prospeccoes(self, linhas: Iterable[dict[str, Any]]) -> ResumoImportacao:
        """Upsert por `telefone_hash` — reimportar a mesma planilha ATUALIZA,
        nunca duplica (requirement "Reimportar a mesma planilha atualiza,
        não duplica"). `linhas` é o que `csv.DictReader` produz (chaves =
        cabeçalho do CSV do usuário: `petshop, bairro, zona, telefone, nota,
        avaliacoes, site, tier_origem, status_origem`); o parsing do arquivo
        em si é responsabilidade de quem chama (`camucrm/painel/api.py`),
        não deste método.

        Telefone ilegível (`normalizar_telefone_br` devolve `None`) ou nome
        vazio reprova só AQUELA linha, sempre reportada em `invalidas` com o
        motivo — nunca descartada em silêncio (requirement "Importação
        nunca descarta linha em silêncio"). Campo numérico malformado
        (`nota`/`avaliacoes`) não reprova a linha, vira `NULL`
        (`_float_ou_none`/`_int_ou_none`).

        `(xmax = 0) AS inserida`: truque padrão de Postgres para saber, no
        mesmo `INSERT ... ON CONFLICT DO UPDATE`, se a linha era nova ou já
        existia — evita uma segunda consulta só para contar novos/
        atualizados.
        """
        novos = 0
        atualizados = 0
        invalidas: list[LinhaInvalida] = []
        with self._conn() as conn:
            with conn.cursor() as cur:
                for indice, linha in enumerate(linhas, start=1):
                    nome = (linha.get("petshop") or "").strip()
                    telefone_bruto = linha.get("telefone") or ""
                    telefone = normalizar_telefone_br(telefone_bruto)
                    if telefone is None:
                        invalidas.append(
                            LinhaInvalida(
                                linha=indice,
                                petshop=nome or None,
                                motivo=f"telefone ilegível: {telefone_bruto!r}",
                            )
                        )
                        continue
                    if not nome:
                        invalidas.append(
                            LinhaInvalida(
                                linha=indice, petshop=None,
                                motivo="nome do petshop vazio",
                            )
                        )
                        continue
                    telefone_hash = hash_telefone(telefone)
                    cur.execute(
                        """
                        INSERT INTO prospeccoes (
                            nome, telefone, telefone_hash, bairro, zona, nota,
                            avaliacoes, site, tier_origem, status_origem
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (telefone_hash) DO UPDATE SET
                            nome = EXCLUDED.nome,
                            bairro = EXCLUDED.bairro,
                            zona = EXCLUDED.zona,
                            nota = EXCLUDED.nota,
                            avaliacoes = EXCLUDED.avaliacoes,
                            site = EXCLUDED.site,
                            tier_origem = EXCLUDED.tier_origem,
                            status_origem = EXCLUDED.status_origem,
                            atualizado_em = now()
                        RETURNING (xmax = 0) AS inserida
                        """,
                        (
                            nome, telefone, telefone_hash,
                            linha.get("bairro") or None, linha.get("zona") or None,
                            _float_ou_none(linha.get("nota")),
                            _int_ou_none(linha.get("avaliacoes")),
                            linha.get("site") or None,
                            linha.get("tier_origem") or None,
                            linha.get("status_origem") or None,
                        ),
                    )
                    if cur.fetchone()[0]:
                        novos += 1
                    else:
                        atualizados += 1
        return ResumoImportacao(novos=novos, atualizados=atualizados, invalidas=invalidas)

    def listar_prospeccoes(
        self,
        *,
        zona: str | None = None,
        bairro: str | None = None,
        nota_minima: float | None = None,
        tier: str | None = None,
        apenas_nao_convertidas: bool = False,
        limite: int = 500,
    ) -> list[ProspeccaoRegistro]:
        """Lista com filtros + detecção de conversão (design.md: "sem estado
        próprio") via `LEFT JOIN` por `telefone_hash` contra `contatos`.
        `contato_id`/`conversa_id` não nulos = a linha já é conversa real —
        quem chama (`camucrm/painel/views.py`) decide se mostra o link de
        WhatsApp ou o link para `#/conversas/{conversa_id}`.

        A conversa escolhida por contato (`LEFT JOIN LATERAL`) prioriza a
        aberta, senão a mais recente encerrada — mesma ordem de
        `Database.marcar_contato_teste` (`ORDER BY (resultado IS NULL) DESC,
        id DESC`), para o link sempre apontar para algo navegável.
        """
        condicoes = []
        params: list[Any] = []
        if zona:
            condicoes.append("p.zona = %s")
            params.append(zona)
        if bairro:
            condicoes.append("p.bairro = %s")
            params.append(bairro)
        if nota_minima is not None:
            condicoes.append("p.nota >= %s")
            params.append(nota_minima)
        if tier:
            condicoes.append("p.tier_origem = %s")
            params.append(tier)
        if apenas_nao_convertidas:
            condicoes.append("c.id IS NULL")
        where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""
        params.append(limite)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT p.id, p.nome, p.telefone, p.bairro, p.zona, p.nota,
                           p.avaliacoes, p.site, p.tier_origem, p.status_origem,
                           p.aberto_em, p.aberto_por, p.criado_em,
                           p.enviado_em, p.enviado_por, p.enviado_erro,
                           c.id AS contato_id, cv.id AS conversa_id
                      FROM prospeccoes p
                      LEFT JOIN contatos c ON c.telefone_hash = p.telefone_hash
                      LEFT JOIN LATERAL (
                          SELECT id FROM conversas
                           WHERE contato_id = c.id
                           ORDER BY (resultado IS NULL) DESC, id DESC
                           LIMIT 1
                      ) cv ON c.id IS NOT NULL
                     {where}
                     ORDER BY p.nome
                     LIMIT %s
                    """,
                    tuple(params),
                )
                return [ProspeccaoRegistro(*row) for row in cur.fetchall()]

    def marcar_prospeccao_aberta(self, prospeccao_id: int, *, por: str | None = None) -> None:
        """Registra que o operador clicou "abrir WhatsApp" — intenção
        registrada, nunca confirmação de envio (design.md: o sistema não tem
        como saber se a mensagem foi de fato enviada dentro do WhatsApp)."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE prospeccoes SET aberto_em = now(), aberto_por = %s "
                    "WHERE id = %s",
                    (por, prospeccao_id),
                )

    def registrar_envio_prospeccao(
        self, prospeccao_id: int, *, por: str, sucesso: bool, erro: str | None = None
    ) -> None:
        """Grava o resultado de UMA tentativa de envio pela Evolution API
        (change `envio-prospeccao-pela-evolution-api`) — distinto de
        `marcar_prospeccao_aberta` acima: aquele é intenção (clicou no
        link), isto é confirmação (a API respondeu).

        Sucesso grava `enviado_em = now()` e limpa `enviado_erro` (a
        tentativa mais recente funcionou). Falha grava só `enviado_erro` —
        `enviado_em` NÃO é apagado: se uma tentativa anterior teve sucesso,
        esse registro precisa sobreviver a uma tentativa nova que falhou,
        para a tela poder mostrar "enviado em X, mas a tentativa mais
        recente falhou" em vez de perder o histórico do que funcionou.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                if sucesso:
                    cur.execute(
                        "UPDATE prospeccoes SET enviado_em = now(), enviado_por = %s, "
                        "enviado_erro = NULL WHERE id = %s",
                        (por, prospeccao_id),
                    )
                else:
                    cur.execute(
                        "UPDATE prospeccoes SET enviado_por = %s, enviado_erro = %s "
                        "WHERE id = %s",
                        (por, erro, prospeccao_id),
                    )

    def prospeccao_por_telefone_hash(self, telefone_hash: str) -> ProspeccaoRegistro | None:
        """Usado por `ingest.ingerir` para decidir se um contato novo nasce
        `tipo=b2b` (requirement "Conversão usa tipo B2B da origem curada").
        Sem o `LEFT JOIN` de `listar_prospeccoes` — quem chama só precisa
        saber se a linha existe, não do estado de conversão.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._PROSPECCAO_SELECT} WHERE p.telefone_hash = %s",
                    (telefone_hash,),
                )
                row = cur.fetchone()
                return ProspeccaoRegistro(*row) if row else None


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    return valor if isinstance(valor, str) else str(valor)
