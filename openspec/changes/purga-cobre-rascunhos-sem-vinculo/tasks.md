# Tasks — purga cobre rascunhos sem vínculo

## 1. Reprodução do bug (antes de corrigir)

- [x] 1.1 `tests/integration/`: reproduzir o cenário — rascunho com
      `mensagem_id IS NULL`, de conversa encerrada há mais de `meses`,
      sobrevive à purga com `opcao_1`/`opcao_2`/`texto_final` intactos
      ANTES da correção (→ Requirement "Purga alcança rascunho sem
      vínculo").

## 2. Implementação

- [x] 2.1 `camucrm/db.py::purgar_mensagens_antigas`: estender a
      anonimização de `rascunhos` para alcançar todo registro de conversa
      encerrada há mais de `meses` via `conversa_id` direto, não só via
      `mensagem_id` (→ Requirement "Purga alcança rascunho sem vínculo").
- [x] 2.2 Reconferir `resumos_conversa`: confirmar (ou corrigir, se a
      mesma lacuna existir) que a purga alcança resumos de conversa
      encerrada independentemente de `ultima_mensagem_id` (→ Requirement
      "Purga de resumo não depende de mensagem_id apontar para linha
      purgada").

## 3. Testes

- [x] 3.1 `tests/integration/`: depois da correção, o mesmo cenário do item
      1.1 agora anonimiza `opcao_1`/`opcao_2`/`texto_final` do rascunho sem
      vínculo (→ Requirement "Purga alcança rascunho sem vínculo").
- [x] 3.2 `tests/integration/`: a linha do rascunho em si (contexto,
      escolha, timestamps) NÃO é removida pela purga — só o texto é
      anonimizado (→ Requirement "Purga alcança rascunho sem vínculo").
- [x] 3.3 `tests/integration/`: resumo de conversa encerrada é alcançado
      pela purga independentemente do valor de `ultima_mensagem_id` (→
      Requirement "Purga de resumo não depende de mensagem_id apontar para
      linha purgada").
- [x] 3.4 Suíte de integração verde (Postgres real).
