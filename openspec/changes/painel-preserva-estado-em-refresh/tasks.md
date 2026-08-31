# Tasks — painel preserva estado do operador durante o refresh de tempo real

## 1. Persistência de filtro

- [x] 1.1 `app.js`: `estadoFiltrosConversas` (objeto de módulo,
      `{estagio, temperatura, bola, ordenar}`) — `montarFiltros`/
      `renderizarConversas` inicializam os `<select>` a partir dele (não
      mais sempre no valor padrão) e cada `change` grava de volta (→
      Requirement "Filtros sobrevivem ao refresh de tempo real").
- [x] 1.2 `app.js`: `estadoFiltrosProspeccao` (objeto de módulo,
      `{zona, bairro, notaMinima, tier, naoConvertidas, ordenar}`) — mesmo
      padrão em `renderizarProspeccao` (→ Requirement "Filtros sobrevivem ao
      refresh de tempo real"). Campo `ordenar` só populado se
      `prospeccao-filtro-e-ordenacao` já estiver implementado; caso
      contrário fica sem efeito, sem quebrar nada.

## 2. Supressão de refresh durante edição em risco

- [x] 2.1 `app.js`: flag de módulo `haEdicaoEmAndamento` (boolean) e
      `atualizacaoPendente` (boolean) — `processarBlocoSse` checa a
      primeira antes de chamar `renderizarRotaSegura()`; se ativa, seta a
      segunda e não renderiza (→ Requirement "Refresh automático não apaga
      edição em andamento").
- [x] 2.2 `renderizarFormularioEval`: todo campo do formulário (`gt-id`,
      `gt-funil`, `gt-estagio-final`, `gt-mensagens-digitadas`, nota)
      levanta `haEdicaoEmAndamento` em `input`/`change` quando não-vazio, e
      derruba quando volta a vazio ou o formulário é enviado/cancelado (→
      Requirement "Refresh automático não apaga edição em andamento").
- [x] 2.3 `renderizarImportarProspeccao`/`renderizarImportarConversaWhatsapp`:
      mesmo padrão no textarea/campo de arquivo principal (→ Requirement
      "Refresh automático não apaga edição em andamento").
- [x] 2.4 `renderizarRascunhos` (botão "Gerar rascunho"), `botaoEscolher`
      (registrar escolha) e `botaoDesconsiderarRecusa`: levantam
      `haEdicaoEmAndamento` antes do `await chamarApiEscrever(...)` e
      derrubam no `finally` — cobre a janela em que a resposta pode chegar
      depois de um `conteudo.textContent = ""` (→ Requirement "Escrita em
      voo nunca é descartada silenciosamente").
- [x] 2.5 Aviso não-destrutivo: quando `atualizacaoPendente` for `true`,
      mostrar um indicador pequeno perto do botão "Atualizar" (ex.:
      `● atualizações disponíveis`) — sem popup, sem bloquear interação (→
      Requirement "Refresh automático não apaga edição em andamento").
      Botão "Atualizar" continua forçando o refresh imediatamente, mesmo
      com `haEdicaoEmAndamento` ativa — é escolha do operador, não do
      sistema.

## 3. Verificação manual (sem suíte de JS no repo)

- [ ] 3.1 Contra o painel real (`./start.sh`): abrir Conversas, aplicar um
      filtro, mandar mensagem de teste em outra conversa (`camucrm
      simular`/webhook de teste) — filtro continua selecionado depois do
      refresh automático.
- [ ] 3.2 Mesmo teste na aba Prospecção.
- [ ] 3.3 Abrir `#/groundtruth/novo`, digitar algo no textarea de
      mensagens, mandar mensagem de teste em outra conversa — formulário
      continua intacto; indicador de atualização pendente aparece.
- [ ] 3.4 Abrir detalhe de uma conversa, clicar "Gerar rascunho", mandar
      mensagem de teste em OUTRA conversa antes da resposta do LLM voltar —
      o rascunho aparece na tela quando pronto (não é descartado).
- [x] 3.5 Suíte completa (`make test`) sem regressão — nenhuma rota Python
      muda, então nenhuma quebra esperada, mas roda por disciplina. Rodada:
      740 testes, OK.

## 4. Sincronização

- [ ] 4.1 Ao concluir, confirmar que a implementação bateu com o
      `proposal.md`; registrar aqui qualquer divergência.

**Nota (agente, 2026-08-31):** seções 1 e 2 (código) implementadas em
`app.js`, com uma segunda passada depois adicionando o `<select id="ordenar-
prospeccao">` real (change `prospeccao-filtro-e-ordenacao`, implementado em
sequência no mesmo arquivo) — o "gancho" `if (document.getElementById(...))`
que este change deixou em `renderizarProspeccao` para o campo `ordenar` foi
substituído pelo select de verdade. Seção 3 (verificação manual contra
`./start.sh` no navegador): 3.5 (suíte automatizada) rodada e verde; 3.1-3.4
exigem abrir o painel real no navegador com o sistema completo no ar
(Evolution API/WhatsApp em produção) — **não executado por este agente**,
por ser infraestrutura de produção que um agente não deveria subir/reiniciar
sem confirmação explícita. Ficam `[ ]` para o operador rodar manualmente
contra `./start.sh` quando puder. 4.1 depende dessa verificação. Divergência
de código encontrada e registrada: `renderizarImportarProspeccao`/
`renderizarImportarConversaWhatsapp` não têm textarea de colagem — o "campo
principal" de ambas é `<input type="file">` (CSV/.txt); a flag
`haEdicaoEmAndamento` foi implementada nesse campo (evento `change`, arquivo
selecionado) em vez de um textarea inexistente.
