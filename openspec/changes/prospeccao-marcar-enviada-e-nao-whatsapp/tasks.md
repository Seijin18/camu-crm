# Tasks — marcar prospecção como já enviada e como "não é WhatsApp"

- [x] 1.1 Schema: `prospeccoes.nao_whatsapp`/`nao_whatsapp_em`/
      `nao_whatsapp_por` (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)
- [x] 1.2 `ProspeccaoRegistro` + `_PROSPECCAO_SELECT` + SELECT de
      `listar_prospeccoes`
- [x] 1.3 `Database.marcar_prospeccao_enviada_manual` (reaproveita `enviado_*`
      com `enviado_instancia='manual'`; desfazer só limpa marca manual)
- [x] 1.4 `Database.marcar_prospeccao_nao_whatsapp`
- [x] 2.1 Rotas `POST /api/prospeccao/{id}/enviada-manual` e `.../nao-whatsapp`
      — `por` obrigatório (422), `valor` para desfazer
- [x] 2.2 `views.prospeccao_para_json`: `enviado_manual`, `nao_whatsapp`,
      `nao_whatsapp_em`, `nao_whatsapp_por`
- [x] 3.1 Frontend: dois botões por linha; `nao_whatsapp` esconde disparo +
      selo + "Desfazer"; envio manual → selo + botão "Desfazer 'já enviado'"
- [x] 4.1 `tests/fakes.py`: dois métodos novos + chaves nos dicts de
      `criar_prospeccao`/`importar_prospeccoes` + campos no registro
- [x] 4.2 `tests/test_prospeccao.py::TesteMarcasManuais`
- [x] 4.3 `tests/test_painel_api.py`: rotas (grava, 422 sem `por`, desfazer,
      path sem "enviar")
- [x] 5.1 `openspec/project.md`: registrar a extensão
- [x] 5.2 `make test` verde (2 falhas pré-existentes de path no Windows em
      `test_eval_painel`, não relacionadas)
