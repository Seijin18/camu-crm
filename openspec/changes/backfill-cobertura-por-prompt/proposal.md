# Backfill não reprocessa o que a versão de prompt atual já cobriu

## Why

`extractor.py::processar_conversa` com `forcar=True` sempre relê a conversa
inteira desde a primeira mensagem (`desde=None`), ignorando
`ultima_mensagem_processada_id`. `backfill.py::extrair_historico` chama
`processar_conversa(..., forcar=True)` para **toda conversa aberta**
(`db.listar_conversas_abertas`), sem condição — é o único modo que
`extrair_historico` conhece.

Consequência medida (auditoria de custo de LLM, 2026-08-27,
`openspec/project.md`): **cada execução de `camucrm backfill --extrair` custa
proporcional à base inteira de conversas abertas, não ao que mudou desde a
última vez.** Rodar duas vezes custa duas vezes o mesmo total, mesmo que
nada tenha mudado entre as duas execuções.

Isso é exatamente o cenário que o `proposal.md` de
`backfill-seguro-para-reexecucao` já registrou como motivador: "importar um
dump atualizado, corrigir um erro e reimportar, rodar `make backfill` mais
de uma vez". Aquele change tornou a reexecução **segura** (idempotente —
não duplica mensagem nem objeção); este change ataca o lado que ficou de
fora: tornar a reexecução **barata** quando nada que justificaria uma nova
leitura mudou.

## Decisão de arquitetura (ver `design.md` para o raciocínio completo)

**Um watermark por versão de prompt, não um novo "resumo" da conversa.**
`conversas.ultima_mensagem_processada_id` já resolve exatamente este
problema para o caminho ao vivo — a lacuna é que ele não sabe **sob qual
versão de prompt** aquele watermark foi alcançado. A tabela nova
(`cobertura_extracao`) é o mesmo watermark, uma linha por
`(conversa_id, prompt_versao)`, com o mesmo cuidado de `GREATEST` que
`atualizar_estado_conversa` já aplica a `ultima_mensagem_processada_id` (e
pela mesma razão: processamento concorrente não pode regredir o watermark).

Isso preserva os dois invariantes que tornam `forcar=True` necessário hoje:
evidência continua exigindo o texto literal da mensagem (§1, invariante 1 —
nenhuma mensagem some, nenhum resumo substitui a evidência), e a primeira
vez que uma versão de prompt nova toca uma conversa continua relendo tudo
do zero, exatamente como hoje — o ganho é só na **segunda** execução em
diante, sob a **mesma** versão.

**Sem "grafo de conhecimento" novo.** A cobertura por versão de prompt é
todo o mecanismo necessário — nenhuma tabela nem tecnologia de grafo
adicional. `fatos`/`objecoes`/`eventos_estagio` já são o registro
estruturado que sobrevive à purga de mensagens (§12); este change só marca
"até onde, sob qual prompt" esse registro é válido.

**Trilha de estágio**: quando a cobertura permite pular a releitura, o
tratamento passa a ser o do caminho normal (não-forçado) — `estagio` de
referência vem do cache (`conversa.estagio`), e a derivação usa
`_avanco_ao_vivo`/`_trilha_de_backfill` conforme a `origem` já passada pelo
chamador, sem mudança nesses métodos. Só a decisão de "por onde começar a
ler" muda.

## What Changes

- **Schema (`camucrm/db.py`)**: nova tabela `cobertura_extracao
  (conversa_id, prompt_versao, ultima_mensagem_id, atualizado_em)`, chave
  primária `(conversa_id, prompt_versao)`. `Database.cobertura_extracao` (
  leitura) e `Database.registrar_cobertura_extracao` (upsert com
  `GREATEST`, mesmo padrão de `atualizar_estado_conversa`).
- **`camucrm/extraction/extractor.py`**: `processar_conversa` ganha
  `somente_desatualizados: bool = False`. Com `forcar=True` e
  `somente_desatualizados=True`, consulta a cobertura da versão de prompt
  atual antes de decidir `desde`/`estagio_referencia`:
  - sem cobertura para esta versão → comportamento de hoje, inalterado
    (releitura total, trilha do zero);
  - com cobertura → lê só a partir do watermark daquela versão, com
    `estagio_referencia = conversa.estagio` (caminho não-forçado).
  Todo bloco processado com sucesso grava sua cobertura (`prompt_versao`
  atual), nos dois caminhos — ao vivo e forçado — para que uma conversa já
  extraída ao vivo sob a versão corrente não seja relida na primeira
  passada de backfill que a alcançar.
- **`camucrm/backfill.py`**: `extrair_historico` ganha
  `somente_desatualizados: bool = True` (default barato) e repassa para
  `processar_conversa`.
- **`camucrm/cli.py`**: `camucrm backfill --extrair` ganha `--forcar-tudo`
  (passa `somente_desatualizados=False` — releitura total incondicional,
  para quando o operador genuinamente desconfia de um problema na mesma
  versão de prompt). `camucrm extrair --conversa X --forcar` continua sem
  mudança de comportamento por padrão (`somente_desatualizados=False`) —
  ganha a mesma flag como opção, não como novo padrão, porque é uma ação
  de operador único, de baixo volume, onde o custo do achado não se aplica.
- **Testes**: `tests/test_backfill.py` (nova classe) — rodar
  `extrair_historico` duas vezes sob a mesma versão de prompt não gera
  segunda chamada de LLM para uma conversa sem mensagem nova; bump de
  `PROMPT_VERSAO` força releitura total mesmo com cobertura da versão
  anterior; `--forcar-tudo`/`somente_desatualizados=False` sempre relê,
  cobertura ou não; objeção não duplica através de qualquer combinação
  disso (regressão sobre o que `backfill-seguro-para-reexecucao` já
  garante). Extensão de `tests/test_e2e.py` com o cenário "backfill
  reexecutado sob a mesma versão não chama o LLM de novo".

## Impact

- Specs afetadas: `backfill-cobertura-por-prompt` (nova)
- Schema alterado: `camucrm/db.py` (`SCHEMA`, tabela `cobertura_extracao`)
- Código alterado: `camucrm/db.py` (`Database.cobertura_extracao`,
  `Database.registrar_cobertura_extracao`), `camucrm/extraction/
  extractor.py` (`processar_conversa`), `camucrm/backfill.py`
  (`extrair_historico`), `camucrm/cli.py` (`cmd_backfill`, `cmd_extrair`,
  parser de argumentos)
- Testes alterados: `tests/test_backfill.py`, `tests/test_e2e.py`,
  `tests/fakes.py` (espelhar `cobertura_extracao` no `FakeDatabase`)
- Bloqueado por: nenhum (depende conceitualmente de
  `backfill-seguro-para-reexecucao`, já implementado — chunking e
  idempotência de objeção continuam intactos, este change não os toca)
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Cobertura parcial dentro de um bloco.** A granularidade é "até onde
  esta versão de prompt já leu", não "quais mensagens específicas dentro
  de um bloco de até 200". Um bloco é lido ou não é — não há meio-termo, e
  não precisa haver: o watermark já é a mesma unidade que
  `ultima_mensagem_processada_id` usa hoje.
- **Migrar `fatos` para carregar `prompt_versao` por linha.** Resolveria o
  mesmo problema por outro caminho (granularidade por fato, não por
  conversa), mas é uma mudança de schema maior para um ganho que o
  watermark por conversa já entrega. Fica como alternativa registrada, não
  como próximo passo.
- **Mudar o default de `camucrm extrair --forcar` (uma conversa).** Só
  `extrair_historico` (o caminho de custo alto, todas as conversas) ganha
  o novo default barato.
