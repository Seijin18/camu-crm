# Tasks — resumo de conversa por LLM, sob demanda

## 1. Implementação — módulo e prompt

- [x] 1.1 `camucrm/summaries.py`: prompt determinístico montado em Python —
      funil, estágio+label, temperatura + `Classificacao.sinal`, fatos com
      evidência literal, linha do tempo de eventos (backfill rotulado
      "momento reconstruído, não confiável (§8)"), objeções, correções,
      follow-ups/teto, últimas 30 mensagens.
- [x] 1.2 `camucrm/summaries.py`: `gerar()` chama `llm`, devolve JSON
      `{"resumo", "proximo_passo"}`; `validar_resumo()` rejeita token de
      `TODOS_ESTAGIOS`/`TEMPERATURAS`, preço (reusar `_PRECO` de
      `drafts.py`), mais de 5 linhas; uma retentativa com o motivo (copiar
      padrão de `drafts.gerar`) (→ Requirement "Resumo nunca afirma estágio,
      temperatura ou preço").
- [x] 1.3 `camucrm/summaries.py`: `PROMPT_VERSAO_RESUMO = "1"`; docstring
      abrindo pela divergência de §1 e nomeando o teste que a sustenta;
      docstring também deixa explícito que este prompt não exige `make
      eval` (sem ground truth para prosa).

## 2. Implementação — schema e purga

- [x] 2.1 `camucrm/db.py`: tabela `resumos_conversa` em `SCHEMA` — colunas
      descritas no `proposal.md`; índice único em `(conversa_id,
      coalesce(ultima_mensagem_id, 0), prompt_versao)` (→ Requirement
      "Cache por versão de prompt e mensagem").
- [x] 2.2 `camucrm/db.py`: leitura/escrita de resumo — checagem de cache
      antes da chamada ao LLM; `?forcar=true` usa `ON CONFLICT ... DO
      UPDATE` (→ Requirement "Cache por versão de prompt e mensagem").
- [x] 2.3 `camucrm/db.py`: estender `purgar_mensagens_antigas` (`db.py:878`)
      para apagar `resumo`/`proximo_passo` de `resumos_conversa` (→
      Requirement "Purga remove prosa do resumo").

## 3. Implementação — painel e docs

- [x] 3.1 `camucrm/painel/api.py`: **divergência registrada** — implementado
      como `POST /api/conversas/{id}/resumo` (+ `GET` para leitura pura do
      cache), não `POST /api/resumos` como o `proposal.md` original citava.
      Motivo: todo o resto do router segue o padrão `/conversas/{id}/<recurso>`
      (`/rascunho`, `/marcos`, `/funil`, `/correcoes`) — `/resumos` no plural
      solto quebraria essa convenção sem ganho, e o corpo `{id}` já identifica
      a conversa. Gera, nunca `GET`, nunca automático (→ Requirement "Geração
      só ao clicar, nunca automática").
- [x] 3.2 `camucrm/painel/static/*`: ordem de tela — cabeçalho, fatos com
      evidência, linha do tempo, objeções, correções, follow-ups, e só então
      o bloco de resumo (staleness "há N mensagens", botão Regerar); telas
      1-6 idênticas sem LLM configurado, bloco 7 diz "não gerado".
- [x] 3.3 `CLAUDE.md`: editar "exatamente dois lugares" para três, com a
      justificativa da folha (fora de `openspec/` — executor do change
      edita, não este agente de planejamento).

## 4. Testes

- [x] 4.1 `tests/test_summaries.py`: prompt contém evidência literal e
      linha do tempo; `validar_resumo` rejeita token de
      estágio/temperatura/preço/mais de 5 linhas; retentativa exatamente uma
      vez; LLM indisponível → resposta com bloco determinístico e `resumo:
      null`, nunca 500 (→ Requirement "Resumo nunca afirma estágio,
      temperatura ou preço"; → Requirement "Falha de LLM não derruba a
      tela").
- [x] 4.2 `tests/test_summaries.py`: guardas por `ast.parse` — `rules/` não
      importa `llm`/`drafts`/`summaries`/`extraction`; importadores de
      `summaries` são conjunto fechado `{painel.api, cli}` (→ Requirement
      "Importadores de summaries são um conjunto fechado").
- [x] 4.3 `tests/integration/`: índice único de `resumos_conversa` torna a
      2ª geração sem mensagem nova um no-op; purga remove resumo (→
      Requirement "Cache por versão de prompt e mensagem"; → Requirement
      "Purga remove prosa do resumo").
- [x] 4.4 `tests/test_e2e.py` (estender): `test_resumo_nao_muda_estado` —
      `(estagio, temperatura, fila)` idênticos antes e depois de gerar
      resumo (→ Requirement "Resumo é folha do grafo").
- [x] 4.5 Suíte completa verde.
