# Tasks — prospecção em tempo real, sem pular pro topo

- [x] 1.1 `db.token_de_mudanca`: 4ª parte `epoch(MAX(prospeccoes.atualizado_em))`
      — token `"m:e:c:p"`; docstring explica o recorte por parte
- [x] 1.2 `atualizado_em = now()` em `marcar_prospeccao_aberta`,
      `registrar_envio_prospeccao` (sucesso e falha),
      `marcar_prospeccao_enviada_manual` (marcar e desfazer),
      `marcar_prospeccao_nao_whatsapp` (marcar e desfazer)
- [x] 1.3 Comentário no schema de `prospeccoes.atualizado_em` registrando o
      novo uso; `stream.py` docstring ("token de 3 partes" → 4)
- [x] 2.1 `app.js`: `rotaRefleteConversas()` / `rotaEhListaProspeccao()`
- [x] 2.2 `app.js`: `processarBlocoSse` parseia o `data:` JSON;
      `reagirAMudanca(token)` compara parte a parte
- [x] 2.3 `app.js`: `refreshSuaveAtual` — zerado em `renderizarRota`,
      preenchido com `carregar` em `renderizarProspeccao`
- [x] 3.1 `tests/fakes.py`: `_toques_prospeccao` + 4ª parte no
      `token_de_mudanca` fake + bump nas 4 mutações e no import
- [x] 3.2 `tests/test_painel_stream.py`: marca de prospecção move só a 4ª
      parte; mensagem nova não move a 4ª parte
- [x] 4.1 `openspec/project.md`: registrar a extensão
- [x] 4.2 `make test` verde (as 2 falhas pré-existentes de path no Windows
      em `test_eval_painel` continuam, não relacionadas)
