## Context

§1 e `CLAUDE.md` fixam "o LLM aparece em exatamente dois lugares". Este
change introduz um terceiro (`camucrm/summaries.py`) — uma tensão real com o
documento, que precisa ser justificada por escrito, não silenciada.

## Decisão: summaries.py é módulo de topo, não dentro de painel/

Critério que separa "terceira superfície aceitável" de "arquitetura
vazando": `extraction/` alimenta `fatos`, que alimenta as regras de
`rules/` — um erro ali é corretude estrutural, produz sistematicamente um
estágio errado. `drafts.py` e `summaries.py` são terminais: a saída não
retroalimenta `fatos` nem nenhuma regra; um erro custa uma leitura ruim para
um humano, nunca um estágio errado no banco.

A regra "`rules/` não importa `llm`/`drafts`/`summaries`/`extraction`"
continua íntegra e ganha teste por `ast.parse` — não por grep, que
falso-positiva em docstring (menciona o nome do módulo em texto) e
falso-negativa em import com alias (`import summaries as s`).

## Decisão: resumos_conversa é folha do grafo

Nenhuma regra lê `resumos_conversa`. Apagar a tabela inteira não muda
`(estágio, temperatura, fila)` de nenhuma conversa. Isso é verificado por
teste de comportamento (`test_resumo_nao_muda_estado`), não só deixado como
convenção de código — convenção sem teste é a mesma categoria de risco que
o invariante 4 do `CLAUDE.md` já nomeia para o filtro de backfill.

## Decisão: importadores de summaries são um conjunto fechado

`{camucrm.painel.api, camucrm.cli}` são os únicos módulos autorizados a
importar `camucrm.summaries`. Um importador novo fora desse conjunto DEVE
forçar uma decisão explícita (editar o teste de guarda e justificar), não
vazar silenciosamente para outro módulo do domínio.

Registrado em três lugares, porque nenhum um sozinho basta: `CLAUDE.md`
(a regra formal do projeto), `openspec/project.md` (a decisão que diverge do
documento), e o docstring de `summaries.py` (o lugar que quem for editar o
módulo realmente vai ler).

## Alternativa descartada

Resumo dentro de `camucrm/painel/views.py` — rejeitada porque colocaria I/O
de LLM dentro do módulo que o resto do painel trata como puro e sem rede
(`views.py` hoje é só funções que montam dicts a partir de dados já lidos).
Misturar isso quebraria a garantia que os testes de `painel-leitura` já
verificam sobre `views.py`.
