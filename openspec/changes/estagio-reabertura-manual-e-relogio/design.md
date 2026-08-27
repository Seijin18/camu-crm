## Context

`CLAUDE.md` fixa o invariante #2 ("estágio nunca regride",
`rules/estagio.py::transicao`) e `openspec/project.md` já registra a decisão
"recusa explícita é fechamento duro e não reabre por esta via [timeout]".
Este change introduz a primeira exceção formal a essa decisão — precisa ser
justificada por escrito, não silenciada, porque mexe num invariante e numa
decisão já documentada.

## Decisão: desconsideração é registrada, não apagamento do fato

O falso positivo de `recusa_explicita` não é corrigido apagando o fato
(`fatos.recusa_explicita = false`) nem regravando-o. Em vez disso, uma linha
nova em `correcoes` registra que aquele fato específico está sendo
**desconsiderado** para fins de decisão de estágio — o fato original
permanece intacto no histórico (§7: nada é apagado, tudo é corrigido com
rastro). `_derive_b2c`/`_derive_b2b` passam a consultar se existe uma
desconsideração ativa para aquele fato antes de tratá-lo como recusa
terminal.

Por que não apagar o fato: apagar destruiria a evidência de que a extração
errou naquele ponto — informação valiosa para `make eval` e para auditoria
futura do prompt. Desconsiderar preserva o "o que o LLM disse" e adiciona "o
que um humano decidiu sobre isso", que é exatamente o modelo já usado por
`correcoes` para outras divergências.

## Decisão: reabertura volta ao maior estágio já alcançado, nunca a S1/P0

Mesmo padrão de `reabrir()` (reabertura por timeout): a conversa reabre no
maior estágio historicamente alcançado antes da recusa, não em S1/P0.
Tratá-la como lead novo apagaria o progresso real que o cliente já tinha
demonstrado antes do falso positivo.

## Decisão: `reabrir()` valida a checagem sozinha, não confia no chamador

Hoje a garantia "recusa não reabre" vive inteiramente em `pipeline.py`, o
único chamador atual. Um chamador novo (painel, `acoes.py`) que esqueça de
replicar essa checagem reabriria uma recusa explícita real silenciosamente.
Este change move a checagem para dentro de `reabrir()` — a função recusa a
reabrir uma conversa cujo estado terminal veio de `recusa_explicita` que não
tenha uma desconsideração ativa, independente de quem chamou. Isso é o
mesmo princípio de "constraint estrutural, não confiar em disciplina do
chamador" já usado no invariante #3 do `CLAUDE.md` para o teto de
follow-ups.

## Decisão: comando de CLI dedicado, não `camucrm corrigir`

`camucrm corrigir` já existe para correções de classificação — mas
desconsiderar uma recusa não é "trocar um valor de campo", é uma decisão
com efeito estrutural sobre a máquina de estados (permite avanço que hoje é
proibido). Um comando nomeado explicitamente (ex. `camucrm desconsiderar-
recusa <conversa_id> --por <operador>`) deixa a intenção visível no
histórico de comandos e evita que "desconsiderar recusa" pareça uma correção
de rotina qualquer.

## Decisão: distinguir avanço causado por cliente vs. Camu como metadado formal

O mapa de "quem causou o avanço" já existe implicitamente em
`_derive_b2c`/`_derive_b2b` (cada transição sabe qual fato a disparou, e
cada fato tem uma direção conhecida — ver
`literalidade-e-idempotencia-da-extracao`, que formaliza a mesma direção
para fins de literalidade). Este change reusa esse mapa como um campo
explícito no `Transicao`/`Derivacao` (ex. `causada_por: "cliente" |
"camu"`), em vez de o classificador de temperatura tentar redescobrir a
direção por conta própria — uma segunda implementação da mesma regra
divergiria da primeira mais cedo ou mais tarde.

## Alternativa descartada

Permitir que qualquer correção via `camucrm corrigir` altere
`recusa_explicita` diretamente para `false` — rejeitada porque apagaria a
evidência de que a extração gerou um falso positivo ali, perdendo o sinal
que `make eval`/revisão de prompt precisaria para melhorar a mesma condição
no futuro.
