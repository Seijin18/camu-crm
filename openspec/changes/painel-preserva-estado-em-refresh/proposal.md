# Painel preserva estado do operador durante o refresh de tempo real

## Why

`token_de_mudanca` (`db.py`, change `painel-tempo-real`) é um cursor único
por processo — agrega `MAX(mensagens.id)`, `MAX(eventos_estagio.id)` e
`MAX(conversas.atualizado_em)` de **todas** as conversas, não por
conversa/aba. `processarBlocoSse` (`app.js`) reage a qualquer evento
`mensagem`/`mudanca` chamando `renderizarRotaSegura()`, que começa por
`conteudo.textContent = ""` — apaga e remonta **a aba inteira**, sem saber
se a mudança tem qualquer relação com o que está na tela.

Consequência observada pelo usuário: uma mensagem em **qualquer** conversa
faz os filtros da aba Conversas e da aba Prospecção voltarem ao padrão,
mesmo que o operador não tenha nada a ver com aquela conversa. Investigação
encontrou dois problemas correlatos, mais sérios que a perda de filtro:

1. **Formulário em edição é apagado sem aviso.** `renderizarFormularioEval`
   (ground truth, `#/groundtruth/novo` e `#/editar`), `renderizarImportar
   Prospeccao` e `renderizarImportarConversaWhatsapp` vivem dentro de
   `conteudo`. Rotular uma conversa ou colar uma exportação grande do
   WhatsApp é trabalho manual; perder isso porque uma mensagem chegou em
   outra conversa é pior que um filtro resetado — é perda de trabalho.
2. **Escrita em andamento é descartada silenciosamente.** Em
   `renderizarRascunhos`, o clique em "Gerar rascunho" chama o LLM e grava
   o resultado num `areaResultado` capturado por closure. Se um re-render
   acontece enquanto a chamada está em voo, o container antigo — com aquele
   nó — é destacado do DOM antes da resposta chegar; a escrita no banco
   **acontece** (o rascunho é gravado), mas a atualização de tela vai para
   um nó órfão que ninguém vê. O operador vê a tela "não fazer nada" e pode
   clicar de novo, achando que falhou. O mesmo padrão existe em
   `botaoEscolher` (registrar escolha de rascunho) e
   `botaoDesconsiderarRecusa`.

Nenhum destes é bug de dado no banco — é a tela mentindo sobre o que
aconteceu, o tipo de coisa que a auditoria de 2026-08 (`project.md`,
"Correções pendentes") tratou como crítico quando encontrado no pipeline de
ingestão. Aqui é o mesmo problema, na camada de UI.

## What Changes

- **Filtros sobrevivem ao re-render.** Estado dos filtros (Conversas:
  `filtro-estagio`/`filtro-temperatura`/`filtro-bola`/`ordenar`;
  Prospecção: `zona`/`bairro`/`nota_minima`/`tier`/`nao_convertidas`, mais
  `ordenar` do change `prospeccao-filtro-e-ordenacao`) passa a viver numa
  variável de módulo por aba (`estadoFiltrosConversas`,
  `estadoFiltrosProspeccao`), lida ao montar os controles e escrita no
  `change` de cada um — não mais reconstruída do zero a cada render.
- **Refresh automático é suprimido enquanto há edição em risco.** Uma flag
  de módulo (`haEdicaoEmAndamento`) é levantada por: qualquer campo do
  formulário de ground truth com valor não vazio; o textarea de importação
  (prospecção ou WhatsApp) com conteúdo colado; uma chamada
  `chamarApiEscrever` em voo (geração de rascunho, escolha de rascunho,
  desconsiderar recusa). Enquanto ativa, `processarBlocoSse` não chama
  `renderizarRotaSegura()` — em vez disso marca `atualizacaoPendente = true`
  e mostra um aviso não-destrutivo ("Há atualizações novas — clique em
  Atualizar quando terminar") ao lado do botão "Atualizar" já existente
  (`CLAUDE.md`/topo de `app.js`: "botão Atualizar manual continua
  existindo"). A flag desce quando o formulário limpa, a resposta da API
  chega, ou o operador sai da rota.
- **Nenhuma mudança no `token_de_mudanca` nem em `stream.py`.** O cursor
  continua global — este change resolve o sintoma na camada de
  apresentação, sem re-arquitetar o SSE (ver "Fora de escopo").

## Impact

- Specs afetadas: `painel-preserva-estado-em-refresh` (nova)
- Código: `camucrm/painel/static/app.js` (não toca `stream.py`, `db.py`
  nem nenhuma rota de `api.py` — puramente client-side)
- Testes: não há suíte de JS no repo hoje (`CLAUDE.md`: `unittest` puro,
  sem fixtures de front-end) — verificação é manual, registrada em
  `tasks.md` contra o painel real (`./start.sh`), no mesmo padrão que
  `marco-manual-visivel-na-aba-conversas` (`tasks.md` 4.2) já usou para
  UI. Nenhum teste Python precisa mudar porque nenhuma rota muda.
- Bloqueado por: nenhum. Bloqueia: nenhum — mas complementa
  `prospeccao-filtro-e-ordenacao` (o `ordenar` novo daquele change entra no
  mesmo mecanismo de persistência de filtro descrito aqui).

## Fora de escopo

- **Escopar `token_de_mudanca` por conversa.** Seria a correção "raiz"
  (re-render só da conversa afetada), mas exige mudar o contrato do SSE e o
  cursor de reconexão — trabalho bem maior para resolver um problema que a
  persistência de estado client-side já resolve na prática. Registrado
  como candidato futuro se o painel crescer para múltiplos operadores
  simultâneos e o custo de re-render completo (hoje barato — DOM pequeno,
  sem framework) deixar de ser desprezível.
- **Preservar posição de scroll** em listas longas (Conversas, Mensagens).
  Cosmético, não perda de dado — fica de fora desta entrega; se incomodar
  na prática depois desta correção, vira change próprio.
- **Undo ou recuperação de formulário perdido.** Este change previne a
  perda suprimindo o refresh; não adiciona rascunho automático de
  formulário (`localStorage` etc.) para o caso de o operador fechar a aba
  por conta própria.
