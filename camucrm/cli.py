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
from typing import Any

from . import acoes, config, metrics
from .acoes import AcaoInvalidaError
from .backfill import extrair_historico, importar_conversas
from .db import Database, MARCOS_MANUAIS, RETENCAO_EVENTOS_BRUTOS_DIAS, TetoFollowupError
from .drafts import RascunhoInvalidoError, gerar as gerar_rascunho
from .extraction import FATOS_BOOLEANOS
from .ingest import ingerir
from .extraction.extractor import Extrator
from .llm import LlmIndisponivelError, criar_llm
from .pipeline import recalcular, recalcular_lote, recalcular_todas
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
    """A fila do dia. O comando que precisa ser rodado toda manhã.

    Change `contatos-de-teste-isolados`: exclui contato de teste por
    padrão. `--incluir-teste` mostra os dois juntos (depuração via
    terminal); `--somente-teste` mostra só teste. Nunca as duas juntas — a
    CLI recusa antes de tocar no banco (`_condicao_teste`, chamado por
    `db.listar_conversas_abertas`, levanta `ValueError`).
    """
    banco = _db()
    if args.incluir_teste and args.somente_teste:
        raise SystemExit("--incluir-teste e --somente-teste não podem ser usados juntos")
    agora = datetime.now(timezone.utc)
    conversas = banco.listar_conversas_abertas(
        incluir_teste=args.incluir_teste, apenas_teste=args.somente_teste
    )
    # `recalcular_lote` (otimização de 2026-08-28): mesmo resultado de
    # `recalcular` chamado uma conversa de cada vez, com uma fração das idas
    # ao banco — ver `Database.contexto_para_recalculo`. Este é o comando
    # que "precisa ser rodado toda manhã" (docstring acima); contra um banco
    # remoto o padrão antigo levava dezenas de segundos.
    estados = recalcular_lote(banco, conversas, agora=agora, persistir=not args.simular)
    candidatos = [
        Candidato(
            conversa_id=conversa.id,
            nome=conversa.nome_contato or f"#{conversa.id}",
            funil=conversa.funil,
            estagio=estado.estagio,
            classificacao=estado.classificacao,
            sinais=estado.sinais,
        )
        for conversa, estado in zip(conversas, estados)
    ]
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
        resultados = [
            extrator.processar_conversa(
                args.conversa,
                forcar=args.forcar,
                somente_desatualizados=args.somente_desatualizados,
            )
        ]
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
    """Envia um texto por um contato. Exige nome de quem aprovou (§1, §10).

    `--rascunho <id> --opcao {1,2}` (change `rascunho-registrado`, design.md
    caminho 1 — o mais confiável dos três caminhos de vínculo) diz
    explicitamente qual rascunho gerou este envio: depois que
    `registrar_mensagem` devolve o id, `db.vincular_rascunho` grava o
    vínculo, e a escolha (opção 1 ou 2) é registrada junto, se a linha ainda
    não tinha escolha (não sobrescreve uma escolha manual já feita).
    """
    quem = _operador(args)
    banco = _db()
    conversa = banco.get_conversa(args.conversa)
    if conversa is None:
        raise SystemExit(f"conversa {args.conversa} não existe")

    rascunho = None
    if args.rascunho is not None or args.opcao is not None:
        if args.rascunho is None or args.opcao is None:
            raise SystemExit("--rascunho e --opcao só fazem sentido juntos")
        rascunho = banco.rascunho(args.rascunho)
        if rascunho is None or rascunho.conversa_id != conversa.id:
            # Erro claro, não silencioso (CLAUDE.md/instrução do change): um
            # id de rascunho de outra conversa não deve vincular nada.
            raise SystemExit(
                f"rascunho {args.rascunho} não existe ou não pertence à "
                f"conversa {conversa.id}"
            )

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
        mensagem_id = banco.registrar_mensagem(
            conversa.id, "out", args.texto, externa_id=resultado.externa_id
        )
        print(f"Enviado por {transporte.nome} (aprovado por {quem}).")
        if rascunho is not None and mensagem_id is not None:
            banco.vincular_rascunho(
                rascunho.id, mensagem_id, estagio_no_envio=conversa.estagio
            )
            if rascunho.escolhida is None and rascunho.texto_final is None:
                banco.registrar_escolha_rascunho(
                    rascunho.id, escolhida=args.opcao, por=quem
                )
            print(f"Rascunho #{rascunho.id} vinculado à mensagem #{mensagem_id}.")
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


def cmd_marcar_teste(args) -> int:
    """Marca/desmarca um contato como teste (change
    `contatos-de-teste-isolados`).

    Comando dedicado, não reaproveita `camucrm corrigir` — "teste" não é
    correção de classificação de negócio (§7), é uma flag operacional
    distinta que tira o contato do kanban/fila/métricas reais por padrão.
    Marcação é sempre manual (§1, mesmo princípio estendido).
    """
    quem = _operador(args)
    banco = _db()
    e_teste = not args.desfazer
    try:
        banco.marcar_contato_teste(args.contato, e_teste, por=quem)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    acao = "desmarcado como" if args.desfazer else "marcado como"
    print(f"Contato {args.contato} {acao} teste (por {quem}).")
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


def cmd_desconsiderar_recusa(args) -> int:
    """Desconsidera um `recusa_explicita` falso positivo (design.md, change
    `estagio-reabertura-manual-e-relogio`).

    Comando dedicado, não reaproveita `camucrm corrigir`: desconsiderar uma
    recusa não é "trocar um valor de campo" — é uma decisão com efeito
    estrutural sobre a máquina de estados (permite avanço hoje proibido).
    Nomear o comando explicitamente deixa essa intenção visível no
    histórico, em vez de parecer uma correção de rotina qualquer.

    O fato `recusa_explicita=true` continua gravado em `fatos`, íntegro —
    só a interpretação da regra de estágio muda a partir daqui
    (`acoes.desconsiderar_recusa`), sempre com `por` identificado (§7).
    """
    quem = _operador(args)
    banco = _db()
    try:
        estado = acoes.desconsiderar_recusa(banco, args.conversa, por=quem)
    except AcaoInvalidaError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"#{args.conversa}: recusa_explicita desconsiderada por {quem} "
        f"-> estágio {estado.estagio}"
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
        resumo, _ = extrair_historico(
            banco, extrator, somente_desatualizados=not args.forcar_tudo
        )
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
    # Change `ingestao-a-prova-de-falha`, design.md: job separado (não o
    # mesmo SQL), mas reaproveita o mesmo comando de retenção para o
    # operador não precisar lembrar de dois comandos. Só `processado=TRUE`
    # sai; falha pendente nunca é apagada automaticamente.
    eventos_apagados = banco.purgar_eventos_brutos_antigos(args.dias_eventos_brutos)
    print(
        f"{eventos_apagados} evento(s) bruto(s) já processado(s) "
        f"descartado(s) (retenção de {args.dias_eventos_brutos} dias; "
        "falhas pendentes nunca são apagadas automaticamente)."
    )
    return 0


def cmd_reprocessar_falhas(args) -> int:
    """Reprocessa payloads que falharam na ingestão (change
    `ingestao-a-prova-de-falha`).

    Lê `eventos_recebidos_bruto` com `processado=False` e tenta reingerir
    cada um pelo MESMO caminho do webhook (`camucrm.ingest.ingerir`) — nunca
    duplica mensagem já gravada com sucesso porque o dedup por `externa_id`
    (ou seu hash sintético, quando ausente) é o mesmo de sempre. Sempre
    manual: um cron automático reprocessando silenciosamente esconderia
    exatamente o sinal que este comando existe para expor (design.md).
    """
    banco = _db()
    pendentes = banco.listar_eventos_brutos_pendentes(limite=args.limite)
    if not pendentes:
        print("Nenhum evento pendente de reprocessamento.")
        return 0

    transporte = criar_transporte("evolution", para_envio=False)
    sucesso = 0
    falha = 0
    for evento in pendentes:
        try:
            resultado = ingerir(banco, transporte.receber(evento.payload), origem="whatsapp")
        except Exception as exc:  # noqa: BLE001 - um evento ruim não pode parar os demais
            banco.marcar_evento_bruto_falhou(evento.id, str(exc))
            falha += 1
            print(f"  #{evento.id}: FALHOU DE NOVO — {exc}")
            continue
        banco.marcar_evento_bruto_processado(evento.id)
        sucesso += 1
        print(f"  #{evento.id}: reprocessado — {resultado}")

    print(f"{sucesso} reprocessado(s) com sucesso, {falha} ainda falhando.")
    return 0 if falha == 0 else 1


def _payload_parece_evolution(payload: Any) -> bool:
    """Heurística: o payload tem a forma de um evento `messages.upsert` da
    Evolution API (envelope `data.key`), mesmo que o `--transporte` efetivo
    não seja `evolution`. Usada só para diferenciar a SAÍDA de `cmd_ingerir`
    (change `ingestao-a-prova-de-falha`) — nunca para decidir se o evento é
    ingerido de verdade, o que continua sendo decisão só do transporte
    resolvido.
    """
    if not isinstance(payload, dict):
        return False
    dados = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return isinstance(dados, dict) and isinstance(dados.get("key"), dict)


def cmd_ingerir(args) -> int:
    """Lê um payload de webhook do stdin e ingere.

    Mesmo caminho que o webhook usa (`camucrm.ingest`) — dois caminhos de
    entrada acabariam divergindo. Por isso o `--transporte` tem o mesmo
    padrão do webhook (`evolution`, só recepção — `para_envio=False`): antes
    deste change o padrão herdado de `criar_transporte` era `console`, e um
    operador que esquecesse a flag via a MESMA saída "evento ignorado" de um
    evento benigno real — sem nenhum aviso de que a configuração divergiu do
    caminho do webhook (spec.md, "cmd_ingerir não finge sucesso silencioso").

    `--transporte` continua aceito (para depurar com fixtures de
    `ConsoleTransporte`, por exemplo); quando o valor escolhido não é
    `evolution` e o payload tem cara de evento real da Evolution API, a
    saída avisa que o motivo do "ignorado" é configuração, não um evento
    benigno de verdade.
    """
    banco = _db()
    nome_transporte = args.transporte or "evolution"
    transporte = criar_transporte(nome_transporte, para_envio=False)
    payload = json.loads(sys.stdin.read())
    # Change `ingestao-restrita-por-instancia`: mesmo parâmetro que o
    # webhook passa (`payload.get("instance")`) — os dois caminhos nunca
    # podem divergir. `--instancia` também aceita override manual, útil
    # pra testar um payload sem o campo `instance` de verdade.
    instancia = args.instancia or (
        payload.get("instance") if isinstance(payload, dict) else None
    )
    resultado = ingerir(
        banco, transporte.receber(payload), origem="whatsapp", instancia=instancia
    )
    if (
        resultado.ignorada
        and transporte.nome != "evolution"
        and _payload_parece_evolution(payload)
    ):
        print(
            f"AVISO: payload parece um evento real da Evolution API, mas "
            f"--transporte={transporte.nome!r} não sabe interpretá-lo — "
            "ignorado por CONFIGURAÇÃO divergente do webhook (use "
            "`--transporte evolution`, o padrão), não porque o evento seja "
            "benigno."
        )
        return 1
    print(resultado)
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
    p.add_argument(
        "--incluir-teste",
        action="store_true",
        dest="incluir_teste",
        help="mostra contato de teste junto com os reais (depuração via terminal)",
    )
    p.add_argument(
        "--somente-teste",
        action="store_true",
        dest="somente_teste",
        help="mostra só contato de teste",
    )
    p.set_defaults(func=cmd_fila)

    p = sub.add_parser("extrair", help="roda a extração sobre o bloco novo")
    p.add_argument("--conversa", type=int)
    p.add_argument("--forcar", action="store_true", help="reprocessa do início")
    p.add_argument(
        "--somente-desatualizados",
        action="store_true",
        help=(
            "com --forcar: só relê de fato se a versão de prompt atual ainda "
            "não cobrir a conversa inteira (change backfill-cobertura-por-prompt)"
        ),
    )
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
    p.add_argument("--rascunho", type=int, help="id do rascunho usado (change rascunho-registrado)")
    p.add_argument("--opcao", type=int, choices=[1, 2], help="qual opção do rascunho foi enviada")
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

    p = sub.add_parser(
        "marcar-teste", help="marca/desmarca um contato como teste (isolado do kanban/fila/métricas)"
    )
    p.add_argument("contato", type=int)
    p.add_argument("--desfazer", action="store_true", help="desmarca o contato como teste")
    p.add_argument("--por")
    p.set_defaults(func=cmd_marcar_teste)

    p = sub.add_parser("tipo", help="classifica a conversa como b2b ou b2c")
    p.add_argument("conversa", type=int)
    p.add_argument("tipo", choices=["b2b", "b2c"])
    p.add_argument("--por")
    p.set_defaults(func=cmd_tipo)

    p = sub.add_parser(
        "desconsiderar-recusa",
        help="desconsidera um recusa_explicita falso positivo (reabre no maior estágio já alcançado)",
    )
    p.add_argument("conversa", type=int)
    p.add_argument("--por")
    p.set_defaults(func=cmd_desconsiderar_recusa)

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
    p.add_argument(
        "--forcar-tudo",
        action="store_true",
        help=(
            "ignora a cobertura por versão de prompt e relê tudo "
            "incondicionalmente (change backfill-cobertura-por-prompt; "
            "sem a flag, conversas já cobertas pela versão atual não geram "
            "chamada de LLM)"
        ),
    )
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
    p.add_argument(
        "--dias-eventos-brutos",
        type=int,
        default=RETENCAO_EVENTOS_BRUTOS_DIAS,
        dest="dias_eventos_brutos",
        help="retenção da caixa de reprocessamento (só eventos já processados)",
    )
    p.set_defaults(func=cmd_purgar)

    p = sub.add_parser("ingerir", help="lê um webhook do stdin")
    p.add_argument(
        "--transporte",
        help="padrão: evolution, mesmo do webhook (ver requirement "
        "'cmd_ingerir não finge sucesso silencioso')",
    )
    p.add_argument(
        "--instancia",
        help="nome da instância de origem (change "
        "`ingestao-restrita-por-instancia`); padrão: campo 'instance' do "
        "próprio payload, mesmo que o webhook usa",
    )
    p.set_defaults(func=cmd_ingerir)

    p = sub.add_parser(
        "reprocessar-falhas",
        help="reprocessa eventos que falharam na ingestão (staging de webhook)",
    )
    p.add_argument("--limite", type=int, default=200)
    p.set_defaults(func=cmd_reprocessar_falhas)

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
