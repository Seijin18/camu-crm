# Resumo de conversa por LLM, sob demanda

## Why

Não existe avaliação de conversa persistida em lugar nenhum;
`Classificacao.sinal` é gerado e descartado. Ler evidência, linha do tempo,
objeções e correções hoje exige ler tabela por tabela. Um resumo por LLM
gerado só ao clicar (nunca automático) reduz esse custo de leitura sem virar
fonte de verdade — nenhuma regra pode depender de prosa gerada por modelo.

## What Changes

- `camucrm/summaries.py` novo, módulo de topo (não dentro de `painel/` —
  enfiar chamada de LLM num módulo de UI seria a arquitetura vazando sem
  ninguém decidir isso explicitamente). Terceira superfície de LLM, ao lado
  de `extraction/` e `drafts.py` — ver `design.md` para a justificativa da
  divergência com §1/`CLAUDE.md` ("exatamente dois lugares").
- Prompt determinístico, montado em Python: funil, estágio + label,
  temperatura + `Classificacao.sinal`, fatos com evidência literal, linha do
  tempo de `eventos_estagio` (linhas de `origem='backfill'` rotuladas
  "momento reconstruído, não confiável (§8)"), objeções com trecho e
  estágio, correções já feitas, follow-ups enviados e teto restante, últimas
  30 mensagens.
- Devolve JSON estrito `{"resumo", "proximo_passo"}` — resumo de 3 a 5
  linhas em terceira pessoa, tom de passagem de bastão; próximo passo em uma
  frase.
- Proibições verificáveis por código, não só por instrução de prompt:
  - Não afirmar estágio, temperatura ou prioridade — `validar_resumo`
    rejeita qualquer token de `TODOS_ESTAGIOS`/`TEMPERATURAS` no texto.
  - Não citar preço — reusa `_PRECO` de `drafts.py`, não duplica a
    constante.
  - Não passar de 5 linhas.
  - Uma retentativa com o motivo da rejeição devolvido ao modelo (copia o
    padrão já usado em `drafts.gerar`).
- `PROMPT_VERSAO_RESUMO = "1"`.
- Tabela `resumos_conversa` nova: `resumo`, `proximo_passo`,
  `ultima_mensagem_id` (fronteira do que o resumo viu — staleness é medida
  em contagem de mensagens acima deste id, não diferença de timestamp),
  estágio, temperatura, `prompt_versao`, modelo, `gerado_em`, `gerado_por`.
  Índice único em `(conversa_id, coalesce(ultima_mensagem_id, 0),
  prompt_versao)` — clicar "gerar" duas vezes sem mensagem nova não duplica
  linha; a checagem de cache acontece antes da chamada ao LLM;
  `?forcar=true` usa `ON CONFLICT ... DO UPDATE`.
- Extensão de `purgar_mensagens_antigas`: remove `resumo` e `proximo_passo`
  de `resumos_conversa` (§12 — prosa derivada carrega conteúdo pessoal).
- `resumos_conversa` é folha do grafo: nenhuma regra a lê. Apagar a tabela
  inteira não muda estágio, temperatura ou fila de nenhuma conversa — ver
  `design.md`.
- `POST /api/resumos` no painel — gera, nunca automático; botão
  "gerar"/"regerar" explícito na tela.
- **Edição de `CLAUDE.md`** (fora de `openspec/`, feita por quem executa
  este change, não por este agente de planejamento): "exatamente dois
  lugares" passa a três, nomeando a propriedade que preserva §1 —
  `extraction/` alimenta `fatos` que alimenta as regras (corretude
  estrutural); `drafts.py` e `summaries.py` são terminais (erro custa uma
  leitura ruim, nunca um estágio errado); a regra original permanece
  íntegra: se `rules/` importar `llm`, a arquitetura vazou.
- **Edição de `openspec/project.md`**: ver seção correspondente no plano
  geral — duas entradas novas em "Decisões que divergem", nota sobre
  `resumos_conversa` como folha do grafo.

## Impact

- Specs afetadas: `resumo-conversa` (nova)
- Código alterado: `camucrm/summaries.py` (novo), `camucrm/db.py` (`SCHEMA`
  — `resumos_conversa`, extensão de `purgar_mensagens_antigas`),
  `camucrm/painel/api.py` (`POST /api/resumos`), `camucrm/painel/views.py`,
  `camucrm/painel/static/*` (bloco de resumo, ordem de tela), `CLAUDE.md`
  (fora de `openspec/`), `openspec/project.md`
- Testes alterados: `tests/test_summaries.py` (novo), `tests/integration/`
  (índice único de `resumos_conversa`), `tests/test_e2e.py` (estendido com
  `test_resumo_nao_muda_estado`)
- Bloqueado por: `painel-leitura`
- Bloqueia: —

## Fora de escopo (decisão explícita)

- Geração automática de resumo — só ao clicar.
- Qualquer regra lendo `resumos_conversa`.
- `make eval` para este prompt — não há ground truth para prosa; a regra do
  `CLAUDE.md` de rodar o eval a cada mudança de prompt não se aplica aqui, e
  isso fica explícito no docstring de `summaries.py` para não ser cobrado por
  analogia ao lugar errado.
