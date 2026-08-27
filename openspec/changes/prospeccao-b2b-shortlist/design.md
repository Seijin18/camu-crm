# Design — lista de prospecção B2B

## Decisão 1: base legal para a lista raspada

§12 do documento é explícito: nenhuma base legal hoje cobre "lista fria
comprada ou raspada". A planilha do usuário (`camu-petshops-shortlist.csv`)
é exatamente isso — levantada de fontes públicas (Google Maps, sites), sem
que o petshop tenha iniciado contato.

Decisão, tomada com o usuário: **legítimo interesse (art. 10, LGPD)** cobre
este caso especificamente porque (a) é **pessoa jurídica**, não consumidor;
(b) é **contato comercial B2B** (proposta de parceria de consignação, não
oferta a um indivíduo); (c) os dados usados (nome do estabelecimento,
telefone comercial público, endereço) são os mesmos que qualquer diretório
comercial público já expõe.

**O que isso NÃO cobre**: qualquer lista de consumidor (B2C) raspada
continua sem base nenhuma — esta capability só existe para o funil B2B, e
`prospeccoes` não tem (e não deve ganhar) um campo `funil` que aceite B2C.
Se um dia o usuário quiser importar uma lista de consumidores, isso é uma
decisão nova, com uma base legal própria a resolver — não uma extensão
silenciosa desta.

Registrado em `openspec/project.md`, seção "Decisões que divergem ou
estendem o documento" — não é uma exceção escondida.

## Decisão 2: link do WhatsApp, não envio pela API

Duas formas de "disparo por clique" foram consideradas:

1. **Envio real via Evolution API** — o painel chamaria
   `transport.enviar(...)`, exigindo `EVOLUTION_API_BASE_URL`/`_API_KEY`/
   `_INSTANCE` no processo do painel (hoje ausentes de propósito) e uma
   rota de envio nova. Reverteria a garantia testada em todos os 6 changes
   anteriores do painel (`camucrm.painel` nunca importa `camucrm.transport`,
   `test_nao_existe_rota_de_envio`).
2. **Link `https://api.whatsapp.com/send/?phone=...&text=...`** — o clique
   abre o link (nova aba/app do WhatsApp) com o número e a mensagem já
   preenchidos; o humano aperta "enviar" dentro do próprio WhatsApp Web ou
   app.

**Escolhida: 2.** Zero mudança na superfície de segurança do painel, zero
credencial nova, e satisfaz o pedido original ("disparo acionado por clique
do usuário") — o clique é o que abre a composição da mensagem; o envio em
si continua sendo um ato humano dentro do WhatsApp, exatamente como o
`camucrm enviar` de hoje continua sendo o único caminho de envio programático
do sistema.

Consequência prática: **`prospeccoes` não precisa de `externa_id`,
`aprovado_por` nem qualquer coisa do contrato de `transport/base.py`** — não
é um envio que o sistema audita como tendo acontecido; é um link que o
sistema oferece. Se o operador quiser rastrear que abriu o link, isso é o
campo `aberto_em` (ver schema), preenchido quando o botão é clicado — é
"intenção registrada", não "envio confirmado".

## Schema

```sql
CREATE TABLE IF NOT EXISTS prospeccoes (
    id              SERIAL PRIMARY KEY,
    nome            VARCHAR(200) NOT NULL,
    telefone        VARCHAR(32) NOT NULL,
    telefone_hash   VARCHAR(64) NOT NULL UNIQUE,
    bairro          VARCHAR(120),
    zona            VARCHAR(60),
    nota            NUMERIC(2,1),
    avaliacoes      INTEGER,
    site            VARCHAR(300),
    tier_origem     VARCHAR(8),
    status_origem   VARCHAR(60),
    aberto_em       TIMESTAMP WITH TIME ZONE,
    aberto_por      VARCHAR(48),
    criado_em       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    atualizado_em   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prospeccoes_zona_idx ON prospeccoes (zona, bairro);
```

`telefone_hash` é o mesmo `db.hash_telefone` já usado em `contatos` —
reaproveitado, não reimplementado, para que a detecção de conversão
(comparar hash contra hash) seja exata.

## Detecção de conversão (shortlist → conversa real)

Sem coluna própria, sem job de sincronização: `listar_prospeccoes(...)` faz

```sql
SELECT p.*, c.id AS contato_id
  FROM prospeccoes p
  LEFT JOIN contatos c ON c.telefone_hash = p.telefone_hash
 ...
```

Se `contato_id` não é nulo, a linha já é uma conversa de verdade — a tela
mostra um link para `#/conversas/{conversa_aberta_do_contato}` em vez do
botão de WhatsApp. Isso está sempre correto no momento da leitura, porque
`contatos.telefone_hash` já existe e já é a fonte de verdade — não há
estado intermediário para divergir.

## Template da mensagem

Arquivo `docs/mensagem-prospeccao.md`, mesmo padrão de `docs/playbook-tom.md`
(texto editável fora do código, path configurável por env var, ex.
`CAMU_MENSAGEM_PROSPECCAO`). Placeholder único: `{nome}` — substituído pelo
nome curto do petshop (mesma lógica de corte em `|`/` - ` do pseudocódigo
original do usuário). **Não é LLM** — substituição de string simples, para
não abrir uma quarta superfície de modelo (CLAUDE.md fixa três: extração,
rascunho, resumo).

## `ingest.ingerir` e o tipo do contato na conversão

Hoje todo contato novo nasce `tipo_padrao=B2C` (`ingest.py`, decisão
deliberada contra inferência automática de negócio). Para uma resposta que
vem de um telefone que já está em `prospeccoes`, o tipo default passa a ser
B2B — não é inferência de conteúdo de conversa (o que §1 proíbe), é uso de
uma origem já curada explicitamente como B2B pelo próprio operador ao
importar a planilha. Implementação: antes de `db.upsert_contato`, `ingerir`
consulta `db.prospeccao_por_telefone_hash(hash)`; se existir, passa
`tipo_padrao=B2B` em vez do default do chamador.
