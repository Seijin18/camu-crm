# Rascunho gerado é registrado, não descartado

## Why

`drafts.py` gera duas opções, `cli.cmd_rascunho` imprime e tudo é descartado
— não existe registro de qual abordagem foi usada nem do resultado. Sem
isso, aprendizado agregado é impossível, com ou sem modelo. Esta tabela é o
destravamento real do change `analise-desempenho`: o resumo por LLM
(`resumo-conversa`) é conforto de leitura; `rascunhos` é o que torna A/B de
rascunho mensurável. Os dois changes são independentes em propósito —
`rascunhos` acumula valor no dia em que existe, sem depender do resumo.

## What Changes

- Tabela `rascunhos` nova em `SCHEMA` (`db.py:134-299`): contexto copiado no
  momento da geração (estágio, temperatura, funil, objeção,
  followups_enviados), as duas opções (`opcao_1`, `opcao_2`), avisos,
  `encerrar` + `motivo` (recusa de gerar é resposta legítima da §10, não
  ausência de dado), modelo, versão de prompt, escolha humana (`escolhida`,
  `texto_final`, `escolhido_em`, `escolhido_por`), vínculo `mensagem_id` +
  `estagio_no_envio`. `NULL` em `escolhida` é resultado (rascunho gerado mas
  ainda não usado), não lacuna de dado.
- Constraints:
  - `rascunhos_forma`: a linha tem as duas opções OU é recusa com `motivo`
    — nunca meia geração.
  - `rascunhos_escolha`: escolha registrada tem `escolhido_em`;
    `escolhida IS NULL` com `texto_final` preenchido é válido (humano
    escreveu do zero, sem usar nenhuma das duas opções).
  - Índice único parcial em `mensagem_id` (`WHERE mensagem_id IS NOT NULL`)
    — nunca duas reivindicações da mesma mensagem enviada.
- Três caminhos de preenchimento de `mensagem_id`, do mais confiável ao
  menos (ver `design.md`):
  1. `camucrm enviar --rascunho <id> --opcao {1,2}` (flags novas em
     `cmd_enviar`, `cli.py:156`) chama `db.vincular_rascunho` logo depois de
     `registrar_mensagem` devolver o id da mensagem. O painel mostra o
     comando pronto com o id ao lado do botão copiar.
  2. Reconciliação pelo eco da Evolution: `ingest.ingerir`, ao gravar uma
     mensagem `out`, procura rascunho da mesma conversa com `mensagem_id
     IS NULL`, gerado nas últimas 48h, cujo texto normalizado (strip,
     colapso de espaço, casefold) bate exatamente com o texto recebido. Sem
     fuzzy, sem LLM — se não casar exatamente, fica `NULL`.
  3. `POST /api/rascunhos/{id}/escolha` — registro manual da escolha, sem
     `mensagem_id` (o operador registra "usei a opção 1" sem que o sistema
     saiba qual mensagem concreta foi enviada).
- Extensão de `purgar_mensagens_antigas` (`db.py:878`): remove `opcao_1`,
  `opcao_2` e `texto_final` de `rascunhos` associados às mensagens purgadas
  (§12 — texto escrito para aquele cliente é conteúdo pessoal).
- `POST /api/rascunhos` (gera — custa cota de LLM, por isso nunca `GET`) e
  `POST /api/rascunhos/{id}/escolha` no painel.

## Impact

- Specs afetadas: `rascunho-registrado` (nova)
- Código alterado: `camucrm/db.py` (`SCHEMA`, `vincular_rascunho`, extensão
  de `purgar_mensagens_antigas`), `camucrm/cli.py` (`cmd_enviar` com
  `--rascunho`/`--opcao`), `camucrm/ingest.py` (reconciliação pelo eco),
  `camucrm/painel/api.py` (`POST /api/rascunhos`, `POST
  /api/rascunhos/{id}/escolha`), `camucrm/painel/views.py`,
  `camucrm/painel/static/*` (botão copiar + comando pronto)
- Testes alterados: `tests/test_rascunhos_registro.py` (novo),
  `tests/integration/` (novo teste de constraint), `tests/test_e2e.py`
  (estendido com `test_ciclo_ate_o_vinculo_do_rascunho`)
- Bloqueado por: `painel-leitura`
- Bloqueia: `analise-desempenho`
