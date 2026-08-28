# Escolher a instância no envio de prospecção pela Evolution API

## Why

O change `envio-prospeccao-pela-evolution-api` deu ao painel um botão que
envia de fato, mas sempre pela instância única de `EVOLUTION_INSTANCE` do
`.env`. O change `ingestao-restrita-por-instancia` já registra que o número
pessoal do Marcos e o do Felipe viram instâncias próprias da Evolution API,
ao vivo, além do número da Camu.

Pedido explícito do usuário (2026-08-28): no popup "Enviar pela Evolution
API", poder escolher **por qual número** a mensagem sai, entre os
cadastrados.

## What Changes

- `EvolutionTransporte.listar_instancias()` — novo: consulta
  `GET /instance/fetchInstances` e normaliza os dois formatos de resposta
  (v1 aninhado, v2 plano) para `list[InstanciaEvolution(nome, conectada)]`.
- `criar_transporte("evolution", instancia=...)` — parâmetro novo que
  sobrepõe `EVOLUTION_INSTANCE` só para aquela chamada. Vazio/ausente = o
  comportamento de antes.
- `camucrm/painel/envio.py` — `enviar_prospeccao(..., instancia=None)`
  repassa a escolha; `instancias_disponiveis()` novo, delegando a
  `transport.listar_instancias_evolution()`. `envio.py` continua o único
  módulo do painel que importa `camucrm.transport`.
- Rota nova `GET /api/prospeccao/instancias` — lista os números para o
  popup. 502 (com detalhe) quando falta credencial no processo do painel ou
  a Evolution API não responde.
- `EnviarProspeccaoBody` ganha `instancia: str | None`.
- Tabela `prospeccoes` ganha `enviado_instancia VARCHAR(64)` (ADIÇÃO) —
  gravada tanto no sucesso quanto na falha, ao lado de `enviado_em`/
  `enviado_por`/`enviado_erro`.
- `views.prospeccao_para_json` inclui `enviado_instancia`.
- Painel — `<select>` "Enviar pelo número" no popup, populado ao vivo; fica
  oculto se a lista não carrega (aí o envio segue pela instância do `.env`).
  A linha da prospecção mostra "enviado às HH:MM pelo &lt;número&gt;".
- `.env.example` — comentário de `EVOLUTION_INSTANCE` explica que agora é só
  o padrão; o popup lista todas ao vivo.

## Impact

- Specs afetadas: `escolher-instancia-no-envio-prospeccao` (nova, estende
  `envio-prospeccao-pela-evolution-api`)
- Código: `camucrm/transport/evolution.py`, `camucrm/transport/__init__.py`,
  `camucrm/db.py` (schema + `ProspeccaoRegistro` + SELECTs +
  `registrar_envio_prospeccao`), `camucrm/painel/envio.py`,
  `camucrm/painel/api.py`, `camucrm/painel/views.py`,
  `camucrm/painel/static/{app.js,app.css}`, `.env.example`
- Testes: `tests/fakes.py`, `tests/test_transport.py`,
  `tests/test_painel_api.py`
- Bloqueado por: nenhum. Bloqueia: nenhum.

## Fora de escopo

- **Roteamento automático de qual número usa qual conversa** — a escolha é
  sempre manual, um popup por vez. Nenhuma regra decide instância.
- **Envio pela API a partir de conversas do funil normal** — continua só
  `camucrm enviar` (CLI), como em `envio-prospeccao-pela-evolution-api`.
- **Cache da lista de instâncias** — cada abertura do popup consulta ao
  vivo; a lista é curta e muda quando um chip cai (§11).
