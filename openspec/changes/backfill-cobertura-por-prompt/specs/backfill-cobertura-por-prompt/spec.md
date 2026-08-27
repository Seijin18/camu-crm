# Delta: backfill-cobertura-por-prompt

## ADDED Requirements

### Requirement: Cobertura é rastreada por versão de prompt

O sistema DEVE registrar, por conversa e por versão de prompt de extração,
até qual mensagem aquela versão já processou (`cobertura_extracao`). A
cobertura de uma versão NUNCA é lida como válida para outra versão,
mesmo quando uma é mais recente que a outra.

#### Scenario: Versões diferentes têm cobertura independente

- **WHEN** uma conversa é extraída sob o prompt versão "1" até a mensagem
  150, e depois a versão de prompt muda para "2"
- **THEN** a cobertura da versão "1" permanece em 150
- **AND** a cobertura da versão "2" começa vazia, independente do que a
  versão "1" alcançou

### Requirement: Cobertura nunca regride sob processamento concorrente

Gravar cobertura para um `(conversa_id, prompt_versao)` que já tem uma
cobertura registrada DEVE manter o maior `ultima_mensagem_id` entre o já
gravado e o novo valor, nunca substituir por um valor menor.

#### Scenario: Duas gravações fora de ordem não regridem o watermark

- **WHEN** dois processamentos da mesma conversa e versão de prompt gravam
  cobertura em ordem invertida (o de mensagem mais alta grava primeiro)
- **THEN** o valor final registrado é o maior dos dois, não o último
  gravado

### Requirement: Backfill não relê o que a versão de prompt atual já cobriu

Com `somente_desatualizados=True`, reprocessar uma conversa DEVE ler
apenas as mensagens posteriores à cobertura já registrada para a versão de
prompt atual, em vez de reler a conversa inteira, quando essa cobertura
existir.

#### Scenario: Segunda execução de backfill sob a mesma versão não chama o LLM

- **WHEN** `extrair_historico` é executado duas vezes seguidas, sob a
  mesma versão de prompt, sem mensagem nova na conversa entre as duas
  execuções
- **THEN** a segunda execução não realiza nenhuma chamada de LLM para
  aquela conversa

### Requirement: Primeira passada de uma versão de prompt nova sempre relê tudo

Quando não existe cobertura registrada para a versão de prompt atual numa
conversa, o comportamento é o de releitura total desde a primeira
mensagem, com a trilha de estágio reconstruída do estágio inicial do
funil — sem exceção, independente de cobertura existir para outra versão.

#### Scenario: Bump de versão de prompt força releitura completa

- **WHEN** a versão de prompt muda e uma conversa que já tinha cobertura
  sob a versão anterior é reprocessada com `somente_desatualizados=True`
- **THEN** a extração relê a conversa inteira, desde a primeira mensagem,
  sob a nova versão

### Requirement: Extração ao vivo alimenta a mesma cobertura que o backfill consulta

Toda extração bem-sucedida — pelo caminho ao vivo (webhook, `camucrm
extrair`) ou pelo caminho forçado (backfill) — DEVE registrar cobertura
para a versão de prompt usada, de forma que qualquer um dos dois caminhos
possa aproveitar o progresso feito pelo outro.

#### Scenario: Backfill não relê o que já foi extraído ao vivo

- **WHEN** uma conversa é extraída inteiramente pelo caminho ao vivo sob a
  versão de prompt atual, e em seguida `extrair_historico` roda sobre ela
  pela primeira vez com `somente_desatualizados=True`
- **THEN** nenhuma chamada de LLM é feita, porque a cobertura já reflete
  toda a conversa

### Requirement: Reprocessamento total continua disponível como opção explícita

Um operador DEVE poder forçar releitura total de uma conversa ou de toda a
base aberta, ignorando qualquer cobertura registrada, através de uma opção
explícita (`somente_desatualizados=False`).

#### Scenario: Opção explícita ignora cobertura existente

- **WHEN** `somente_desatualizados=False` é usado numa conversa que já tem
  cobertura completa para a versão de prompt atual
- **THEN** a extração relê a conversa inteira do mesmo jeito, chamando o
  LLM para todo o histórico

### Requirement: Ação de operador em uma conversa não muda de comportamento por padrão

`camucrm extrair --conversa X --forcar`, sem flag adicional, DEVE manter o
comportamento anterior a este change: releitura total, sem consultar
cobertura.

#### Scenario: Forçar uma conversa isolada continua relendo tudo por padrão

- **WHEN** o operador roda `camucrm extrair --conversa X --forcar` sem
  passar `--somente-desatualizados`
- **THEN** a conversa é relida por inteiro, como antes deste change
