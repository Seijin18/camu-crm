# Delta: painel-tempo-real

## ADDED Requirements

### Requirement: Token de mudança como cursor

O `token_de_mudanca` DEVE mudar sempre que uma mensagem nova é registrada,
um evento de estágio é gravado, ou `conversas.atualizado_em` muda. O mesmo
token DEVE servir como cursor de reconexão (`?desde_id=N`), de forma que um
cliente que reconecta depois de ficar offline não perca eventos ocorridos
durante a queda.

#### Scenario: Token muda com mensagem, evento de estágio ou atualização de conversa

- **WHEN** uma mensagem é registrada, ou um evento em `eventos_estagio` é
  gravado, ou `conversas.atualizado_em` avança
- **THEN** `token_de_mudanca` calculado depois é diferente do calculado antes

#### Scenario: Reconexão com desde_id não perde eventos

- **WHEN** um cliente SSE reconecta informando `desde_id` do último evento
  que recebeu antes de cair
- **THEN** o stream entrega, antes de retomar o tempo real, tudo o que
  mudou entre `desde_id` e o momento da reconexão

### Requirement: Poller único por processo

O processo do painel DEVE manter no máximo um poller de mudança ativo,
independentemente de quantos clientes SSE estão conectados.

#### Scenario: N clientes conectados geram 1 consulta por intervalo

- **WHEN** N clientes SSE estão conectados simultaneamente
- **THEN** o número de consultas de `token_de_mudanca` por intervalo de
  1,5s é 1, não N

### Requirement: Leitura de banco no gerador SSE nunca bloqueia o loop

Toda chamada síncrona a `psycopg` feita de dentro de um gerador `async def`
do stream SSE DEVE passar por `asyncio.to_thread`, nunca ser executada
diretamente no event loop.

#### Scenario: Chamada de banco no gerador SSE passa por to_thread

- **WHEN** o gerador SSE precisa ler o estado atual do banco
- **THEN** a chamada é despachada via `asyncio.to_thread`, não executada
  diretamente na coroutine

### Requirement: Token nunca na URL

A autenticação do stream SSE (`X-Camu-Token`) DEVE viajar por header, nunca
por query string — evita vazamento em log de acesso de proxy/servidor.

#### Scenario: Autenticação do stream usa header, não query string

- **WHEN** o cliente conecta ao endpoint SSE
- **THEN** o token de autenticação é enviado no header `X-Camu-Token`
- **AND** nenhum parâmetro de query string carrega o token
