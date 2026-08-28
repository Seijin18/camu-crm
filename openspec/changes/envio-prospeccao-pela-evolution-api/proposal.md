# Envio de prospecção pela Evolution API — botão no painel

## Why

`prospeccao-b2b-shortlist/design.md` (decisão 2) escolheu deliberadamente
**não** enviar pela Evolution API: só o link `api.whatsapp.com/send`, com o
clique do operador abrindo o WhatsApp e o envio de fato acontecendo lá
dentro. A razão registrada era clara — "zero mudança na superfície de
segurança do painel, zero credencial nova".

Pedido explícito do usuário (2026-08-28) reabre essa decisão: quer um botão
na aba de prospecção que envie de fato, pela Evolution API, sem sair do
painel — com popup para revisar/editar o número e a mensagem antes de
confirmar.

Isso é uma reversão consciente de uma garantia testada
(`test_nenhum_modulo_do_painel_importa_transport`), não uma correção de bug
— por isso vira change formal, e a decisão revertida fica registrada aqui e
em `openspec/project.md`, não apagada.

## O que continua de pé (§1/§10 do documento principal)

**Envio continua sendo sempre humano, nunca automático.** O que muda é
*onde* o clique de aprovação acontece — no painel em vez do WhatsApp
Web/app — não *se* um humano aprova antes do envio. O popup mostra a
mensagem pronta para edição; o envio exige `aprovado_por` preenchido, a
mesma trava de `transport/base.py::validar_aprovacao` que `camucrm enviar`
já usa. Nenhum envio acontece sem esse nome.

## What Changes

- `docs/mensagem-prospeccao.md` — texto do template atualizado (pedido do
  usuário, sem mudança de mecanismo: `{nome}` continua sendo o único
  placeholder).
- Tabela `prospeccoes` ganha `enviado_em`, `enviado_por`, `enviado_erro`
  (ADIÇÃO ao schema de `prospeccao-b2b-shortlist/design.md`) — resultado da
  última tentativa de envio pela API, distinto de `aberto_em`/`aberto_por`
  (que já existia e continua significando "clicou no link do WhatsApp").
- Módulo novo `camucrm/painel/envio.py` — **o único** módulo de
  `camucrm/painel/` que importa `camucrm.transport`, documentado como tal.
  Todo módulo de leitura continua sem essa importação; a garantia vira "só
  este módulo, e ele exige aprovado_por", não "nenhum módulo".
- Rota nova `POST /api/prospeccao/{id}/enviar` — corpo
  `{telefone, mensagem, por}`, os três obrigatórios. Chama
  `transport.criar_transporte("evolution").enviar(...)`. 422 se `por`
  vazio; 502 se a Evolution recusar ou estiver fora do ar (mesmo
  `TransporteError` que `camucrm enviar` já trata).
- Painel — botão "Enviar pela Evolution API" ao lado do link de WhatsApp
  existente (que continua existindo — os dois convivem, o operador escolhe).
  Abre popup com telefone e mensagem pré-preenchidos e editáveis, e o campo
  "aprovado por" (mesmo padrão de outras ações do painel).
- `.env.example` — comentário atualizado: `EVOLUTION_API_KEY` deixa de ser
  "só para o receptor/CLI enviarem"; o painel também passa a lê-la quando o
  botão é usado.

## Impact

- Specs afetadas: `envio-prospeccao-pela-evolution-api` (nova, estende
  `prospeccao-b2b-shortlist`)
- Código alterado: `camucrm/db.py` (schema + método), `camucrm/painel/
  envio.py` (novo), `camucrm/painel/api.py`, `camucrm/painel/views.py`,
  `camucrm/painel/static/*`, `docs/mensagem-prospeccao.md`,
  `.env.example`
- Testes alterados: `tests/test_painel_api.py` (a garantia AST muda de
  forma, não desaparece — ver `design.md`)
- Testes novos: `tests/test_painel_envio.py`
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Envio em lote/automático** — continua só disparo unitário por clique
  humano, um popup por vez, mesma disciplina do link de WhatsApp que
  substitui em uso, não em existência (o link continua no ar).
- **Generalizar a rota de envio para conversas normais** — esta rota é
  escopada à tabela `prospeccoes` (prospecção fria B2B, base legal já
  decidida em `prospeccao-b2b-shortlist/design.md`). Enviar pela API a
  partir de uma conversa do funil normal continua sendo só `camucrm
  enviar` (CLI) — generalizar isso é decisão maior, fora deste pedido.
