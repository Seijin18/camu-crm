# Delta: backfill-seguro-para-reexecucao

## ADDED Requirements

### Requirement: Reimportar dump sem externa_id não duplica mensagem

`backfill.py::importar_conversas` DEVE gerar um `externa_id` sintético
estável (derivado de contato, texto e timestamp) para mensagens sem id de
origem, tornando a reimportação do mesmo dump idempotente.

#### Scenario: Reimportar o mesmo dump duas vezes não duplica

- **WHEN** o mesmo dump de histórico, sem `externa_id` nativo nas
  mensagens, é importado duas vezes
- **THEN** a segunda importação não cria mensagens duplicadas

### Requirement: Histórico grande não estoura numa chamada só

Um histórico de conversa longo DEVE ser dividido em blocos de tamanho
administrável antes de ser enviado ao LLM, preservando a ordem cronológica
entre blocos — nunca uma única chamada monolítica para todo o histórico.

#### Scenario: Histórico de 1000+ mensagens é processado em blocos

- **WHEN** um backfill processa uma conversa com mais de 1000 mensagens
- **THEN** a extração ocorre em múltiplas chamadas de LLM, cada uma dentro
  de um limite administrável de tamanho
- **AND** a ordem cronológica das mensagens é preservada entre os blocos

### Requirement: Ordem de leitura bate com enviada_em

O consumo de mensagens para extração (backfill) DEVE ordenar por
`enviada_em`, não apenas por `id` de inserção, quando os dois divergem.

#### Scenario: Mensagens fora de ordem de inserção são lidas cronologicamente

- **WHEN** um dump contém mensagens cujo `id` de inserção não corresponde
  à ordem real de `enviada_em`
- **THEN** a extração lê as mensagens na ordem de `enviada_em`, não na
  ordem de `id`

### Requirement: Trilha de backfill considera origem e destino

`pipeline.py::_trilha_de_backfill` DEVE verificar o par `(de, para)`, não
apenas `para`, ao decidir se uma transição já está registrada.

#### Scenario: Backfill reexecutado com funil trocado não confunde trilhas

- **WHEN** um backfill é reexecutado após o funil de uma conversa ter
  mudado, produzindo uma transição com o mesmo `para` mas `de` diferente da
  trilha já registrada
- **THEN** a nova trilha é tratada como distinta, não descartada como já
  registrada
