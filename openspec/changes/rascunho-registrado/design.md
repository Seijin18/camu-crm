## Context

O painel deliberadamente não envia mensagem (decisão do usuário, ver
`painel-leitura`), então o vínculo "qual rascunho gerou qual mensagem
enviada de fato" não pode nascer de uma rota de envio no painel — precisa
vir de outro lugar.

## Decisão: três caminhos de vínculo, confiabilidade decrescente

1. **Flag na CLI** (`camucrm enviar --rascunho <id> --opcao {1,2}`): o
   operador diz explicitamente qual rascunho usou no momento do envio. É o
   caminho mais confiável porque a intenção é declarada, não inferida.
2. **Reconciliação por texto exato no eco**: quando o envio não passa pela
   flag, `ingest.ingerir` tenta casar o texto da mensagem `out` recebida de
   volta da Evolution com um rascunho pendente da mesma conversa, por
   igualdade exata após normalização (strip, colapso de espaço, casefold).
   Sem fuzzy matching, sem LLM.
3. **Registro manual** (`POST /api/rascunhos/{id}/escolha` sem
   `mensagem_id`): o operador diz "usei a opção 1" sem que o sistema saiba
   qual mensagem concreta corresponde.

Honestidade sobre o alcance de cada caminho: o caminho 3 sozinho só mede
"opção 1 foi escolhida em X% das vezes" — mede o prompt, não a conversão.
Medir "avançou de S1 para S2 em N% das vezes que a opção 1 foi usada" exige
o caminho 1 ou 2, porque só eles ligam o rascunho a uma mensagem real na
timeline da conversa.

## Risco aceito: reconciliação é heurística

Mensagem editada no momento do envio nunca vincula automaticamente pelo
caminho 2 — o texto normalizado não bate. Decisão explícita: nunca
"melhorar" a reconciliação com casamento aproximado. Um vínculo errado
envenena a análise agregada em silêncio, de um jeito que ninguém percebe até
o número já estar errado há semanas. Preferir `NULL` (rascunho sem vínculo)
a adivinhar.

## Alternativa descartada

Exigir sempre a flag da CLI (caminho 1) e não implementar reconciliação —
rejeitada porque a maior parte dos envios reais hoje não passa por
`cmd_enviar` com a flag nova até o hábito operacional mudar. Sem o caminho
2, a tabela `rascunhos` ficaria maiormente sem vínculo por um tempo longo
demais para ser útil.
