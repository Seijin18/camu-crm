# Delta: painel-web

## ADDED Requirements

### Requirement: Painel não envia e não segura credencial

O painel web NÃO DEVE expor nenhuma rota capaz de enviar mensagem para o
cliente, e NÃO DEVE importar `camucrm.transport` nem segurar credencial da
Evolution API. `camucrm enviar` continua o único caminho de envio (§1: envio
é humano, sempre).

#### Scenario: Nenhuma rota de envio existe

- **WHEN** as rotas de `camucrm/painel/api.py` são inspecionadas
- **THEN** nenhum path contém "enviar" ou equivalente de disparo de mensagem

#### Scenario: camucrm.painel não importa camucrm.transport

- **WHEN** o módulo `camucrm.painel` (e seus submódulos) é analisado por
  `ast.parse`
- **THEN** nenhum import de `camucrm.transport` aparece na árvore

#### Scenario: Bind é 127.0.0.1

- **WHEN** o servidor do painel sobe
- **THEN** ele escuta em `127.0.0.1`, nunca em `0.0.0.0` ou interface externa

### Requirement: Telefone nunca em claro

Toda resposta da API do painel que descreve um contato DEVE expor apenas
`tem_telefone` como booleano. O número de telefone em claro NÃO DEVE
aparecer em nenhuma resposta (§12).

#### Scenario: Resposta da API nunca inclui telefone

- **WHEN** qualquer rota de `/api` devolve dados de um contato
- **THEN** o corpo da resposta contém `tem_telefone: true` ou
  `tem_telefone: false`
- **AND** não contém o número de telefone em nenhuma chave

### Requirement: Colunas derivadas do kanban recusam drop

Uma coluna do kanban cujo estágio é alcançado só por regra (não por marco
manual) DEVE sair da API marcada como `derivada: true`, com
`aceita_drop: false` e `motivo_recusa` citando a seção do documento que
sustenta a recusa (§3 — estágio não regride e não é escrito à mão fora dos
marcos previstos).

#### Scenario: Coluna derivada recusa drop

- **WHEN** a listagem do kanban é montada para uma conversa em estágio
  derivado
- **THEN** a coluna correspondente sai com `derivada: true`,
  `aceita_drop: false` e `motivo_recusa` contendo a referência "§3"

### Requirement: Autenticação por token opcional

Quando nenhum token está configurado no ambiente, todas as rotas DEVEM
responder normalmente. Quando um token está configurado, toda rota DEVE
exigir o header `X-Camu-Token` comparado por `hmac.compare_digest` — nunca
por igualdade direta de string.

#### Scenario: Sem token configurado, tudo passa

- **WHEN** nenhum token está definido no ambiente
- **THEN** requisições sem qualquer header de autenticação são aceitas

#### Scenario: Token configurado e header ausente ou errado recusa

- **WHEN** um token está configurado e a requisição chega sem `X-Camu-Token`
  ou com um valor incorreto
- **THEN** a resposta é recusada (401 ou 403), sem executar a rota

#### Scenario: Token configurado e header correto passa

- **WHEN** um token está configurado e a requisição chega com
  `X-Camu-Token` correspondente
- **THEN** a rota executa normalmente

### Requirement: Leitura reaproveita as regras existentes, sem duplicar cálculo

O painel NÃO DEVE reimplementar cálculo de fila, estágio ou temperatura. Toda
leitura agregada DEVE passar pelas funções de `rules/` e `pipeline.py` já
existentes.

#### Scenario: Fila do painel bate com rules.fila.montar_fila

- **WHEN** a fila é solicitada pela API do painel
- **THEN** a ordem e o conteúdo batem exatamente com
  `rules.fila.montar_fila` chamado sobre os mesmos dados

#### Scenario: Kanban reflete pipeline.recalcular(persistir=False)

- **WHEN** o kanban é montado para uma conversa
- **THEN** o estágio exibido é o mesmo que `pipeline.recalcular(persistir=False)`
  produziria para aquela conversa, sem gravar nada no banco
