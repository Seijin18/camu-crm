# Delta: ground-truth-no-painel

## ADDED Requirements

### Requirement: Validação de rótulo tem um único lugar de verdade

`dataset.validar_entrada(bruto, onde) -> ConversaRotulada` DEVE ser o único
lugar do sistema onde uma entrada de ground truth é validada — tanto
`carregar()` (lendo do arquivo) quanto as rotas do painel DEVEM chamar essa
função, nunca reimplementar a regra de validação.

#### Scenario: Rota do painel rejeita o mesmo erro que carregar() rejeitaria

- **WHEN** uma entrada com estágio fora da taxonomia, objeção fora da
  lista, ou fato fora do contrato é enviada via `POST /eval/rotulos`
- **THEN** a rota rejeita com o mesmo tipo de erro que `dataset.carregar`
  lançaria para a mesma entrada malformada no arquivo

### Requirement: Testes nunca tocam o dataset real

Toda leitura/escrita de dataset de ground truth em teste DEVE usar
`CAMU_EVAL_DATASET` apontando para um arquivo temporário — nenhum teste
DEVE ler ou escrever `data/eval/conversas.jsonl` real.

#### Scenario: Suíte de testes usa arquivo temporário

- **WHEN** a suíte de testes exercita qualquer rota de `/api/eval`
- **THEN** `CAMU_EVAL_DATASET` aponta para um arquivo temporário criado
  pelo próprio teste, nunca para o arquivo real do projeto

### Requirement: Criar entrada a partir de conversa real puxa as mensagens

`POST /eval/rotulos` com `conversa_id` DEVE preencher `mensagens[]`
automaticamente a partir de `db.listar_mensagens_registradas(conversa_id)`,
sem exigir que o operador digite a transcrição.

#### Scenario: Entrada criada a partir de conversa_id tem as mensagens reais

- **WHEN** `POST /eval/rotulos` é chamado com `conversa_id` de uma conversa
  existente
- **THEN** a entrada gravada tem `mensagens[]` idêntico ao histórico real
  dessa conversa

### Requirement: Entrada malformada nunca corrompe o arquivo

Uma tentativa de gravar uma entrada que falha em `dataset.validar_entrada`
NÃO DEVE alterar `data/eval/conversas.jsonl` — o arquivo permanece no
estado anterior à tentativa.

#### Scenario: Entrada inválida não é gravada

- **WHEN** `POST /eval/rotulos` recebe uma entrada com um fato fora do
  contrato
- **THEN** a rota recusa a gravação
- **AND** o arquivo do dataset permanece sem a entrada inválida

### Requirement: Detalhe de entrada é editável

`PUT /eval/rotulos/{id}` DEVE revalidar e atualizar o rótulo preservando o
`id` original. `DELETE /eval/rotulos/{id}` DEVE remover a entrada
correspondente.

#### Scenario: Editar preserva o id

- **WHEN** `PUT /eval/rotulos/{id}` atualiza o rótulo de uma entrada
  existente
- **THEN** a entrada resultante mantém o mesmo `id`

#### Scenario: Excluir remove a entrada

- **WHEN** `DELETE /eval/rotulos/{id}` é chamado para uma entrada existente
- **THEN** essa entrada deixa de existir no dataset

### Requirement: Status do dataset reflete completude real

`GET /eval/status` DEVE reportar `completo: false` quando o dataset tem
menos de `TAMANHO_MINIMO` entradas, e `completo: true` a partir desse
tamanho, junto com os avisos de `avisos_de_tamanho()`.

#### Scenario: Dataset abaixo do mínimo reporta incompleto

- **WHEN** o dataset tem menos de `TAMANHO_MINIMO` entradas
- **THEN** `GET /eval/status` reporta `completo: false`

#### Scenario: Dataset no mínimo ou acima reporta completo

- **WHEN** o dataset tem `TAMANHO_MINIMO` entradas ou mais
- **THEN** `GET /eval/status` reporta `completo: true`

### Requirement: Rodar eval abaixo do tamanho mínimo é estruturalmente recusado

`POST /eval/rodar` DEVE recusar com 422 quando `len(dataset) <
TAMANHO_MINIMO` — a restrição é imposta pelo código, não apenas prometida
na documentação.

#### Scenario: Rodar eval abaixo do mínimo falha com 422

- **WHEN** `POST /eval/rodar` é chamado com um dataset com menos de
  `TAMANHO_MINIMO` entradas
- **THEN** a resposta é 422 e nenhuma chamada ao LLM é feita

### Requirement: Tela /o-que-funciona só afirma acurácia com eval disponível

`GET /api/o-que-funciona` DEVE incluir o bloco "Acurácia de extração (§7)"
apenas quando `GET /eval/resultado` tem um cache disponível. Sem cache
disponível, a tela DEVE manter o texto de restrição já registrado em
`project.md`.

#### Scenario: Sem resultado cacheado, tela mantém a restrição

- **WHEN** nenhum resultado de eval foi cacheado ainda
- **THEN** `/o-que-funciona` não afirma nada sobre acurácia de extração,
  mostrando o texto de restrição

#### Scenario: Com resultado cacheado, tela mostra o bloco de acurácia

- **WHEN** um resultado de eval está cacheado em
  `data/eval/ultimo_resultado.json`
- **THEN** `/o-que-funciona` mostra fatos/objeção/falsos-positivos contra
  as metas, com `rodado_em`
