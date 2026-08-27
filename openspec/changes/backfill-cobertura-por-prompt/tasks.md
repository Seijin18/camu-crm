# Tasks — backfill não reprocessa o que a versão de prompt atual já cobriu

## 1. Schema e acesso a dados

- [x] 1.1 `camucrm/db.py::SCHEMA`: tabela `cobertura_extracao (conversa_id,
      prompt_versao, ultima_mensagem_id, atualizado_em)`, PK
      `(conversa_id, prompt_versao)`, `ON DELETE CASCADE` em `conversa_id`
      (→ Requirement "Cobertura é rastreada por versão de prompt").
- [x] 1.2 `Database.cobertura_extracao(conversa_id, prompt_versao) -> int |
      None` (→ Requirement "Cobertura é rastreada por versão de prompt").
- [x] 1.3 `Database.registrar_cobertura_extracao(conversa_id, prompt_versao,
      ultima_mensagem_id, conn=None)`: `INSERT ... ON CONFLICT DO UPDATE`
      com `GREATEST`, mesmo padrão de `atualizar_estado_conversa` (→
      Requirement "Cobertura nunca regride sob processamento concorrente").
- [x] 1.4 `tests/fakes.py::FakeDatabase`: espelhar as duas operações acima
      (dict `(conversa_id, prompt_versao) -> ultima_mensagem_id`, com o
      mesmo `max()` defensivo que `atualizar_estado_conversa` já usa para
      `ultima_mensagem_processada_id`).

## 2. Extrator

- [x] 2.1 `camucrm/extraction/extractor.py::processar_conversa`: parâmetro
      `somente_desatualizados: bool = False`. Com `forcar=True` e
      `somente_desatualizados=True`, consultar
      `db.cobertura_extracao(conversa_id, prompt_mod.PROMPT_VERSAO)` e usar
      como `desde`/gatilho de `estagio_referencia` conforme `design.md` (→
      Requirement "Backfill não relê o que a versão de prompt atual já
      cobriu").
- [x] 2.2 Sem cobertura para a versão atual (`somente_desatualizados=True`
      mas `cobertura is None`), preservar o comportamento de hoje sem
      alteração: releitura total, `estagio_referencia =
      estagio_inicial(funil)` (→ Requirement "Primeira passada de uma
      versão de prompt nova sempre relê tudo").
- [x] 2.3 Todo bloco persistido com sucesso (caminho ao vivo E forçado)
      grava `registrar_cobertura_extracao(conversa_id, PROMPT_VERSAO,
      ultima_id)` (→ Requirement "Extração ao vivo alimenta a mesma
      cobertura que o backfill consulta").

## 3. Backfill e CLI

- [x] 3.1 `camucrm/backfill.py::extrair_historico`: parâmetro
      `somente_desatualizados: bool = True`, repassado a
      `processar_conversa` (→ Requirement "Backfill não relê o que a
      versão de prompt atual já cobriu").
- [x] 3.2 `camucrm/cli.py::cmd_backfill`: flag `--forcar-tudo` (passa
      `somente_desatualizados=False`); sem a flag, comportamento novo
      (barato) é o padrão (→ Requirement "Backfill não relê o que a versão
      de prompt atual já cobriu").
- [x] 3.3 `camucrm/cli.py::cmd_extrair`: flag `--somente-desatualizados`,
      opcional, default `False` — `camucrm extrair --conversa X --forcar`
      sem a flag continua com o comportamento de hoje, sem mudança (→
      Requirement "Ação de operador em uma conversa não muda de
      comportamento por padrão").

## 4. Testes

- [x] 4.1 `tests/test_backfill.py`: `extrair_historico` chamado duas vezes
      seguidas, mesma versão de prompt, sem mensagem nova entre as duas —
      a segunda chamada não gera nenhuma chamada de LLM (`FakeLlm.chamadas`
      vazio na segunda rodada) (→ Requirement "Backfill não relê o que a
      versão de prompt atual já cobriu").
- [x] 4.2 `tests/test_backfill.py`: bump de `PROMPT_VERSAO` (monkeypatch)
      entre duas execuções força releitura total na segunda, mesmo com
      cobertura da versão anterior (→ Requirement "Primeira passada de uma
      versão de prompt nova sempre relê tudo").
- [x] 4.3 `tests/test_backfill.py`: `somente_desatualizados=False` (via
      `--forcar-tudo` ou direto) sempre relê, com ou sem cobertura
      existente (→ Requirement "Reprocessamento total continua disponível
      como opção explícita").
- [x] 4.4 `tests/test_backfill.py`: conversa extraída ao vivo até a
      mensagem N sob a versão atual, depois submetida a
      `extrair_historico(somente_desatualizados=True)` sem mensagem nova —
      zero chamadas de LLM (→ Requirement "Extração ao vivo alimenta a
      mesma cobertura que o backfill consulta").
- [x] 4.5 `tests/test_backfill.py`: objeção não duplica ao combinar bump de
      versão + reexecução (regressão sobre `backfill-seguro-para-
      reexecucao`).
- [x] 4.6 `tests/test_e2e.py`: estende o ciclo único com o cenário
      "backfill reexecutado sob a mesma versão de prompt não chama o LLM
      de novo" (não duplica um E2E paralelo).
- [x] 4.7 Suíte completa verde (`make test`).
