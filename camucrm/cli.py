"""CLI do CRM. A fila é o produto; o resto são ferramentas em volta dela.

§0: "A saída do sistema é uma fila que alguém precisa abrir. Se a fila não for
aberta por 5 dias úteis seguidos, o problema não é o sistema — e nenhuma
feature conserta isso."

Por isso `camucrm fila` é o comando sem cerimônia: sem argumento obrigatório,
saída curta, no máximo 10 nomes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import acoes, config, metrics
from .acoes import AcaoInvalidaError
from .backfill import extrair_historico, importar_conversas
from .db import Database, MARCOS_MANUAIS, TetoFollowupError
from .drafts import RascunhoInvalidoError, gerar as gerar_rascunho
from .extraction import FATOS_BOOLEANOS
from .ingest import ingerir
from .extraction.extractor import Extrator
from .llm import LlmIndisponivelError, criar_llm
from .pipeline import recalcular, recalcular_todas
from .rules.fila import Candidato, formatar_fila, montar_fila
from .rules.estagio import sugere_b2b
from .rules.temperatura import classificar
from .taxonomia import BOLA_CAMU, FILA_TAMANHO_MAXIMO, estagio_label
from .transport import Destinatario, criar_transporte


def _db() -> Database:
    banco = Database(config.dsn())
    banco.init_pool()
    return banco


def _operador(args) -> str:
    quem = (getattr(args, "por", None) or config.operador()).strip()
    if not quem:
        raise SystemExit(
            "informe quem está operando: --por NOME (ou defina CAMU_OPERADOR). "
            "Ação humana precisa de nome para ser auditável."
        )
    return quem


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------


def cmd_init(args) -> int:
    banco = _db()
    banco.ensure_schema()
    print(f"Schema aplicado em {config.dsn().rsplit('@', 1)[-1]}")
    return 0


def cmd_fila(args) -> int:
    """A fila do dia. O comando que precisa ser rodado toda manhã."""
    banco = _db()
    agora = datetime.now(timezone.utc)
    candidatos = []
    for conversa in banco.listar_conversas_abertas():
        estado = recalcular(banco, conversa, agora=agora, persistir=not args.simular)
        candidatos.append(
            Candidato(
                conversa_id=conversa.id,
                nome=conversa.nome_contato or f"#{conversa.id}",
                funil=conversa.funil,
                estagio=estado.estagio,
                classificacao=estado.classificacao,
                sinais=estado.sinais,
            )
        )
    itens = montar_fila(candidatos, limite=args.limite)
    print(formatar_fila(itens, data=agora.strftime("%d/%m")))
    if args.motivos:
        print()
        for item in itens:
            print(f"  #{item.conversa_id} {item.nome}: {item.motivo}")
    fora = len(candidatos) - len(itens)
    if fora > 0:
        print(f"\n({fora} conversa(s) aberta(s) não entraram na fila hoje)")
    return 0


def cmd_extrair(args) -> int:
    banco = _db()
    extrator = Extrator(banco, criar_llm(args.provider))
    if args.conversa:
        resultados = [extrator.processar_conversa(args.conversa, forcar=args.forcar)]
    else:
        resultados = extrator.processar_todas()
    for r in resultados:
        estagio = r.estado.estagio if r.estado else "?"
        temperatura = r.estado.temperatura if r.estado else "?"
        marca = "!" if r.erro else " "
        print(
            f"{marca} #{r.conversa_id}: {r.mensagens_processadas} msg -> "
            f"{estagio} ({estagio_label(estagio)}), {temperatura.upper()}"
            + (f" — ERRO: {r.erro}" if r.erro else "")
        )
        for democao in r.democoes:
            print(f"      rebaixado: {democao}")
    return 0


def cmd_recalcular(args) -> int:
    """Reaplica as regras sobre os fatos já extraídos. Não chama LLM (§1)."""
    banco = _db()
    estados = recalcular_todas(banco)
    mudaram = [e for e in estados if e.transicao]
    print(f"{len(estados)} conversa(s) recalculada(s), {len(mudaram)} transição(ões).")
    for estado in mudaram:
        t = estado.transicao
        print(f"  #{estado.conversa_id}: {t.de} -> {t.para} ({t.motivo})")
    return 0


def cmd_rascunho(args) -> int:
    banco = _db()
    conversa = banco.get_conversa(args.conversa)
    if conversa is None:
        raise SystemExit(f"conversa {args.conversa} não existe")
    estado = recalcular(banco, conversa, persistir=False)
    historico = [(m.direcao, m.texto) for m in banco.listar_mensagens(conversa.id)]
    try:
        rascunho = gerar_rascunho(
            criar_llm(args.provider),
            historico[-20:],
            estagio=estado.estagio,
            temperatura=estado.temperatura,
            funil=conversa.funil,
            followups_enviados=conversa.followups_enviados,
            playbook=config.playbook(),
        )
    except (RascunhoInvalidoError, LlmIndisponivelError) as exc:
        raise SystemExit(f"não foi possível rascunhar: {exc}") from exc
    print(f"#{conversa.id} {conversa.nome_contato or ''} — {estado.estagio}, "
          f"{estado.temperatura.upper()}")
    print()
    print(rascunho)
    if not rascunho.encerrar:
        print()
        print("Escolha uma, edite, e envie você mesmo. O sistema não envia (§10).")
    return 0


def cmd_enviar(args) -> int:
    """Envia um texto por um contato. Exige nome de quem aprovou (§1, §10)."""
    quem = _operador(args)
    banco = _db()
    conversa = banco.get_conversa(args.conversa)
    if conversa is None:
        raise SystemExit(f"conversa {args.conversa} não existe")
    with banco._conn() as conn:  # noqa: SLF001
        with conn.cursor() as cur:
            cur.execute(
                "SELECT telefone, nome FROM contatos WHERE id = %s", (conversa.contato_id,)
            )
            linha = cur.fetchone()
    if not linha or not linha[0]:
        raise SystemExit("contato sem telefone em claro — não é possível enviar (§12)")

    transporte = criar_transporte(args.transporte)
    resultado = transporte.enviar(
        Destinatario(linha[0], linha[1]), args.texto, aprovado_por=quem
    )
    if resultado.entregue:
        banco.registrar_mensagem(
            conversa.id, "out", args.texto, externa_id=resultado.externa_id
        )
        print(f"Enviado por {transporte.nome} (aprovado por {quem}).")
    else:
        print(f"Não enviado: {resultado.detalhe}")
    if args.followup:
        try:
            numero = banco.registrar_followup(conversa.id, args.texto)
            print(f"Registrado como follow-up {numero}/2.")
        except TetoFollowupError as exc:
            print(f"AVISO: {exc}")
    return 0


def cmd_followup(args) -> int:
    """Registra que um follow-up foi enviado. O banco recusa o terceiro (§6)."""
    banco = _db()
    try:
        numero = banco.registrar_followup(args.conversa, args.texto)
    except TetoFollowupError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Follow-up {numero}/2 registrado na conversa {args.conversa}.")
    return 0


def cmd_marcar(args) -> int:
    """Marca um marco manual. Sequência real em `acoes.marcar_marco`.

    A CLI e o painel (drop numa coluna de marco no kanban) chamam a mesma
    função — nenhum dos dois caminhos reimplementa a validação nem os
    efeitos (`acoes-humanas`, requirement "Ação humana compartilhada entre
    CLI e painel").
    """
    quem = _operador(args)
    banco = _db()
    try:
        resultado = acoes.marcar_marco(banco, args.conversa, args.marco, por=quem)
    except AcaoInvalidaError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"#{resultado.conversa_id}: marco `{resultado.marco}` por {quem} "
        f"-> estágio {resultado.estado.estagio}"
    )
    return 0


def cmd_tipo(args) -> int:
    """Classifica um contato como B2B ou B2C. Decisão humana, sempre.

    §1 tira inferência de decisão de negócio, e esta é a mais consequente:
    contato no funil errado sai da fila pela regra errada e ninguém descobre
    por quê. Por isso nada aqui é automático — o sistema só sugere (ver
    `rules.estagio.sugere_b2b`) e espera alguém que conhece o cliente decidir.
    """
    quem = _operador(args)
    banco = _db()
    conversa = banco.get_conversa(args.conversa)
    if conversa is None:
        raise SystemExit(f"conversa {args.conversa} não existe")
    if conversa.funil == args.tipo:
        print(f"#{conversa.id} já é {args.tipo.upper()}; nada mudou.")
        return 0

    try:
        resultado = acoes.mudar_funil_conversa(banco, args.conversa, args.tipo, por=quem)
    except AcaoInvalidaError as exc:
        raise SystemExit(str(exc)) from exc

    if resultado.movimento:
        print(
            f"#{conversa.id} {conversa.nome_contato}: {resultado.anterior.upper()} -> "
            f"{resultado.novo.upper()}, estágio {resultado.movimento.de} -> "
            f"{resultado.movimento.para}"
        )
    else:
        print(
            f"#{conversa.id} {conversa.nome_contato}: {resultado.anterior.upper()} -> "
            f"{resultado.novo.upper()}"
        )
    return 0


def cmd_corrigir(args) -> int:
    """Grava uma correção humana (§7). Toda correção passa por aqui."""
    quem = _operador(args)
    banco = _db()
    banco.registrar_correcao(args.conversa, args.campo, args.de, args.para, por=quem)
    print(f"Correção gravada: #{args.conversa} {args.campo}: {args.de!r} -> {args.para!r}")
    print("(alimenta o eval e revela o que o prompt não está vendo)")
    return 0


def cmd_backfill(args) -> int:
    banco = _db()
    if args.arquivo:
        registros = json.loads(Path(args.arquivo).read_text(encoding="utf-8"))
        resumo = importar_conversas(banco, registros)
        print(f"Importado: {resumo}")
    if args.extrair:
        extrator = Extrator(banco, criar_llm(args.provider))
        resumo, _ = extrair_historico(banco, extrator)
        print(f"Extraído (origem=backfill): {resumo}")
        print("Lembrete (§8): estes eventos ficam fora de qualquer métrica de tempo.")
    return 0


def cmd_eval(args) -> int:
    from .evaluation import carregar, rodar

    conversas = carregar(args.dataset)
    relatorio = rodar(criar_llm(args.provider), conversas)
    print(relatorio)
    return 0 if relatorio.aprovado else 1


def cmd_metricas(args) -> int:
    banco = _db()
    desde = (
        datetime.now(timezone.utc) - timedelta(days=args.dias) if args.dias else None
    )
    print(metrics.relatorio(banco, desde=desde))
    return 0


def cmd_purgar(args) -> int:
    banco = _db()
    apagadas = banco.purgar_mensagens_antigas(args.meses)
    print(f"{apagadas} mensagem(ns) descartada(s) (§12, retenção de {args.meses} meses).")
    print("Preservados: fatos, objeções e eventos de estágio.")
    return 0


def cmd_ingerir(args) -> int:
    """Lê um payload de webhook do stdin e ingere.

    Mesmo caminho que o webhook usa (`camucrm.ingest`) — dois caminhos de
    entrada acabariam divergindo.
    """
    banco = _db()
    transporte = criar_transporte(args.transporte)
    payload = json.loads(sys.stdin.read())
    print(ingerir(banco, transporte.receber(payload), origem="whatsapp"))
    return 0


def cmd_acompanhar(args) -> int:
    """Painel de terminal que redesenha sozinho: o que entrou e onde parou.

    Não é o painel da §13 (aquele é o passo 8 e só faz sentido com histórico).
    É um instrumento de operação e de teste: mostra a conversa chegando, o
    estágio subindo e a fila mudando, para dar para ver o sistema trabalhando
    em vez de conferir tabela por tabela.

    Com `--extrair`, roda a extração a cada ciclo. Ela só chama o LLM quando
    há mensagem nova, então o custo acompanha o movimento e não o relógio.
    """
    banco = _db()
    extrator = Extrator(banco, criar_llm(args.provider)) if args.extrair else None
    logging.getLogger().setLevel(logging.WARNING)

    try:
        while True:
            if extrator is not None:
                try:
                    extrator.processar_todas()
                except Exception as exc:  # noqa: BLE001 - não derruba o painel
                    print(f"(extração falhou: {exc})")
            _desenhar(banco, extraindo=extrator is not None)
            if args.uma_vez:
                return 0
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print()
        return 0


def _desenhar(banco: Database, *, extraindo: bool) -> None:
    agora = datetime.now(timezone.utc)
    print("\033[2J\033[H", end="")  # limpa a tela e volta ao topo

    local = agora.astimezone()
    modo = "extraindo" if extraindo else "só observando"
    print(f"camu-crm — {local:%d/%m %H:%M:%S} ({modo})")
    print("=" * 72)

    conversas = banco.listar_conversas_abertas()
    candidatos = []
    for conversa in conversas:
        estado = recalcular(banco, conversa, agora=agora, persistir=False)
        candidatos.append(
            Candidato(
                conversa_id=conversa.id,
                nome=conversa.nome_contato or f"#{conversa.id}",
                funil=conversa.funil,
                estagio=estado.estagio,
                classificacao=estado.classificacao,
                sinais=estado.sinais,
            )
        )

    print(f"\nCONVERSAS ABERTAS ({len(conversas)})")
    if not candidatos:
        print("  nenhuma ainda — mande uma mensagem para o WhatsApp da Camu")
    for c in sorted(candidatos, key=lambda c: -(c.sinais.horas_desde_inbound or 0)):
        bola = "nossa" if c.sinais.bola_com == BOLA_CAMU else "dele"
        marca = (
            "  << parece petshop: `camucrm tipo %s b2b`" % c.conversa_id
            if sugere_b2b(c.funil, banco.fatos_da_conversa(c.conversa_id))
            else ""
        )
        print(
            f"  #{c.conversa_id:<4} {c.nome[:22]:<22} "
            f"{c.estagio:<3} {estagio_label(c.estagio)[:18]:<18} "
            f"{c.classificacao.temperatura.upper():<10} bola: {bola}{marca}"
        )

    print("\nÚLTIMAS MENSAGENS")
    for direcao, texto, quando, nome in _ultimas_mensagens(banco):
        seta = "<-" if direcao == "in" else "->"
        quem = "cliente" if direcao == "in" else "Camu   "
        print(f"  {quando:%H:%M} {seta} {quem} {nome[:16]:<16} {texto[:44]}")

    itens = montar_fila(candidatos)
    print(f"\nFILA DE HOJE ({len(itens)})")
    if not itens:
        print("  vazia")
    for i, item in enumerate(itens, 1):
        print(f"  {i}. [{item.prioridade}] {item.nome[:22]:<22} {item.acao}")

    print("\n(ctrl+c para sair)")


def _ultimas_mensagens(banco: Database, limite: int = 8):
    """Wrapper fino: o SQL mora em `db.ultimas_mensagens_globais` (CLAUDE.md:
    "db.py é o único lugar do repo com SQL"). Movido de propósito no change
    `painel-leitura`, para que `cli.acompanhar` e o painel web leiam da mesma
    consulta em vez de duas SQLs que podem divergir."""
    return banco.ultimas_mensagens_globais(limite)


def cmd_servir(args) -> int:
    """Sobe o receptor de webhook da Evolution API."""
    from .webhook import PORTA_PADRAO, servir

    porta = args.porta or PORTA_PADRAO
    print(f"Ouvindo em http://0.0.0.0:{porta}/webhook/evolution")
    print("Este serviço não envia nada — só recebe (§10).")
    servir(porta)
    return 0


def cmd_painel(args) -> int:
    """Sobe o painel web de leitura (§13, antecipado — change `painel-leitura`)."""
    from .painel import PORTA_PADRAO, servir as servir_painel

    porta = args.porta or PORTA_PADRAO
    print(f"Painel em http://127.0.0.1:{porta}")
    print("Este painel não envia nada — envio continua por `camucrm enviar` (§10).")
    servir_painel(porta)
    return 0



# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="camucrm",
        description="CRM de conversas da Camu — LLM extrai, regra decide, humano envia.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("init", help="cria/atualiza o schema").set_defaults(func=cmd_init)

    p = sub.add_parser("fila", help="a fila do dia (no máximo 10 nomes)")
    p.add_argument("--limite", type=int, default=FILA_TAMANHO_MAXIMO)
    p.add_argument("--motivos", action="store_true", help="mostra o sinal que disparou")
    p.add_argument("--simular", action="store_true", help="não grava o recálculo")
    p.set_defaults(func=cmd_fila)

    p = sub.add_parser("extrair", help="roda a extração sobre o bloco novo")
    p.add_argument("--conversa", type=int)
    p.add_argument("--forcar", action="store_true", help="reprocessa do início")
    p.add_argument("--provider")
    p.set_defaults(func=cmd_extrair)

    p = sub.add_parser("recalcular", help="reaplica as regras sem chamar LLM")
    p.set_defaults(func=cmd_recalcular)

    p = sub.add_parser("rascunho", help="duas opções de resposta para uma conversa")
    p.add_argument("conversa", type=int)
    p.add_argument("--provider")
    p.set_defaults(func=cmd_rascunho)

    p = sub.add_parser("enviar", help="envia um texto (exige --por)")
    p.add_argument("conversa", type=int)
    p.add_argument("--texto", required=True)
    p.add_argument("--por", help="quem aprovou o envio")
    p.add_argument("--transporte")
    p.add_argument("--followup", action="store_true", help="conta como follow-up")
    p.set_defaults(func=cmd_enviar)

    p = sub.add_parser("followup", help="registra um follow-up enviado (teto de 2)")
    p.add_argument("conversa", type=int)
    p.add_argument("--texto")
    p.set_defaults(func=cmd_followup)

    p = sub.add_parser("marcar", help="registra um marco manual")
    p.add_argument("conversa", type=int)
    p.add_argument("marco", choices=MARCOS_MANUAIS)
    p.add_argument("--por")
    p.set_defaults(func=cmd_marcar)

    p = sub.add_parser("tipo", help="classifica a conversa como b2b ou b2c")
    p.add_argument("conversa", type=int)
    p.add_argument("tipo", choices=["b2b", "b2c"])
    p.add_argument("--por")
    p.set_defaults(func=cmd_tipo)

    p = sub.add_parser("corrigir", help="grava uma correção humana (§7)")
    p.add_argument("conversa", type=int)
    p.add_argument("campo", choices=[*FATOS_BOOLEANOS, "objecao", "estagio", "temperatura"])
    p.add_argument("--de", required=True)
    p.add_argument("--para", required=True)
    p.add_argument("--por")
    p.set_defaults(func=cmd_corrigir)

    p = sub.add_parser("backfill", help="importa e extrai o histórico (§8)")
    p.add_argument("--arquivo", help="JSON com as conversas históricas")
    p.add_argument("--extrair", action="store_true")
    p.add_argument("--provider")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("eval", help="roda o eval contra o conjunto rotulado (§7)")
    p.add_argument("dataset", nargs="?", default="data/eval/conversas.jsonl")
    p.add_argument("--provider")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("metricas", help="os três números da §14")
    p.add_argument("--dias", type=int, default=0, help="janela; 0 = tudo")
    p.set_defaults(func=cmd_metricas)

    p = sub.add_parser("purgar", help="retenção de mensagens (§12)")
    p.add_argument("--meses", type=int, default=12)
    p.set_defaults(func=cmd_purgar)

    p = sub.add_parser("ingerir", help="lê um webhook do stdin")
    p.add_argument("--transporte")
    p.set_defaults(func=cmd_ingerir)

    p = sub.add_parser("acompanhar", help="painel ao vivo do que está entrando")
    p.add_argument("--intervalo", type=int, default=5, help="segundos entre atualizações")
    p.add_argument("--extrair", action="store_true", help="extrai a cada ciclo")
    p.add_argument("--uma-vez", action="store_true", help="desenha uma vez e sai")
    p.add_argument("--provider")
    p.set_defaults(func=cmd_acompanhar)

    p = sub.add_parser("servir", help="recebe webhooks da Evolution API")
    p.add_argument("--porta", type=int)
    p.set_defaults(func=cmd_servir)

    p = sub.add_parser("painel", help="painel web de leitura (§13, antecipado)")
    p.add_argument("--porta", type=int)
    p.set_defaults(func=cmd_painel)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)
