# Tasks — popup "quem está operando?" nas ações da Prospecção

## 1. Implementação

- [x] 1.1 `app.js::salvarOperadorEAtualizarTopo`: grava + reflete no
      `<select id="campo-operador">` do topo (→ Requirement "Escolha de
      operador dentro de popup atualiza o dropdown do topo").
- [x] 1.2 `app.js::garantirOperador`: popup com `criarSeletorOperador()`,
      resolve na hora se já há operador válido salvo; `null` se
      cancelado/Escape/clique fora do modal (→ Requirement "Ação sem
      operador mostra popup de escolha em vez de erro").
- [x] 1.3 `linhaProspeccao`: `botaoEnviadaManual`, `botaoNaoWhatsapp` e o
      `desfazer` de "não é WhatsApp" chamam `garantirOperador()` antes de
      `chamarApiEscrever`, abortando sem chamada nenhuma se `por` vier nulo
      (→ Requirement "Ação sem operador mostra popup de escolha em vez de
      erro").
- [x] 1.4 `abrirPopupEnvioProspeccao`: troca `salvarOperador(por)` por
      `salvarOperadorEAtualizarTopo(por)` (→ Requirement "Escolha de
      operador dentro de popup atualiza o dropdown do topo").

## 2. Verificação

- [x] 2.1 `node --check camucrm/painel/static/app.js` — sintaxe válida.
- [x] 2.2 `make test` — 754 testes, OK (nenhuma regressão; mudança é só
      front-end).
- [ ] 2.3 Manual contra o painel real (`./restart.sh` para o JS novo
      entrar em vigor, já que é servido como arquivo estático): com "Quem
      está operando" vazio, clicar "Marcar como já enviado" numa linha da
      Prospecção — popup aparece em vez de alerta; escolher um operador
      confirma a ação E atualiza o dropdown do topo.

## 3. Sincronização

- [x] 3.1 Implementação bateu com o `proposal.md`, sem divergência.
