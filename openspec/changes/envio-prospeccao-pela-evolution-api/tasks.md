# Tasks — envio de prospecção pela Evolution API

- [x] 1.1 Atualizar `docs/mensagem-prospeccao.md` com o texto pedido
- [x] 2.1 Schema: `enviado_em`/`enviado_por`/`enviado_erro` em `prospeccoes`
      (`camucrm/db.py::SCHEMA`)
- [x] 2.2 `ProspeccaoRegistro` ganha os três campos; `_PROSPECCAO_SELECT` e
      `listar_prospeccoes`/`prospeccao_por_telefone_hash` atualizados
- [x] 2.3 `Database.registrar_envio_prospeccao(id, *, por, sucesso, erro=None)`
- [x] 3.1 Módulo novo `camucrm/painel/envio.py` — `enviar_prospeccao(...)`,
      único ponto do painel que importa `camucrm.transport`
- [x] 3.2 Rota `POST /api/prospeccao/{id}/enviar` em `camucrm/painel/api.py`
      — 422 sem `por`/`telefone`/`mensagem`, 502 em `TransporteError`
- [x] 3.3 `views.prospeccao_para_json` inclui `enviado_em`/`enviado_por`/
      `enviado_erro` (sem expor `telefone` como campo — continua só no link)
- [x] 4.1 Frontend: botão "Enviar pela Evolution API" + popup em
      `camucrm/painel/static/app.js`
- [x] 4.2 CSS do popup em `camucrm/painel/static/app.css`
- [x] 5.1 `tests/test_painel_api.py`: `TesteSemRotaDeEnvio` reformulado —
      exceção nomeada para `envio.py`, teste complementar de que ele exige
      `aprovado_por`
- [x] 5.2 `tests/test_painel_envio.py` novo: `enviar_prospeccao` sucesso,
      falha (grava `enviado_erro`, propaga erro), validação dos três campos
      obrigatórios
- [x] 5.3 `tests/test_prospeccao_db.py`/testes de `db.py` cobrindo
      `registrar_envio_prospeccao` (sucesso não apaga histórico de sucesso
      anterior em caso de falha subsequente)
- [x] 6.1 `.env.example`: comentário sobre `EVOLUTION_API_KEY` também ser
      lida pelo painel
- [x] 6.2 `openspec/project.md`: registrar a decisão revertida em "Decisões
      que divergem ou estendem o documento"
- [x] 7.1 `make test` verde
