# Design — marcar prospecção como já enviada e como "não é WhatsApp"

## Envio manual reaproveita `enviado_*`, não ganha coluna própria

O change `envio-prospeccao-pela-evolution-api` já criou `enviado_em`/
`enviado_por`/`enviado_erro` e `escolher-instancia-no-envio-prospeccao`
somou `enviado_instancia`. "Marcar como já enviado" é o MESMO fato —
"esta linha foi contatada, tirar da fila" — só sem a confirmação da API.

Gravar com `enviado_instancia = 'manual'` deixa o registro auto-descritivo:
a tela distingue "enviado às HH:MM pelo pessoal-felipe" (API) de "marcado
como já enviado" (manual) só olhando esse campo, sem uma quarta coluna
booleana redundante. `views.prospeccao_para_json` expõe `enviado_manual`
derivado — o front não precisa conhecer o valor sentinela.

Consequência: o "Desfazer 'já enviado'" precisa ser cuidadoso. Ele limpa as
quatro colunas `enviado_*`, mas o `UPDATE` carrega
`WHERE ... AND enviado_instancia = 'manual'` — um clique errado nunca apaga
o registro de um envio real que a Evolution API confirmou.

## `nao_whatsapp` ganha coluna própria — a linha não pode sumir

Diferente do envio manual, "não é número de WhatsApp" não cabe em nenhuma
coluna existente e precisa SOBREVIVER à reimportação da planilha. O
`INSERT ... ON CONFLICT DO UPDATE` de `importar_prospeccoes` não lista
`nao_whatsapp` no `SET`, então reimportar a mesma planilha atualiza nome/
nota/bairro e preserva a marca — testado em
`test_nao_whatsapp_sobrevive_a_reimportacao_da_planilha`.

A linha continua em `listar_prospeccoes` (nenhum filtro novo) — a tela é
que esconde os botões de disparo e mostra o selo. Manter a linha visível é
de propósito: o operador precisa ver que aquele petshop foi triado, não que
ele sumiu.

## Rotas fora de `envio.py`, sem "enviar" no path

Nenhuma das duas rotas toca `camucrm.transport` — são escrita pura em
`prospeccoes`, como `/abrir`. Ficam em `api.py` direto, sem passar por
`envio.py`. Os paths são `/enviada-manual` e `/nao-whatsapp`: nenhum contém
a substring `enviar`, então o teste-guarda
`test_apenas_o_path_de_envio_de_prospeccao_contem_enviar` continua com um
único path no conjunto. Um teste novo
(`test_rotas_de_marca_manual_nao_contem_enviar_no_path`) fixa isso de
propósito.

## `por` obrigatório

Toda ação manual do painel é rastreável (`marcos_manuais.por`,
`correcoes.por`, `enviar` exige `aprovado_por`). As duas rotas recusam com
422 quando `por` vem vazio, antes de tocar o banco — mesma disciplina.
