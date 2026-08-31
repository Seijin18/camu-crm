# Popup "quem está operando?" nas ações da Prospecção sem alerta cru

## Why

Pedido do usuário, direto do uso real do painel: os três botões de ação por
linha da Prospecção que não têm formulário próprio ("Marcar como já
enviado", "Não é número de WhatsApp", "Desfazer") chamavam
`chamarApiEscrever(..., {por: obterOperador(), ...})` direto. Quando o
dropdown "Quem está operando" do topo (change `dropdown-operador`) está
vazio, o servidor recusa (`por é obrigatório`) e o erro só aparecia como um
`alert()` cru — o operador tinha que fechar o alerta, rolar até o topo,
escolher o operador, e clicar o botão de novo.

O popup "Enviar pela Evolution API" (`abrirPopupEnvioProspeccao`) já não
tem esse problema — tem campo de operador próprio, com o mesmo
`criarSeletorOperador()` do topo. Faltava o mesmo tratamento nos três
botões mais simples, que nunca tiveram formulário.

## What Changes

- `garantirOperador()` (novo, `app.js`): popup reaproveitando
  `criarSeletorOperador()` — se já existe um operador válido salvo
  (`obterOperador()` bate com `OPERADORES`), resolve na hora sem mostrar
  nada; senão mostra o popup, e resolve com o valor escolhido (ou `null` se
  cancelado/Escape/clique fora).
- `salvarOperadorEAtualizarTopo(valor)` (novo): grava no `localStorage`
  (`salvarOperador`) e também escreve no `<select id="campo-operador">` do
  cabeçalho — sem isto, escolher o operador dentro de um popup persistia
  mas o dropdown do topo continuava mostrando o valor antigo até o próximo
  carregamento de página.
- `linhaProspeccao`: os três handlers (`botaoEnviadaManual`,
  `botaoNaoWhatsapp`, `desfazer` de "não é WhatsApp") chamam
  `await garantirOperador()` antes de `chamarApiEscrever`, e abortam sem
  nada acontecer se o operador cancelar o popup.
- `abrirPopupEnvioProspeccao`: troca `salvarOperador(por)` por
  `salvarOperadorEAtualizarTopo(por)` — mesmo ganho (dropdown do topo
  reflete a escolha feita dentro do popup), sem mudar o resto do fluxo já
  existente daquele popup.

## Impact

- Specs afetadas: `prospeccao-popup-operador` (nova)
- Código: `camucrm/painel/static/app.js` (só front-end — nenhuma rota,
  nenhum contrato de API muda)
- Testes: não há suíte de JS no repo (mesma situação de
  `painel-preserva-estado-em-refresh`) — verificação manual contra o painel
  real, registrada em `tasks.md`. `make test` roda por disciplina (nenhuma
  regressão esperada em Python).
- Bloqueado por: nenhum.

## Fora de escopo

- **Estender `garantirOperador()` para outras telas** (kanban
  drag-and-drop, escolha de rascunho, desconsiderar recusa). Só a
  Prospecção foi pedida; os outros pontos já usam `obterOperador()` direto
  e continuam como estavam — se o mesmo incômodo aparecer lá, vira ajuste
  próprio, não algo emendado aqui.
- **Adicionar operador à lista `OPERADORES` de dentro do popup.** A lista
  continua fixa (`["Marcos", "Felipe"]`, change `dropdown-operador`) —
  editá-la é mudança de código, não de dado de usuário.
