# Design — ingestão restrita por instância

## Decisão 1: restrição por instância (allowlist de instâncias restritas), não global

Considerado e descartado: aplicar a regra "só contato conhecido ou de
prospecção" a TODA ingestão, independente de instância. Foi rejeitado
explicitamente pelo usuário ao ser perguntado, porque quebraria o funil B2C:
hoje, um consumidor que nunca falou com a Camu manda DM pela primeira vez e
vira `contato`/`conversa` — é assim que a maioria dos leads B2C entra (§12,
"B2C: o cliente iniciou o contato — diligência pré-contratual"). Restringir
isso globalmente pararia de capturar lead novo por DM, o canal de aquisição
principal do funil.

A instância é o sinal certo para diferenciar os dois casos, porque é
METADADO de transporte (de qual número o evento chegou), não inferência de
conteúdo — continua dentro do que §1 permite decidir automaticamente.

**Allowlist do que é restrito, não do que é livre.** `CAMU_INSTANCIAS_
RESTRITAS` lista as instâncias que ganham a regra nova; qualquer instância
não listada (inclusive a instância única de hoje, sem nome nenhum
configurado) segue exatamente como antes. A alternativa (listar as
instâncias LIVRES, tudo mais restrito por padrão) foi descartada porque
inverteria o padrão de segurança: uma instalação de hoje, com uma instância
só e zero configuração nova, precisaria de uma ação explícita (nomear a
instância da Camu como livre) só para continuar funcionando como sempre —
regressão silenciosa para quem não tocar a variável nova. Com a allowlist do
restrito, ausência de configuração = comportamento de hoje, sempre.

## Decisão 2: telefone desconhecido não gera contato, e o payload cru é excluído após a decisão

**Revisão de 2026-08-27**, pedida explicitamente pelo usuário depois de ver a
primeira versão desta decisão — o raciocínio original está riscado abaixo,
mantido para não apagar o porquê da mudança.

O usuário pediu "ignorar totalmente" um telefone desconhecido numa instância
restrita — nenhum `contato`, nenhuma `conversa`, nada visível em lugar
nenhum do painel. Esta parte sempre foi direta e continua igual.

~~Zona cinzenta: `webhook._processar` grava o payload cru em
`eventos_recebidos_bruto` ANTES de chamar `ingerir()`... Decisão: o staging
continua incondicional.~~ A primeira versão manteve o staging para SEMPRE,
apoiada na política de retenção geral (12 meses) — mas isso deixava o
conteúdo da mensagem de um amigo/familiar acessível por meses num lugar
técnico, mesmo que invisível no painel. O usuário confirmou explicitamente:
**se o payload for excluído depois que a decisão de ignorar for tomada, não
tem problema ele ter passado pelo sistema.**

**Decisão corrigida**: `webhook._processar` continua gravando o payload
ANTES de chamar `ingerir()` — a garantia de durabilidade contra falha no
meio do caminho (`ingestao-a-prova-de-falha`) permanece intacta, porque o
staging ainda acontece incondicionalmente. O que muda é DEPOIS: quando
`ingerir()` devolve com sucesso (sem exceção) e o motivo foi especificamente
"instância restrita + telefone desconhecido"
(`ResultadoIngestao.ignorada_por_restricao_instancia=True`), `_processar`
chama `db.excluir_evento_bruto(evento_bruto_id)` — `DELETE` imediato, não
espera os dias de `purgar_eventos_brutos_antigos`. Qualquer outro motivo de
`ignorada=True` (evento que não é mensagem de conversa, ex. status de
conexão) continua marcado como processado e sujeito só à retenção padrão —
a exclusão imediata é específica do caso "gente sem relação nenhuma com a
Camu", não de todo evento ignorado.

Por que isso não abre mão de durabilidade: a exclusão só acontece DEPOIS de
`ingerir()` retornar sem exceção — ou seja, a decisão já foi tomada com
sucesso, e o payload staged já cumpriu o único papel que tinha (permitir
reprocessar se algo tivesse quebrado no meio). Se `ingerir()` lançar uma
exceção no meio do caminho (banco fora do ar, bug), o payload permanece
(`marcar_evento_bruto_falhou`, comportamento inalterado) — só a exclusão
some é a de um caminho que terminou com sucesso E com esse motivo
específico.

## Decisão 3: nome do campo `instance` no payload da Evolution API

**Verificado em produção em 2026-08-28** (`tasks.md`, item 7.2) — quando
escrita, esta decisão era uma suposição não testada; deixo o raciocínio
original abaixo porque ele continua valendo como princípio (o "e se eu
tivesse errado"), mesmo já confirmado.

O evento `messages.upsert` da Evolution API traz `instance` no nível raiz
do corpo do webhook (`{"event": ..., "instance": "nome-da-instancia",
"data": {...}}`). Confirmado contra tráfego real: instância `pessoal-
marcos` registrada, webhook apontado, mensagem de teste enviada — o log do
receptor mostrou a restrição disparando (`instância restrita 'pessoal-
marcos'`), e `webhook.py::_processar` só chega nesse ponto se `payload.get(
"instance")` já tiver extraído o nome certo. Sem divergência nenhuma do
formato assumido.

**Falha segura, princípio que continua valendo pra qualquer instância
futura não testada ainda**: se o campo viesse ausente ou com nome
diferente do esperado, `payload.get("instance")` devolveria `None`, e
`ingerir(..., instancia=None)` não aplicaria restrição nenhuma — o evento
seguiria pelo caminho de hoje (sem restrição), nunca descartado por
engano. Errar do lado "restringe menos do que devia" é reversível e
visível (contato indevido aparece no painel, um humano percebe e ajusta);
errar do lado "restringe mais do que devia" é o modo de falha caro
(mensagem de petshop de verdade some sem rastro). O padrão do parâmetro
protegia contra o pior caso, e não precisou ser acionado.

## Fluxo

```
Evolution API (instância "camu-pessoal", por exemplo)
    │  webhook POST, corpo com "instance": "camu-pessoal"
    ▼
webhook._processar(payload)
    │
    ├─ db.registrar_evento_bruto(payload)   ← incondicional, sempre (Decisão 2)
    │
    ├─ evento = transporte.receber(payload)  ← parsing de sempre, sem mudança
    │
    ▼
ingest.ingerir(db, evento, instancia=payload.get("instance"))
    │
    ├─ instancia in config.instancias_restritas()?  ─ não → segue como hoje
    │        │ sim
    │        ▼
    │   telefone já é contato OU está em prospeccoes?
    │        │ não → ResultadoIngestao(ignorada=True,
    │        │        ignorada_por_restricao_instancia=True), NADA gravado
    │        │ sim
    │        ▼
    ▼   segue o caminho de sempre (upsert_contato, get_or_create_conversa,
        registrar_mensagem, mesma transação de `ingestao-a-prova-de-falha`)

de volta em webhook._processar(payload):
    │
    ├─ resultado.ignorada_por_restricao_instancia?
    │        │ sim → db.excluir_evento_bruto(evento_bruto_id)  ← DELETE
    │        │        imediato (Decisão 2, revisada), retorna
    │        │ não
    │        ▼
    ▼   db.marcar_evento_bruto_processado(evento_bruto_id)  ← comportamento
        de sempre para qualquer outro caso (sucesso, duplicata, ignorado
        por outro motivo)
```

## `CAMU_INSTANCIAS_RESTRITAS`

```
CAMU_INSTANCIAS_RESTRITAS=camu-pessoal-marcos,camu-pessoal-felipe
```

CSV de nomes de instância, comparação exata (sem normalização de
maiúscula/minúscula — nome de instância da Evolution API é definido pelo
operador no cadastro dela, não texto de usuário). Vazio ou ausente = nenhuma
instância restrita, comportamento idêntico ao de antes deste change.
