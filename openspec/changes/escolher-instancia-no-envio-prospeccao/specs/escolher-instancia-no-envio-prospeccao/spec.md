# Delta: escolher-instancia-no-envio-prospeccao

## ADDED Requirements

### Requirement: O popup lista as instâncias cadastradas na Evolution API

`GET /api/prospeccao/instancias` DEVE devolver os números cadastrados
consultados ao vivo na Evolution API (`fetchInstances`), cada um com nome e
estado de conexão. Quando a Evolution API estiver inacessível ou faltar
credencial no processo do painel, a rota DEVE responder 502 com o detalhe, e
o popup DEVE esconder o seletor em vez de bloquear o envio.

#### Scenario: Duas instâncias cadastradas

- **WHEN** o operador abre o popup "Enviar pela Evolution API" e há duas
  instâncias pareadas
- **THEN** o seletor "Enviar pelo número" mostra as duas, marcando qual está
  desconectada

#### Scenario: Evolution API fora do ar não impede o envio

- **WHEN** `GET /api/prospeccao/instancias` responde 502
- **THEN** o popup abre sem o seletor e o envio segue pela instância de
  `EVOLUTION_INSTANCE`

### Requirement: O número escolhido é por qual a mensagem sai

Quando o corpo de `POST /api/prospeccao/{id}/enviar` trouxer `instancia`
não-vazia, o envio DEVE usar essa instância da Evolution API, sobrepondo
`EVOLUTION_INSTANCE`. Corpo sem `instancia` (ou vazia) DEVE usar
`EVOLUTION_INSTANCE`, o comportamento anterior a este change.

#### Scenario: Envio pelo número pessoal

- **WHEN** o operador seleciona "pessoal-felipe" e clica "Enviar"
- **THEN** a chamada à Evolution API é feita contra a instância
  `pessoal-felipe`

### Requirement: A instância da tentativa é registrada

`prospeccoes.enviado_instancia` DEVE ser gravada com a instância usada tanto
quando o envio tem sucesso quanto quando falha — a tela precisa dizer por
qual número a última tentativa (bem-sucedida ou não) saiu.

#### Scenario: Falha mostra o número

- **WHEN** o envio pela instância `pessoal-marcos` falha porque o chip caiu
- **THEN** `enviado_instancia` fica `pessoal-marcos` e a linha da prospecção
  mostra "envio falhou pelo pessoal-marcos: ..."

### Requirement: Só um módulo do painel importa o transporte (mantido)

A consulta de instâncias e o repasse da instância escolhida DEVEM passar por
`camucrm/painel/envio.py` — que continua o único arquivo de
`camucrm/painel/` autorizado a importar `camucrm.transport`.

#### Scenario: A rota de listagem não importa transport direto

- **WHEN** `camucrm/painel/api.py` precisa da lista de instâncias
- **THEN** ele chama `envio.instancias_disponiveis()`, e o teste AST de
  `tests/test_painel_api.py` continua passando
