# Tasks — ingestão restrita por instância

## 1. Config

- [x] 1.1 `camucrm/config.py`: `ENV_INSTANCIAS_RESTRITAS =
      "CAMU_INSTANCIAS_RESTRITAS"` + `instancias_restritas() -> frozenset[str]`,
      CSV parseado, vazio por padrão (→ Requirement "Restrição é por
      instância, nunca global").

## 2. Leitura de contato sem upsert

- [x] 2.1 `camucrm/db.py::contato_por_telefone_hash(hash) -> Contato | None`
      — leitura pura, mesmo padrão de `prospeccao_por_telefone_hash` (→
      Requirement "Instância restrita só acompanha contato já conhecido").
- [x] 2.2 `tests/fakes.py`: `FakeDatabase.contato_por_telefone_hash` —
      procura em `self.contatos` por `telefone_hash`.

## 3. `ingest.ingerir`

- [x] 3.1 Novo parâmetro `instancia: str | None = None`. Quando `instancia
      in config.instancias_restritas()`: consulta `contato_por_telefone_hash`
      e `prospeccao_por_telefone_hash`; sem nenhum dos dois, devolve
      `ResultadoIngestao(None, None, ignorada=True)` ANTES de tocar
      `db.transacao()` — nenhum upsert, nenhuma mensagem gravada (→
      Requirement "Instância restrita só acompanha contato já conhecido").
- [x] 3.2 A checagem roda para as duas direções (inbound e eco `fromMe`) —
      nenhum `if evento.direcao == ...` na condição.
- [x] 3.3 Docstring de `ingerir` atualizada explicando a restrição e por que
      é por instância, não global (linkar para `design.md` do change).

## 4. Webhook

- [x] 4.1 `camucrm/webhook.py::_processar`: `instancia = payload.get(
      "instance")`, passado para `ingerir(..., instancia=instancia)`. Sem
      mudança na ordem `registrar_evento_bruto` → `ingerir` (→ Requirement
      "Payload cru continua sendo preservado incondicionalmente").

## 5. CLI

- [x] 5.1 `camucrm/cli.py::cmd_ingerir`: flag `--instancia`, repassada para
      `ingerir(..., instancia=args.instancia)` — mesmo caminho do webhook
      (→ Requirement "`cmd_ingerir` e o webhook nunca divergem na
      restrição").

## 6. Testes

- [x] 6.1 `tests/test_ingest.py`: instância não restrita aceita telefone
      novo (comportamento inalterado); instância restrita + telefone
      desconhecido não cria nada e devolve `ignorada=True`; instância
      restrita + telefone já `contato` segue normalmente; instância
      restrita + telefone em `prospeccoes` cria contato B2B normalmente;
      restrição vale igual para `direcao=in` e `direcao=out` (eco
      `fromMe`); `CAMU_INSTANCIAS_RESTRITAS` ausente não muda nada.
- [x] 6.2 `tests/test_webhook.py`: evento com `instance` no payload chega
      restrito em `ingerir`; evento ignorado por restrição ainda grava
      `eventos_recebidos_bruto` (→ Requirement "Payload cru continua sendo
      preservado incondicionalmente").
- [x] 6.3 Teste (onde existir cobertura de `cmd_ingerir`) confirmando que
      `--instancia` produz o mesmo resultado que o webhook para o mesmo
      payload (→ Requirement "`cmd_ingerir` e o webhook nunca divergem").
- [x] 6.4 Suíte completa verde (`make test`).

## 7. Sincronização (antes de arquivar)

- [x] 7.1 Nenhuma mudança de schema (`contatos_por_telefone_hash` é
      `SELECT`, não `ALTER`) — confirmar antes de fechar.
- [ ] 7.2 **Pendente, fora do código** — quando as instâncias novas forem
      de fato registradas na Evolution API, confirmar contra um payload
      real que o campo `instance` chega como assumido em `design.md`
      (Decisão 3) — se divergir, é ajuste de uma linha em `webhook.py`,
      registrado aqui para não se perder. Não bloqueia o resto do change
      (falha segura: `instancia=None` não restringe nada).
