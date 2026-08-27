# Tasks — lista de prospecção B2B

## 1. Schema e dados

- [ ] 1.1 `camucrm/db.py`: tabela `prospeccoes` (`design.md`) na constante
      `SCHEMA`, com `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` no
      `ensure_schema()` (mesmo padrão de `contatos.e_teste`, para não
      exigir banco recriado) (→ Requirement "Shortlist separada de
      contatos/conversas").
- [ ] 1.2 `db.importar_prospeccoes(linhas: list[dict]) -> ResumoImportacao`:
      upsert por `telefone_hash` (via `hash_telefone` reaproveitado), reporta
      contagem de novos/atualizados/inválidos — nunca descarta linha
      ilegível em silêncio (→ Requirement "Importação nunca descarta linha
      em silêncio").
- [ ] 1.3 `db.listar_prospeccoes(*, zona=None, bairro=None, nota_minima=None,
      tier=None, apenas_nao_convertidas=False) -> list[ProspeccaoRegistro]`:
      `LEFT JOIN` por `telefone_hash` contra `contatos`, expõe `contato_id`/
      `conversa_id` quando já convertida (→ Requirement "Detecção de
      conversão sem estado próprio").
- [ ] 1.4 `db.marcar_prospeccao_aberta(prospeccao_id, *, por)`: grava
      `aberto_em`/`aberto_por` (→ Requirement "Abertura de link é
      registrada").
- [ ] 1.5 `db.prospeccao_por_telefone_hash(hash) -> ProspeccaoRegistro | None`.

## 2. Ingestão

- [ ] 2.1 `camucrm/ingest.py::ingerir`: antes de `upsert_contato`, consulta
      `prospeccao_por_telefone_hash`; se existir, usa `tipo_padrao=B2B` em
      vez do default do chamador (→ Requirement "Conversão usa tipo B2B da
      origem curada, não inferência de conteúdo").

## 3. Template de mensagem

- [ ] 3.1 `docs/mensagem-prospeccao.md` — template com `{nome}`, path
      configurável via `CAMU_MENSAGEM_PROSPECCAO` em `camucrm/config.py`
      (mesmo padrão de `CAMU_PLAYBOOK`) (→ Requirement "Mensagem é template
      fixo, não geração por LLM").
- [ ] 3.2 Função pura (`camucrm/prospeccao.py` ou dentro de
      `camucrm/painel/views.py`) que monta nome curto (corta em `|`/` - `,
      minúsculas) + link `https://api.whatsapp.com/send/?phone=...&text=...`
      codificado corretamente para URL.

## 4. Painel — API

- [ ] 4.1 `POST /api/prospeccao/importar` (multipart/form-data, CSV) →
      `views.resumo_importacao_para_json`.
- [ ] 4.2 `GET /api/prospeccao?zona=&bairro=&nota_minima=&tier=&nao_convertidas=`
      → lista com link/mensagem prontos por linha.
- [ ] 4.3 `POST /api/prospeccao/{id}/abrir` — corpo `{por}`, grava
      `aberto_em`/`aberto_por`.

## 5. Painel — front

- [ ] 5.1 Aba nova "Importar prospecção": upload de CSV, relatório do
      resultado (N novos, M atualizados, K inválidos com motivo).
- [ ] 5.2 Aba nova "Prospecção": tabela com os campos da planilha + filtros
      (zona, bairro, nota mínima, tier, "já é conversa"); por linha, botão
      "copiar mensagem" e botão/link "abrir WhatsApp" (`target="_blank"`,
      nunca dispara nada sozinho); linha já convertida mostra link para
      `#/conversas/{id}` em vez dos botões de disparo.
- [ ] 5.3 **Nunca aparece em kanban/fila/conversas/métricas** — confirmar
      que nenhuma rota existente foi tocada para incluir `prospeccoes` (→
      Requirement "Shortlist separada de contatos/conversas").

## 6. Testes

- [ ] 6.1 `tests/test_prospeccao.py`: importar CSV novo cria linhas;
      reimportar a mesma planilha atualiza em vez de duplicar (dedupe por
      `telefone_hash`); linha com telefone ilegível é reportada, não
      descartada silenciosamente; filtros de `listar_prospeccoes`.
- [ ] 6.2 Teste de conversão: contato/conversa real criada via `ingest.ingerir`
      com o mesmo telefone de uma linha de `prospeccoes` faz
      `listar_prospeccoes` expor `contato_id`/`conversa_id` (→ Requirement
      "Detecção de conversão sem estado próprio").
- [ ] 6.3 Extensão de `tests/test_ingest.py`: mensagem inbound de telefone
      presente em `prospeccoes` cria contato com `tipo=b2b`, não o padrão
      B2C (→ Requirement "Conversão usa tipo B2B da origem curada").
- [ ] 6.4 Extensão de `tests/test_painel_api.py`: as três rotas novas;
      confirmar que `prospeccoes` nunca aparece em `/api/kanban`, `/api/fila`,
      `/api/conversas`, `/api/o-que-funciona`.
- [ ] 6.5 Teste do link gerado: telefone formatado corretamente (dígitos +
      código do país), nome curto cortado em `|`/` - `, texto codificado
      para URL sem quebrar caracteres acentuados.
- [ ] 6.6 Suíte completa verde (`make test`).
