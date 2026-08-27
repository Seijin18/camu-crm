# Purga cobre rascunhos sem vínculo

## Why

Auditoria completa de `purgar_mensagens_antigas` (`db.py:878`) confirmou por
leitura direta que a purga NUNCA anonimiza rascunhos com `mensagem_id IS
NULL`. Qualquer rascunho gerado e nunca vinculado — a maioria dos casos
reais: gerado mas editado antes de enviar, ou nunca usado — sobrevive à
purga com texto pessoal em claro, contrariando exatamente o que a docstring
da função promete (§12: dado pessoal de conversa encerrada há mais de
`meses` deve ser anonimizado).

A mesma lacuna estrutural provavelmente se repete para qualquer outra tabela
derivada que hoje só é alcançada pela purga via `mensagem_id`/
`ultima_mensagem_id` — este change verifica e corrige `rascunhos` e
`resumos_conversa` (a segunda já tem seu próprio caminho de purga
implementado em `resumo-conversa`, mas é reconferida aqui como parte da
auditoria, para garantir que o mesmo padrão de bug não se repita ali).

## What Changes

- `db.py::purgar_mensagens_antigas`: estender a anonimização de `rascunhos`
  para alcançar TODOS os registros de conversas encerradas há mais de
  `meses`, via `conversa_id` direto — não apenas os que têm `mensagem_id`
  preenchido. Um rascunho com `mensagem_id IS NULL` de uma conversa
  encerrada há mais de `meses` tem `opcao_1`, `opcao_2` e `texto_final`
  anonimizados, exatamente como um rascunho vinculado.
- Reconferir `resumos_conversa`: o caminho de purga já implementado em
  `resumo-conversa` usa `ultima_mensagem_id` — confirmar que esse caminho
  também alcança resumos cuja conversa está encerrada há mais de `meses`
  independentemente de `ultima_mensagem_id` apontar para uma mensagem já
  purgada ou não. Se a mesma lacuna existir, corrigir aqui; se não, o teste
  de regressão desta seção documenta que já está correto.

## Impact

- Specs afetadas: `purga-cobre-rascunhos-sem-vinculo` (nova)
- Código alterado: `camucrm/db.py` (`purgar_mensagens_antigas`)
- Testes alterados: `tests/integration/` (teste que primeiro REPRODUZ o bug
  — rascunho nunca vinculado, de conversa velha e encerrada, sobrevive
  intacto antes da correção — e depois prova que a correção anonimiza;
  mesma verificação para `resumos_conversa`)
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- Qualquer mudança na definição de "conversa encerrada" ou no valor de
  `meses` — este change só estende o ALCANCE da anonimização já existente,
  não a política de quando purgar.
