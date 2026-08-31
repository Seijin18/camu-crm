# Design — prospecção em tempo real, sem pular pro topo

## O recorte mora no cliente, não no poller

`PollerMudanca` continua burro de propósito: consulta um token, compara a
string inteira, dispara o `Event` se mudou. Poderia haver N pollers (um por
"assunto"), mas isso multiplica consultas e contradiz o requirement "Poller
único por processo". Em vez disso, o token carrega **partes** e o cliente
decide o que fazer com cada uma — `stream.py` nem sabe que existe "parte de
prospecção".

Isso segue a forma que o token já tinha: a docstring original diz "três
partes porque são três motivos distintos de a tela estar desatualizada".
Agora são quatro motivos. A 4ª (`prospeccoes.atualizado_em`) é a única que se
move quando alguém tria uma linha da shortlist, e nunca se move por uma
mensagem de WhatsApp.

## Por que `prospeccoes.atualizado_em` e não uma tabela de eventos

`prospeccoes` já tinha a coluna (nasceu com o upsert de reimportação). As
mutações são poucas e todas passam por métodos nomeados no `Database` — botar
`atualizado_em = now()` em cada UPDATE é uma linha por método e não abre
tabela nova. `epoch(MAX(...))` é o mesmo formato que a parte de `conversas`
já usa. Não há necessidade de granularidade por linha: o cliente recarrega a
lista filtrada inteira, que é curta.

## Refresh suave: recarregar a lista, não a rota

`renderizarRotaSegura()` faz `conteudo.textContent = ""` e remonta tudo —
inclusive os `<input>` de filtro, o que é o que rouba foco e scroll. Mas
`renderizarProspeccao` já separa `filtros` (estável) de `#lista-prospeccao`
(volátil), e a closure `carregar` já recarrega **só** a lista lendo os
valores atuais dos filtros. Registrar essa closure em `refreshSuaveAtual` e
chamá-la no evento `mudanca` é reusar o que existe: nenhuma remontagem de
container, nenhum reset de scroll (o navegador só reposiciona scroll quando o
elemento pai é removido/recriado, o que aqui não acontece).

`renderizarRota` zera `refreshSuaveAtual = null` no começo de cada navegação
para a tela seguinte não herdar o hook da anterior — se um `mudanca` chega
logo depois de sair de `/prospeccao`, `rotaEhListaProspeccao()` já é `false`
e o hook nem seria chamado, mas o `null` é a garantia limpa.

## Primeiro evento após conectar

`gerador_sse` sem `desde_id` fixa o cursor no token atual mas **não** o
emite. O primeiro `mudanca` que o cliente vê é sempre uma mudança real. Com
`ultimoTokenMudanca === null`, a comparação parte a parte trata todas as
partes como "mudaram" → na aba de prospecção isso causa **um** refresh suave
extra (recarrega a mesma lista, logo após o load, com o scroll ainda no
topo). Inofensivo, e mais simples do que semear o token na conexão.

## Comportamento das outras abas

`#/metricas`, `#/funciona`, `#/groundtruth*`, `#/prospeccao/importar`,
`#/importar-whatsapp` deixam de reagir ao stream. Nenhuma delas mostra dado
que o stream cobre em tempo hábil, e todas têm o botão "Atualizar". É a
"Correção recomendada" da investigação: o stream volta a ser reforço para as
telas que se beneficiam dele (fila, kanban, conversas), não um tremor global.
