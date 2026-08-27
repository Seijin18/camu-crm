# Tasks — ground truth no painel

## 1. Implementação — validação reusável e config

- [x] 1.1 `camucrm/evaluation/dataset.py`: extrair `validar_entrada(bruto,
      onde) -> ConversaRotulada` de `_para_conversa`, reusada por
      `carregar()` e pelas rotas novas (→ Requirement "Validação de rótulo
      tem um único lugar de verdade").
- [x] 1.2 `camucrm/config.py`: `CAMU_EVAL_DATASET` apontando para
      `data/eval/conversas.jsonl` por padrão (→ Requirement "Testes nunca
      tocam o dataset real").

## 2. Implementação — rotas do painel

- [x] 2.1 `camucrm/painel/api.py::GET /eval/status`: `carregar()` +
      `avisos_de_tamanho()` — contagem, `completo`, lista resumida, avisos
      (→ Requirement "Status do dataset reflete completude real").
- [x] 2.2 `camucrm/painel/api.py::GET /eval/rotulos/{id}`: detalhe completo
      de uma entrada (→ Requirement "Detalhe de entrada é editável").
- [x] 2.3 `camucrm/painel/api.py::POST /eval/rotulos`: cria entrada a partir
      de `conversa_id` (via `db.listar_mensagens_registradas`) ou
      `mensagens[]` digitadas; valida via `dataset.validar_entrada` antes de
      gravar (→ Requirement "Criar entrada a partir de conversa real puxa
      as mensagens"; → Requirement "Entrada malformada nunca corrompe o
      arquivo").
- [x] 2.4 `camucrm/painel/api.py::PUT /eval/rotulos/{id}`: edita rótulo
      existente, revalida, preserva o `id` (→ Requirement "Detalhe de
      entrada é editável").
- [x] 2.5 `camucrm/painel/api.py::DELETE /eval/rotulos/{id}`: remove entrada
      (→ Requirement "Detalhe de entrada é editável").
- [x] 2.6 `camucrm/painel/api.py::POST /eval/rodar`: roda
      `evaluation.rodar()`; recusa com 422 se `len(dataset) <
      TAMANHO_MINIMO`; cacheia resultado em
      `data/eval/ultimo_resultado.json` (→ Requirement "Rodar eval abaixo do
      tamanho mínimo é estruturalmente recusado").
- [x] 2.7 `camucrm/painel/api.py::GET /eval/resultado`: lê o cache; devolve
      `disponivel: false` antes da primeira execução (→ Requirement "Rodar
      eval abaixo do tamanho mínimo é estruturalmente recusado").

## 3. Implementação — painel e tela /o-que-funciona

- [x] 3.1 `camucrm/painel/static/*`: aba nova "Ground truth (§7)" —
      progresso, avisos, lista com editar/excluir, botão "rodar eval"
      habilitado só com `completo: true` (→ Requirement "Status do dataset
      reflete completude real").
- [x] 3.2 `camucrm/painel/static/*`: botão "Usar para ground truth (§7)" no
      detalhe da conversa, abrindo o formulário com transcrição real
      pré-carregada (somente leitura) e campos de julgamento em branco (→
      Requirement "Criar entrada a partir de conversa real puxa as
      mensagens").
- [x] 3.3 `GET /api/o-que-funciona`: bloco "Acurácia de extração (§7)"
      populado só quando há cache disponível; mantém o texto de restrição
      quando não há (→ Requirement "Tela /o-que-funciona só afirma acurácia
      com eval disponível").

## 4. Atualização de project.md

- [x] 4.1 `openspec/project.md`: reescrever a linha do candidato
      `ground-truth-marcos` para apontar para este change.

## 5. Testes

- [x] 5.1 `tests/test_eval_painel.py`: criar entrada a partir de
      `conversa_id` puxa as mensagens reais corretamente (→ Requirement
      "Criar entrada a partir de conversa real puxa as mensagens").
- [x] 5.2 `tests/test_eval_painel.py`: validação rejeita estágio fora da
      taxonomia/objeção fora da lista/fato fora do contrato com o mesmo
      erro que `dataset.carregar` lançaria (→ Requirement "Validação de
      rótulo tem um único lugar de verdade").
- [x] 5.3 `tests/test_eval_painel.py`: editar preserva o `id`; excluir
      remove a entrada (→ Requirement "Detalhe de entrada é editável").
- [x] 5.4 `tests/test_eval_painel.py`: `/eval/status` reporta
      `completo=false` abaixo de 30 e `true` a partir de 30 (→ Requirement
      "Status do dataset reflete completude real").
- [x] 5.5 `tests/test_eval_painel.py`: `/eval/rodar` recusa com 422 abaixo
      de 30 — teste estrutural, não só de UI (→ Requirement "Rodar eval
      abaixo do tamanho mínimo é estruturalmente recusado").
- [x] 5.6 `tests/test_eval_painel.py`: dataset fake de 30 entradas roda de
      ponta a ponta e popula o bloco novo de `/o-que-funciona` (→
      Requirement "Tela /o-que-funciona só afirma acurácia com eval
      disponível").
- [x] 5.7 Confirmar que nenhum teste toca `data/eval/conversas.jsonl`
      real — todos usam `CAMU_EVAL_DATASET` apontando para um arquivo
      temporário (→ Requirement "Testes nunca tocam o dataset real").
- [x] 5.8 Suíte completa verde (`make test`).
