# Design — escolher a instância no envio de prospecção

## Fonte da lista: consulta ao vivo, não env var

Decisão do usuário (2026-08-28): listar as instâncias consultando
`GET /instance/fetchInstances` na Evolution API a cada abertura do popup, em
vez de um CSV novo no `.env` (como `CAMU_INSTANCIAS_RESTRITAS`).

Motivo: a lista precisa refletir o que existe de fato — um chip que caiu, ou
uma instância nova pareada hoje (§11: repareamento é rotina). Um CSV manual
divergiria em silêncio. O custo aceito: o popup depende da Evolution API
estar no ar para mostrar o seletor. Mitigação: se a consulta falhar, o
seletor fica **oculto** e o envio segue pela instância única de
`EVOLUTION_INSTANCE` — exatamente o comportamento anterior a este change,
nunca um erro que impeça o envio.

`fetchInstances` mudou de formato entre versões da Evolution API — v1 aninha
em `{"instance": {"instanceName", "connectionStatus"}}`, v2 devolve o objeto
plano (`{"name", "connectionStatus"}`). `listar_instancias` tolera os dois e
ignora item sem nome, em vez de assumir uma versão.

## Onde a chamada mora: `envio.py`, não uma exceção nova à regra da AST

`camucrm/painel/envio.py` já é o único módulo do painel autorizado a
importar `camucrm.transport` (change `envio-prospeccao-pela-evolution-api`,
provado por AST em `tests/test_painel_api.py`). A consulta de instâncias é
mais uma chamada à Evolution API com credencial — mesmo risco que o envio —
então mora no mesmo módulo. `api.py` chama `envio.instancias_disponiveis()`,
não `transport.listar_instancias_evolution()` direto. A garantia AST não
muda de forma nem de espírito: continua "só `envio.py`".

## `instancia` via `criar_transporte`, não via `enviar`

`EvolutionTransporte` já guarda `self.instancia`. Sobrepor via
`criar_transporte("evolution", instancia=...)` mantém o adaptador com uma
única fonte de verdade para a instância durante a chamada — mais simples que
um parâmetro opcional em `enviar` que teria de conviver com `self.instancia`
e decidir a precedência a cada uso. `criar_transporte` sem `instancia` (todo
o resto do sistema: `camucrm enviar`, webhook) continua lendo
`EVOLUTION_INSTANCE` como antes.

## `enviado_instancia`: gravado no sucesso E na falha

Diferente de `enviado_em` (só sucesso), `enviado_instancia` é gravado nos
dois casos. A pergunta que o operador faz depois de uma falha é "falhou por
qual número?" — se o chip do número pessoal caiu, ele precisa ver isso na
linha ("envio falhou pelo pessoal-felipe: ...") para trocar de número na
próxima tentativa. Uma falha não apaga um `enviado_instancia` de sucesso
anterior sozinha — ela o sobrescreve com a instância da tentativa atual, que
é a informação corrente e útil. O `enviado_em` de um sucesso anterior
sobrevive (regra que já existe).

## Frontend: seletor oculto por padrão

O `<select>` nasce com `class="escondido"` (display:none) e só aparece
depois que `/api/prospeccao/instancias` responde com ≥1 instância. Assim:

- Evolution API no ar → operador escolhe o número.
- Evolution API fora, ou sem credencial no painel → seletor nunca aparece, o
  campo `instancia` vai `undefined` no corpo, e o servidor usa
  `EVOLUTION_INSTANCE`. O envio em si (que também depende da Evolution API)
  falha ou funciona pelo caminho de sempre — o seletor ausente não é um
  bloqueio novo.

Item desconectado aparece na lista como "&lt;nome&gt; (desconectado)" — o
operador ainda pode escolher (talvez vá reconectar), mas vê o estado.
