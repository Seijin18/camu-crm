# Tasks — extração em lote por janela (não uma chamada de LLM por mensagem)

## 1. Acesso a dados

- [x] 1.1 `camucrm/db.py::Database.primeira_mensagem_pendente_em(conversa_id,
      desde_id) -> datetime | None` — `MIN(enviada_em)` das mensagens com
      `id > desde_id` (→ Requirement "Gatilho híbrido decide quando
      extrair imediatamente").
- [x] 1.2 `tests/fakes.py::FakeDatabase`: espelhar o método acima.

## 2. Gatilho híbrido no webhook

- [x] 2.1 `camucrm/webhook.py`: `ENV_LIMIAR_MENSAGENS`/
      `ENV_TETO_ESPERA_MINUTOS` + funções `limiar_mensagens()`/
      `teto_espera_minutos()` lendo de ambiente, mesma convenção de
      `extrair_ao_receber()` (→ Requirement "Limiares são configuráveis
      por ambiente").
- [x] 2.2 `camucrm/webhook.py::_deve_extrair_agora(db, conversa, *, agora)`:
      `True` se `mensagens_desde >= limiar_mensagens()` OU se a mensagem
      pendente mais antiga já espera `>= teto_espera_minutos()`; `False`
      quando não há mensagem pendente (→ Requirement "Gatilho híbrido
      decide quando extrair imediatamente").
- [x] 2.3 `camucrm/webhook.py::_extrair`: consulta `_deve_extrair_agora`
      antes de chamar `extrator.processar_conversa` — abaixo dos limiares,
      retorna sem chamar o LLM, sem avançar
      `ultima_mensagem_processada_id` (→ Requirement "Abaixo dos limiares,
      a extração fica pendente para o cron").
- [x] 2.4 Atualizar `tests/test_webhook.py::TesteExtracaoAoReceber
      ::test_falha_na_extracao_nao_propaga` para configurar o mock do novo
      gatilho (força `_deve_extrair_agora` a `True`), preservando a
      garantia original (extração que quebra não desfaz a mensagem).

## 3. Botão manual no painel

- [x] 3.1 `camucrm/painel/api.py`: `POST /conversas/{id}/extrair` —
      dispara `Extrator.processar_conversa` incondicionalmente (sem
      gatilho), mesmo padrão de erro/autenticação de `/rascunho` e
      `/resumo` (→ Requirement "Operador pode forçar extração imediata
      pelo painel").
- [x] 3.2 `camucrm/painel/static/app.js` (+ `views.py` se necessário):
      botão "Extrair agora" na conversa, mesmo padrão visual do botão
      "Gerar resumo".

## 4. Testes

- [x] 4.1 `tests/test_webhook.py`: `mensagens_desde >= limiar` dispara
      mesmo com a mensagem pendente recém-chegada (→ Requirement "Gatilho
      híbrido decide quando extrair imediatamente").
- [x] 4.2 `tests/test_webhook.py`: mensagem pendente mais antiga além do
      teto de espera dispara mesmo com poucas mensagens pendentes (→
      Requirement "Gatilho híbrido decide quando extrair imediatamente").
- [x] 4.3 `tests/test_webhook.py`: abaixo dos dois limiares, `_extrair` não
      chama `processar_conversa` (→ Requirement "Abaixo dos limiares, a
      extração fica pendente para o cron").
- [x] 4.4 `tests/test_webhook.py`: limiares respeitam override por
      variável de ambiente (→ Requirement "Limiares são configuráveis por
      ambiente").
- [x] 4.5 `tests/test_painel_api.py`: `POST /conversas/{id}/extrair`
      extrai incondicionalmente, mesmo abaixo dos limiares que bloqueariam
      o caminho do webhook (→ Requirement "Operador pode forçar extração
      imediata pelo painel").
- [x] 4.6 Suíte completa verde (`make test`).
