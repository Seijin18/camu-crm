# Marcar prospecção como já enviada e como "não é WhatsApp"

## Why

Pedido explícito do usuário (2026-08-31): na aba `/prospeccao`, o operador
precisa de dois botões de triagem por linha:

1. **Marcar como já enviado** — o petshop já foi contatado por outro caminho
   (ligação, e-mail, visita), e o número não deve continuar na fila de
   disparo. Hoje só existe "enviado" quando a Evolution API confirma
   (`enviado-prospeccao-pela-evolution-api`) ou quando o link `wa.me` é
   aberto (`aberto_em`, que é só intenção). Não há como registrar "já
   resolvi isso" à mão.
2. **Não é número de WhatsApp** — o telefone comercial da planilha não
   atende no WhatsApp. A linha precisa sair da lista de disparo, mas não
   pode ser apagada: a próxima reimportação da planilha traria o número de
   volta.

Ambas são marcas MANUAIS, nunca inferidas — mesmo princípio de
`contatos.e_teste` e `marcos_manuais`.

## What Changes

- Schema `prospeccoes`: `nao_whatsapp BOOLEAN NOT NULL DEFAULT FALSE`,
  `nao_whatsapp_em`, `nao_whatsapp_por` (`ALTER TABLE ... ADD COLUMN IF NOT
  EXISTS`). O envio manual NÃO ganha coluna nova — reaproveita `enviado_*`
  com `enviado_instancia = 'manual'`.
- `ProspeccaoRegistro` ganha `nao_whatsapp`/`nao_whatsapp_em`/
  `nao_whatsapp_por`; `_PROSPECCAO_SELECT` e o SELECT de `listar_prospeccoes`
  atualizados.
- `Database.marcar_prospeccao_enviada_manual(id, *, por, valor=True)` —
  grava `enviado_em = now()`, `enviado_instancia = 'manual'`; `valor=False`
  desfaz, mas SÓ quando a marca era manual (nunca apaga um envio real da
  API).
- `Database.marcar_prospeccao_nao_whatsapp(id, *, por, valor=True)`.
- Rotas novas `POST /api/prospeccao/{id}/enviada-manual` e
  `POST /api/prospeccao/{id}/nao-whatsapp` — `por` obrigatório (422 sem),
  `valor` para desfazer. Nenhuma toca `camucrm.transport`; nenhuma contém
  "enviar" no path (o teste-guarda de `test_painel_api.py` continua valendo).
- `views.prospeccao_para_json` inclui `enviado_manual` (derivado de
  `enviado_instancia == 'manual'`), `nao_whatsapp`, `nao_whatsapp_em`,
  `nao_whatsapp_por`.
- Painel: dois botões por linha na aba de prospecção. `nao_whatsapp` esconde
  os botões de disparo e mostra selo + "Desfazer"; envio manual mostra selo
  "marcado como já enviado (data)" e o botão vira "Desfazer 'já enviado'".

## Impact

- Specs afetadas: `prospeccao-marcar-enviada-e-nao-whatsapp` (nova, estende
  `prospeccao-b2b-shortlist` / `envio-prospeccao-pela-evolution-api`)
- Código: `camucrm/db.py`, `camucrm/painel/api.py`, `camucrm/painel/views.py`,
  `camucrm/painel/static/app.js`
- Testes: `tests/fakes.py`, `tests/test_prospeccao.py`,
  `tests/test_painel_api.py`
- Bloqueado por: nenhum. Bloqueia: nenhum.

## Fora de escopo

- **Filtro "esconder já enviadas / não-whatsapp" na listagem** — a linha
  continua aparecendo, só sem os botões de disparo. Um filtro pode vir
  depois se a lista ficar poluída.
- **Métrica de quantos números da planilha não são WhatsApp** — o dado fica
  gravado (`nao_whatsapp_em`), mas nenhuma tela agrega isso ainda.
