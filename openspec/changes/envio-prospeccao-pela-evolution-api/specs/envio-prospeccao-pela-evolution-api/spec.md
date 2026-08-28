# Delta: envio-prospeccao-pela-evolution-api

## ADDED Requirements

### Requirement: Envio pela API exige aprovação humana explícita

Toda chamada a `POST /api/prospeccao/{id}/enviar` DEVE recusar (422) quando
`por` (aprovado_por) estiver vazio ou ausente. Nenhum envio DEVE alcançar a
Evolution API sem esse valor.

#### Scenario: Envio sem operador identificado é recusado

- **WHEN** o popup de envio é submetido com o campo "aprovado por" vazio
- **THEN** a API responde 422 e nenhuma chamada à Evolution API acontece

### Requirement: Telefone e mensagem enviados são os que o operador viu

O envio DEVE usar o `telefone`/`mensagem` recebidos no corpo da
requisição — o que o operador reviu e pôde editar no popup — nunca lidos
diretamente de `prospeccoes.telefone` ou recalculados do template no
servidor no momento do envio.

#### Scenario: Edição no popup é o que é enviado

- **WHEN** o operador edita o texto da mensagem no popup antes de clicar
  "Enviar"
- **THEN** o texto que chega na Evolution API é o texto editado, não o
  gerado originalmente pelo template

### Requirement: Só um módulo do painel importa o transporte

`camucrm/painel/envio.py` DEVE ser o único arquivo em `camucrm/painel/`
que importa `camucrm.transport`, direta ou indiretamente por `from`.
Qualquer outro arquivo do painel que importar `camucrm.transport` DEVE
falhar o teste de checagem por AST.

#### Scenario: Novo módulo do painel não pode importar transport

- **WHEN** um módulo novo é adicionado a `camucrm/painel/` e importa
  `camucrm.transport`
- **THEN** o teste `tests/test_painel_api.py` falha, a menos que o módulo
  seja explicitamente adicionado à exceção nomeada

### Requirement: Falha de envio é reportada, não engolida

Quando a Evolution API recusar o envio ou estiver inacessível, a rota DEVE
responder com código de erro (502) e o detalhe do problema, e o popup DEVE
mostrar esse erro sem fechar nem descartar o texto editado.

#### Scenario: Evolution API fora do ar

- **WHEN** o operador clica "Enviar" e a Evolution API não responde
- **THEN** o popup mostra o erro, continua aberto, e o texto editado
  permanece nos campos

### Requirement: Resultado do envio é distinto da abertura do link

`prospeccoes.enviado_em`/`enviado_por` (confirmação de envio pela API)
DEVEM ser colunas distintas de `aberto_em`/`aberto_por` (clique no link
`wa.me`, já existente) — nunca a mesma coluna reaproveitada para os dois
significados.

#### Scenario: Abrir o link não marca como enviado

- **WHEN** o operador clica no link `wa.me` (sem usar o botão de envio pela
  API)
- **THEN** `aberto_em` é atualizado e `enviado_em` permanece como estava
