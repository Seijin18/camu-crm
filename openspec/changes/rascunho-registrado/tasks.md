# Tasks — rascunho gerado é registrado, não descartado

## 1. Implementação — schema

- [ ] 1.1 `camucrm/db.py`: tabela `rascunhos` em `SCHEMA` (`db.py:134-299`)
      — colunas de contexto copiado, duas opções, avisos, `encerrar`/
      `motivo`, modelo, `prompt_versao`, escolha, `mensagem_id`,
      `estagio_no_envio` (→ Requirement "Rascunho gerado é persistido com as
      duas opções").
- [ ] 1.2 `camucrm/db.py`: constraint `rascunhos_forma` (duas opções OU
      recusa com motivo) (→ Requirement "Rascunho gerado é persistido com as
      duas opções").
- [ ] 1.3 `camucrm/db.py`: constraint `rascunhos_escolha` (escolha com
      momento; `escolhida IS NULL` + `texto_final` válido) (→ Requirement
      "Opção não escolhida não é descartada").
- [ ] 1.4 `camucrm/db.py`: índice único parcial em `mensagem_id`
      (`WHERE mensagem_id IS NOT NULL`) (→ Requirement "Um mensagem_id não é
      reivindicado por mais de um rascunho").
- [ ] 1.5 `camucrm/db.py`: `vincular_rascunho(rascunho_id, mensagem_id,
      estagio_no_envio)`.
- [ ] 1.6 `camucrm/db.py`: estender `purgar_mensagens_antigas` (`db.py:878`)
      para apagar `opcao_1`/`opcao_2`/`texto_final` de `rascunhos`
      associados às mensagens purgadas (→ Requirement "Purga remove texto de
      rascunho").

## 2. Implementação — caminhos de vínculo

- [ ] 2.1 `camucrm/cli.py`: `cmd_enviar` (`cli.py:156`) ganha `--rascunho
      <id> --opcao {1,2}`; depois de `registrar_mensagem` devolver o id,
      chama `db.vincular_rascunho` (→ Requirement "Vínculo por flag na
      CLI").
- [ ] 2.2 `camucrm/ingest.py`: ao gravar mensagem `out`, procurar rascunho
      da mesma conversa, `mensagem_id IS NULL`, últimas 48h, texto
      normalizado (strip + colapso de espaço + casefold) igual; sem match,
      não faz nada (→ Requirement "Reconciliação pelo eco não usa casamento
      aproximado").
- [ ] 2.3 `camucrm/painel/api.py`: `POST /api/rascunhos` (gera via
      `drafts.gerar`, persiste contexto copiado); `POST
      /api/rascunhos/{id}/escolha` (registro manual, sem `mensagem_id`).
- [ ] 2.4 `camucrm/painel/static/*`: botão copiar mostra ao lado o comando
      `camucrm enviar --rascunho <id> --opcao N` pronto.

## 3. Testes

- [ ] 3.1 `tests/test_rascunhos_registro.py`: escolha nos três caminhos;
      `_normalizar` vincula texto exato e não vincula texto editado
      (asserção sobre a ausência de vínculo) (→ Requirement "Vínculo por
      flag na CLI"; → Requirement "Reconciliação pelo eco não usa casamento
      aproximado").
- [ ] 3.2 `tests/integration/`: `rascunhos_forma`/`rascunhos_escolha`
      recusam linha meia-preenchida; índice único parcial de `mensagem_id`;
      purga remove texto de rascunho (→ Requirement "Um mensagem_id não é
      reivindicado por mais de um rascunho"; → Requirement "Purga remove
      texto de rascunho").
- [ ] 3.3 `tests/test_e2e.py` (estender, não duplicar):
      `test_ciclo_ate_o_vinculo_do_rascunho` — gera → escolhe → registra
      outbound → reconcilia → afirma `mensagem_id`; inbound de resposta →
      extração → S5, com o rascunho vinculado ao evento que veio depois
      dele.
- [ ] 3.4 Suíte completa verde (unitária sem Postgres; integração à parte
      com Postgres).
