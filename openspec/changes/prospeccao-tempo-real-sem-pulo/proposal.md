# Prospecção em tempo real, sem pular pro topo

## Why

Pedido do usuário (2026-08-31): com dois operadores na aba `/prospeccao` ao
mesmo tempo, a tela "se atualiza sozinha" a cada mensagem que a Evolution API
recebe em qualquer instância — o operador é jogado de volta pro topo e perde
o que estava digitando nos filtros.

Causa, confirmada no código:

- `PollerMudanca` (`painel/stream.py`) compara `db.token_de_mudanca`, que era
  `"m:e:c"` — `MAX(mensagens.id)`, `MAX(eventos_estagio.id)`,
  `epoch(MAX(conversas.atualizado_em))`. Qualquer mensagem nova move a 1ª
  parte, o poller dispara o evento `mudanca`, e **todo** cliente conectado
  chama `renderizarRotaSegura()`, que faz `conteudo.textContent = ""` e
  remonta a tela inteira — inclusive os `<input>` de filtro, o que zera foco,
  valor e posição de scroll.
- A aba de prospecção **não depende** de nenhuma das três partes do token: a
  tabela `prospeccoes` está fora da fórmula. O re-render ali nunca traz
  informação nova — é só dano colateral.
- Pior: as marcas de triagem de um operador (`enviada-manual`,
  `nao-whatsapp`, `abrir`, envio pela API) **não** moviam o token, então o
  outro operador nunca via essas mudanças via stream de qualquer forma.

## What Changes

**Correção recomendada (recorte por rota, no cliente):**

- `static/app.js`: o evento `mudanca`/`mensagem` do stream só dispara
  `renderizarRotaSegura()` quando a tela atual reflete o stream de conversas
  — fila (`#/`), kanban, lista de conversas, detalhe de conversa. Nas demais
  abas (prospecção, importações, ground truth, métricas, "o que funciona") o
  stream é ignorado; o botão "Atualizar" manual continua sendo o caminho
  (já é a regra declarada no topo de `app.js`: "o stream é reforço, não
  substituição").

**Ideia 1 (sincronizar prospecção entre operadores, de propósito):**

- `db.token_de_mudanca` ganha uma 4ª parte:
  `epoch(MAX(prospeccoes.atualizado_em))` — token vira `"m:e:c:p"`. O cursor
  de reconexão (`stream.gerador_sse`) já lê só `token.split(":")[0]`, não
  muda.
- Toda mutação da aba de prospecção grava `atualizado_em = now()`:
  `marcar_prospeccao_aberta`, `registrar_envio_prospeccao`,
  `marcar_prospeccao_enviada_manual`, `marcar_prospeccao_nao_whatsapp`
  (`importar_prospeccoes` já gravava no upsert).
- `static/app.js`: no evento `mudanca`, o cliente compara o token **parte a
  parte** com o anterior. Partes 0-2 diferentes → tela de conversas
  redesenha (como acima). Parte 3 diferente **e** a tela atual é
  `#/prospeccao` → chama um "refresh suave" que recarrega **só**
  `#lista-prospeccao`, sem tocar em `#conteudo` nem nos campos de filtro —
  scroll e o que o operador digitou ficam onde estavam. Uma mensagem de
  WhatsApp qualquer (parte 3 igual) nunca chega aqui.
- O "refresh suave" é a closure `carregar` que `renderizarProspeccao` já
  tem; ela passa a se registrar em `refreshSuaveAtual`, que
  `renderizarRota` zera a cada navegação.

## Impact

- Specs afetadas: `painel-tempo-real` (MODIFIED — "Token de mudança como
  cursor"; ADDED — "Re-render do stream é recortado por rota" e "Aba de
  prospecção sincroniza entre operadores sem perder o lugar")
- Código: `camucrm/db.py` (token + 4 UPDATEs + comentário de schema),
  `camucrm/painel/stream.py` (docstring), `camucrm/painel/static/app.js`
- Testes: `tests/fakes.py` (4ª parte do token fake + toques),
  `tests/test_painel_stream.py` (2 casos novos)
- Bloqueado por: nenhum. Bloqueia: nenhum.

## Fora de escopo

- **Atualização in-place por linha** (reconciliar só o `<div>` da linha que
  mudou em vez de recarregar a lista) — a lista é curta e o refresh suave já
  não mexe em scroll/filtro; a diferença não se paga agora.
- **Recorte fino das partes 0-2** (a lista de conversas não precisa
  redesenhar por um evento de estágio de uma conversa que nem está na tela)
  — o comportamento atual dessas telas não muda nesta entrega.
- **Detalhe de conversa não pular enquanto o operador lê/gera rascunho** —
  problema real e análogo, mas é outra tela e outro change.
