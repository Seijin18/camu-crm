# Refresh de tempo real ignora contato de teste

## Why

Usuário reportou, depois de `painel-preserva-estado-em-refresh` já
implementado: o painel ainda dá refresh completo da aba aberta sempre que
ele manda mensagem para o Felipe. Investigação contra o banco de produção
confirmou a causa, e é **diferente** do que `painel-preserva-estado-em-
refresh` resolveu:

- Felipe é `contato_id=94`, `e_teste=TRUE` (change
  `contatos-de-teste-isolados`) — a conversa dele (`id=102`) já é invisível
  em kanban/fila/conversas por padrão, exatamente como o requirement
  daquele change pede.
- `Database.token_de_mudanca` (`db.py`), no entanto, sempre foi um `MAX`
  bruto sobre `mensagens`/`eventos_estagio`/`conversas`, **sem** o filtro de
  `e_teste` que toda outra leitura "real" do painel já aplica
  (`listar_conversas_abertas`, `montar_fila`, etc. via `_condicao_teste`).
  Mandar mensagem para um contato de teste bump `mensagens.id`, o poller de
  `painel/stream.py` detecta a mudança, e `renderizarRotaSegura()` dispara
  — mesmo que nada visível na tela real tenha mudado.

`painel-preserva-estado-em-refresh` (já implementado) resolve o que
acontece **depois** que um refresh dispara (filtro resetado, formulário
apagado, escrita em voo perdida) — nunca teve como escopo evitar refresh
**irrelevante**; isso ficou registrado explicitamente como fora de escopo
naquele `proposal.md` ("Escopar `token_de_mudanca` por conversa"). Este
change é mais estreito que aquele item adiado: não exige granularidade por
conversa — só exclui o que já é invisível por padrão em todo o resto do
painel.

## What Changes

- `Database.token_de_mudanca`: as três subconsultas ganham `JOIN` até
  `conversas`/`contatos` e `WHERE ct.e_teste = FALSE` — mesmo filtro padrão
  (nem `incluir_teste`, nem `apenas_teste`) que o resto do painel usa nas
  telas reais. Sem parâmetro novo: este método nunca precisou escolher entre
  as duas visões, porque o painel nunca mistura as duas na mesma tela
  (`_condicao_teste`, docstring).
- `tests/fakes.py::FakeDatabase.token_de_mudanca`: mesmo filtro, via
  `_e_teste_da_conversa` já existente no fake.
- Nenhuma rota, nenhum contrato de API, nenhum HTML/JS muda — o cursor é
  interno a `stream.py`/`server.py`.

## Impact

- Specs afetadas: `painel-refresh-ignora-contato-de-teste` (nova)
- Código: `camucrm/db.py` (`token_de_mudanca`), `tests/fakes.py`
- Testes: `tests/test_painel_stream.py` (novo cenário: mensagem em conversa
  de teste não muda o token), `tests/test_db_*` conforme convenção do
  arquivo que já cobre `token_de_mudanca` hoje
- Bloqueado por: nenhum. Não depende de `painel-preserva-estado-em-
  refresh` nem o modifica — os dois se complementam (um evita refresh
  irrelevante, o outro protege o que sobra de relevante).

## Fora de escopo

- **Notificação ao vivo para quem está em "Modo teste" ligado.** Com este
  change, o poller nunca acorda por causa de conversa de teste — um
  operador com "Modo teste" ativo (change `contatos-de-teste-isolados`)
  não recebe mais o nudge automático quando uma conversa de teste muda; o
  botão "Atualizar" manual continua funcionando normalmente. Aceito porque
  "Modo teste" é usado para inspeção deliberada, não para atenção urgente
  (§0/§6 do documento de definições são sobre a fila real) — resolver isso
  direito exigiria um cursor por conexão (o cliente informa se está em modo
  teste, `gerador_sse` compara contra o próprio cursor) em vez de um único
  cursor global compartilhado por processo, e o ganho não justifica a
  complexidade adicional agora. Registrado como candidato futuro se "Modo
  teste" passar a ser usado para acompanhamento ao vivo na prática.
- **Catch-up de reconexão (`?desde_id=N`) ainda pode entregar uma mensagem
  de teste isolada.** `gerador_sse` busca `listar_mensagens_registradas`
  sem filtro de teste no caminho de reconexão (antes de entrar no laço
  guiado pelo poller) — um cliente que reconectar exatamente entre uma
  mensagem de teste e uma real ainda pode ver um `mensagem` SSE de teste
  uma vez. Não corrigido aqui: é limitado à janela de reconexão (rara),
  contra o caminho ao vivo (constante) que motivou o reporte do usuário.
  Se `listar_mensagens_registradas` ganhar filtro de teste por outro motivo
  no futuro, este caminho também se beneficia de graça.
