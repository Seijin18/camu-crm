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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, metrics
from .backfill import extrair_historico, importar_conversas
from .db import Database, MARCOS_MANUAIS, TetoFollowupError
from .drafts import RascunhoInvalidoError, gerar as gerar_rascunho
from .extraction import FATOS_BOOLEANOS
from .extraction.extractor import Extrator
from .llm import LlmIndisponivelError, criar_llm
from .pipeline import carregar_sinais, recalcular, recalcular_todas
from .rules.fila import Candidato, formatar_fila, montar_fila
from .rules.temperatura import classificar
from .taxonomia import FILA_TAMANHO_MAXIMO, estagio_label
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
    quem = _operador(args)
    banco = _db()
    banco.registrar_marco(args.conversa, args.marco, por=quem)
    conversa = banco.get_conversa(args.conversa)
    if conversa is None:
        raise SystemExit(f"conversa {args.conversa} não existe")
    if args.marco == "perdido":
        banco.atualizar_estado_conversa(conversa.id, resultado="perdido")
    elif args.marco == "ganho":
        banco.atualizar_estado_conversa(conversa.id, resultado="ganho")
    estado = recalcular(banco, conversa)
    print(f"#{conversa.id}: marco `{args.marco}` por {quem} -> estágio {estado.estagio}")
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
    """Lê um payload de webhook do stdin e grava a mensagem."""
    banco = _db()
    transporte = criar_transporte(args.transporte)
    payload = json.loads(sys.stdin.read())
    evento = transporte.receber(payload)
    if evento is None:
        print("Evento ignorado (não é mensagem de conversa).")
        return 0
    contato = banco.upsert_contato(evento.telefone, nome=evento.nome, origem=transporte.nome)
    conversa = banco.get_or_create_conversa(contato.id)
    inserida = banco.registrar_mensagem(
        conversa.id, evento.direcao, evento.texto, evento.enviada_em,
        externa_id=evento.externa_id,
    )
    if inserida is None:
        print(f"Mensagem já conhecida (externa_id={evento.externa_id}); nada mudou.")
        return 0
    estado = recalcular(banco, banco.get_conversa(conversa.id))
    print(f"#{conversa.id} {contato.label}: {estado.estagio}, {estado.temperatura.upper()}")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)
