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

## Decisão 2: telefone desconhecido não gera contato — mas o payload cru continua sendo staged

O usuário pediu "ignorar totalmente" um telefone desconhecido numa instância
restrita — nenhum `contato`, nenhuma `conversa`, nada visível em lugar
nenhum do painel. Esta parte é direta.

Zona cinzenta: `webhook._processar` grava o payload cru em
`eventos_recebidos_bruto` ANTES de chamar `ingerir()` (change
`ingestao-a-prova-de-falha`, para durabilidade — nenhum evento se perde
mesmo se o processamento falhar no meio). Aplicar a decisão "ignorar
totalmente" a esse staging também significaria mover o corte de instância/
telefone para ANTES do staging — perdendo a garantia de durabilidade
justamente para o caminho que mais precisa dela testado (é código novo).

**Decisão: o staging continua incondicional.** `eventos_recebidos_bruto` já
tem política de retenção e purga própria (§12, `purga-cobre-rascunhos-sem-
vinculo` e a retenção geral de 12 meses) — não é uma segunda cópia
permanente e visível da mensagem, é staging técnico de curta vida para
reprocessamento em caso de falha. "Ignorar totalmente" nesta proposta
significa: **nenhum `contato`, `conversa` ou `mensagem` é criado, e nada
aparece em nenhuma tela do painel** — a garantia que resolve o problema real
(poluir kanban/fila com contato de amigo/família). Registrado aqui como
decisão explícita, não escondida — se o usuário quiser ir além (nem staging
para instância restrita + telefone desconhecido), é extensão futura,
reabrindo a garantia de durabilidade só para esse caso específico.

## Decisão 3: nome do campo `instance` no payload da Evolution API

Assumido, não verificado contra o payload real das duas instâncias novas
(ainda não registradas): o evento `messages.upsert` da Evolution API traz
`instance` no nível raiz do corpo do webhook (`{"event": ..., "instance":
"nome-da-instancia", "data": {...}}`) — formato documentado da Evolution
API, e não uma suposição sem base, mas também não testado contra tráfego
real desta integração especificamente.

**Falha segura**: se o campo vier ausente ou com nome diferente do
esperado, `payload.get("instance")` devolve `None`, e `ingerir(...,
instancia=None)` não aplica restrição nenhuma — o evento segue pelo caminho
de hoje (sem restrição), nunca é descartado por engano. Errar aqui do lado
"restringe menos do que devia" é reversível e visível (contato indevido
aparece no painel, um humano percebe e ajusta); errar do lado "restringe
mais do que devia" é o modo de falha caro (mensagem de petshop de verdade
some sem rastro). O padrão do parâmetro protege contra o pior caso.

Quando as instâncias forem registradas de verdade, vale confirmar o nome
exato do campo contra um payload real antes de configurar `CAMU_INSTANCIAS_
RESTRITAS` em produção — ver task dedicada em `tasks.md`.

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
    │        │ não → ResultadoIngestao(ignorada=True), NADA gravado
    │        │ sim
    │        ▼
    ▼   segue o caminho de sempre (upsert_contato, get_or_create_conversa,
        registrar_mensagem, mesma transação de `ingestao-a-prova-de-falha`)
```

## `CAMU_INSTANCIAS_RESTRITAS`

```
CAMU_INSTANCIAS_RESTRITAS=camu-pessoal-marcos,camu-pessoal-felipe
```

CSV de nomes de instância, comparação exata (sem normalização de
maiúscula/minúscula — nome de instância da Evolution API é definido pelo
operador no cadastro dela, não texto de usuário). Vazio ou ausente = nenhuma
instância restrita, comportamento idêntico ao de antes deste change.
