# Ground truth no painel

## Why

Pedido do usuário, em resposta direta a "como rotular uma conversa à mão":
em vez de editar `data/eval/conversas.jsonl` num editor de texto, rotular
pelo próprio painel — e uma vez que as 30 conversas estiverem completas e
válidas, o sistema desbloqueia o que hoje está condicionado a isso (a
restrição já registrada em `project.md`: "a tela `/funciona` fica proibida
de afirmar qualquer coisa sobre acurácia de extração até `ground-truth-
marcos` entrar").

Este change é a implementação real do candidato `ground-truth-marcos` que já
constava em `openspec/project.md` — deixa de ser só um item da lista de
pendências e vira um change com nome e desenho próprios.
`ground-truth-no-painel` substitui esse candidato: a linha correspondente em
`project.md` é reescrita para apontar para este change em vez de descrevê-lo
como pendência externa manual.

A integração que faz a diferença: em vez de o operador digitar as mensagens
à mão (o exemplo do README sugere isso, mas é o passo mais trabalhoso), o
painel deixa escolher uma conversa REAL já existente no CRM —
`db.listar_mensagens_registradas(conversa_id)` preenche `mensagens[]`
automaticamente — e a pessoa só precisa preencher os campos de julgamento
(`estagio_final`, `objecao`, os 7 `fatos`, `marcos`, `nota`), lendo a
transcrição que já está na tela.

## What Changes

- `camucrm/evaluation/dataset.py`: extrair a validação de uma entrada (hoje
  só `_para_conversa`, privada e acoplada à leitura de arquivo) para uma
  função reusável (`validar_entrada(bruto, onde) -> ConversaRotulada`) que
  tanto `carregar()` quanto as rotas novas do painel chamam — nenhuma
  reimplementação de regra de validação no painel (mesma disciplina de
  `db.py` ser o único lugar com SQL: aqui, `dataset.py` é o único lugar com
  validação de rótulo).
- `camucrm/config.py`: `CAMU_EVAL_DATASET` (env var nova, mesmo padrão de
  `CAMU_PLAYBOOK`) apontando para `data/eval/conversas.jsonl` por padrão —
  permite testes apontarem para um arquivo temporário sem tocar o real.
- `camucrm/painel/api.py` — rotas novas, todas sob `/api/eval`:
  - `GET /eval/status` — `carregar()` + `avisos_de_tamanho()`: contagem
    atual, `completo: bool` (`len >= TAMANHO_MINIMO`), lista resumida (id,
    funil, estagio_final, objecao, nota, nº de mensagens), avisos (ex.
    "menos de 5 com objeção").
  - `GET /eval/rotulos/{id}` — detalhe completo de uma entrada (mensagens +
    rótulo), para edição.
  - `POST /eval/rotulos` — cria uma entrada nova. Corpo aceita ou
    `conversa_id` (puxa mensagens reais do CRM via
    `db.listar_mensagens_registradas`) ou `mensagens[]` digitadas (fallback
    para histórico que não está mais no CRM), mais os campos de `rotulo`.
    Valida via `dataset.validar_entrada` antes de gravar — uma entrada
    malformada nunca chega a corromper o arquivo.
  - `PUT /eval/rotulos/{id}` — edita o rótulo de uma entrada existente
    (revalida).
  - `DELETE /eval/rotulos/{id}` — remove uma entrada (ex. escolheu a
    conversa errada).
  - `POST /eval/rodar` — roda `evaluation.rodar()` (chama o LLM contra o
    dataset inteiro) — gasta cota, então é `POST`, nunca `GET`, mesmo padrão
    de rascunho/resumo. Recusa com 422 se `len(dataset) < TAMANHO_MINIMO` —
    a restrição de `project.md` fica estrutural, não só prometida na
    documentação. Resultado (`RelatorioEval`) é cacheado em
    `data/eval/ultimo_resultado.json` (arquivo, não tabela — mesma
    fronteira file-based do dataset; `ResultadoConversa` não carrega texto
    de mensagem, só métricas, então cachear não amplia a superfície de dado
    pessoal).
  - `GET /eval/resultado` — lê o cache acima, se existir; devolve
    `disponivel: false` antes da primeira execução.
- Painel — aba nova "Ground truth (§7)": cabeçalho com progresso ("18/30
  rotuladas" ou "✓ 30/30 completo"), avisos de `avisos_de_tamanho()`
  visíveis; lista das entradas já rotuladas com editar/excluir; botão "rodar
  eval" (habilitado só com `completo: true`, mostra o último resultado
  cacheado com `rodado_em`). Na tela de detalhe de uma conversa
  (`#/conversas/{id}`), botão novo "Usar para ground truth (§7)" abre o
  formulário de rotulagem com a transcrição real pré-carregada (somente
  leitura) e os campos de julgamento em branco para preencher.
- `GET /api/o-que-funciona`: ganha o bloco "Acurácia de extração (§7)",
  populado só quando `GET /eval/resultado` tem cache disponível — mostra
  fatos/objeção/falsos-positivos contra as metas (`META_FATOS`,
  `META_OBJECAO`, `META_FALSOS_POSITIVOS`), com `rodado_em`. Sem cache
  disponível, mantém o texto de restrição que `project.md` já registra
  ("esta tela não afirma nada sobre acurácia de extração...").
- `openspec/project.md`: a linha do candidato `ground-truth-marcos` na
  seção "Próximos changes candidatos" é reescrita para apontar para este
  change.

## Impact

- Specs afetadas: `ground-truth-no-painel` (nova)
- Código alterado: `camucrm/evaluation/dataset.py` (`validar_entrada`
  extraída), `camucrm/config.py` (`CAMU_EVAL_DATASET`),
  `camucrm/painel/api.py` (rotas `/api/eval/*`), `camucrm/painel/views.py`,
  `camucrm/painel/static/*` (aba "Ground truth", botão "Usar para ground
  truth" no detalhe da conversa), `openspec/project.md`
- Testes alterados: `tests/test_eval_painel.py` (novo — criar entrada a
  partir de `conversa_id` puxa mensagens reais corretamente; validação
  rejeita estágio fora da taxonomia/objeção fora da lista/fato fora do
  contrato com o mesmo erro que `dataset.carregar` lançaria; editar
  preserva o `id`; excluir remove; `/eval/status` reporta `completo=false`
  abaixo de 30 e `true` a partir de 30; `/eval/rodar` recusa com 422 abaixo
  de 30 — teste estrutural, não só de UI; dataset fake de 30 entradas roda
  de ponta a ponta e popula o bloco novo de `/o-que-funciona`). Nenhum
  teste toca `data/eval/conversas.jsonl` real — todos usam
  `CAMU_EVAL_DATASET` apontando para um arquivo temporário.
- Bloqueado por: nenhum (pode entrar logo após
  `literalidade-e-idempotencia-da-extracao` — não depende de nenhuma das
  correções de recepção/painel; desbloquear a métrica de acurácia é o que
  dá sentido a rodar `make eval`/o eval pelo painel com confiança depois
  das correções de `_fold`/corpus por direção)
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- Migrar o dataset para Postgres — decisão explícita de manter em arquivo,
  ver `design.md`.
- Qualquer geração automática de rótulo por LLM — rotulagem é sempre
  julgamento humano; o painel só facilita a coleta, nunca sugere ou
  pré-preenche os campos de julgamento a partir de inferência automática.
