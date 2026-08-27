# Importação de conversas via exportação do WhatsApp

## Why

O contato com clientes/petshops não acontece mais exclusivamente pelo número
da Camu ligado à Evolution API — parte das conversas roda por número pessoal
ou outro número comercial, fora do webhook (`camucrm/webhook.py`) que hoje é
a única porta de entrada de mensagem. Essas conversas ficam invisíveis para
o CRM: sem `fatos`, sem estágio, sem entrar na fila de follow-up, mesmo
sendo conversa real em andamento.

O WhatsApp já resolve a exportação (`Exportar conversa` → arquivo `.txt`).
Falta o lado de importação: transformar esse `.txt` no mesmo formato que
`camucrm/backfill.py::importar_conversas` já aceita, e deixar o operador
disparar isso pelo painel.

## Decisão de arquitetura (ver `design.md` para o raciocínio completo)

**Extração usa `origem='live'`, não `'backfill'` — o `.txt` do WhatsApp
carrega timestamp real por mensagem, e `pipeline.py` só descarta timestamp
real (`em=None`, fora de métrica de tempo) para `origem='backfill'`; para
`'live'`, `eventos_estagio.em` recebe o momento real do fato
(`fatos.mensagem_em`, já gravado independente de origem). Descartar isso
para um dado que é real seria perder informação sem necessidade — e
excluiria pra sempre do §14 exatamente as conversas que motivaram este
change.** Consequência prática: a extração reaproveita a rota que **já
existe**, `POST /conversas/{conversa_id}/extrair` (change
`extracao-em-lote-por-janela`), sem tocar nela — nenhuma rota nova de
extração, e reimportar a mesma conversa depois processa só o bloco novo
(mesmo comportamento incremental de uma conversa alimentada por webhook).

Consequência prática: **nenhuma mudança em `camucrm/db.py` (schema),
`camucrm/rules/`, nem em `camucrm/pipeline.py`.** O trabalho novo é só:
(1) um parser puro do `.txt` exportado → mesmo formato de `registro` que
`importar_conversas` já consome; (2) uma rota no painel que faz upload →
parse → `backfill.importar_conversas` (reaproveitado sem mudança). A
extração em si é a rota de extração que já existe, chamada pelo painel
depois do upload — nenhum código novo para isso.

## What Changes

- `camucrm/whatsapp_export.py` (novo, sem I/O, sem LLM, sem DB): parser puro
  do formato de texto exportado pelo WhatsApp (`DD/MM/AA, HH:MM - Nome:
  texto`, variante `[DD/MM/AA, HH:MM:SS] Nome: texto`) para uma lista de
  `{"direcao", "texto", "enviada_em"}`, dado o nome que representa "nosso
  lado" na exportação. Junta linha de continuação (mensagem multi-linha) à
  mensagem anterior; reconhece placeholders de mídia (`<Mídia oculta>`,
  variantes em inglês) e os preserva como mensagem sem texto, mesmo
  tratamento de `mensagem-sem-texto-preservada`, não descarta; linha de
  aviso de sistema (criptografia, "mensagem apagada") é ignorada e contada,
  nunca vira mensagem. Linha que não bate com nenhum padrão reconhecido é
  reportada, nunca descartada em silêncio.
- `POST /api/importacao-whatsapp` (painel): multipart — arquivo `.txt`,
  `telefone`, `tipo` (b2b|b2c), `nome_operador` (nome que aparece na
  exportação do lado da Camu/do rep), `nome` e `origem` opcionais. Faz
  parse em memória (nunca grava o `.txt` bruto em disco — mesmo padrão do
  upload de CSV de `prospeccao-b2b-shortlist`), monta o `registro` e chama
  `backfill.importar_conversas`. Retorna resumo (mensagens novas, mídia
  preservada, linhas não reconhecidas) — nunca um número só de "sucesso".
- Extração continua sendo um passo separado do upload — mas usa a rota que
  **já existe**, `POST /conversas/{conversa_id}/extrair`, sem nenhuma rota
  nova: o operador revisa o resumo do parse primeiro (é a chamada de LLM
  que pode demorar/custar), depois clica "extrair" e o painel chama essa
  mesma rota, com o `conversa_id` devolvido pela importação.
- Painel — aba nova "Importar conversa (fora do número Camu)": formulário
  de upload + campos acima, relatório do resultado, botão "extrair" pós-
  importação. Nunca aparece fundida com kanban/fila/conversas.
- **Validação de direção obrigatória**: se `nome_operador` não aparece em
  nenhuma linha reconhecida do arquivo, a importação é recusada com erro
  explícito (nunca importa tudo como uma direção só por falha silenciosa de
  correspondência de nome).

## Impact

- Specs afetadas: `importacao-conversas-whatsapp` (nova)
- Código novo: `camucrm/whatsapp_export.py`
- Código alterado: `camucrm/painel/api.py`, `camucrm/painel/views.py`,
  `camucrm/painel/static/*`
- Código reaproveitado, sem alteração: `camucrm/backfill.py`
  (`importar_conversas`), rota já existente `POST
  /conversas/{conversa_id}/extrair` em `camucrm/painel/api.py` (change
  `extracao-em-lote-por-janela`)
- Testes novos: `tests/test_whatsapp_export.py` (parser puro, sem DB/LLM),
  extensão de `tests/test_painel_api.py`
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Comando CLI dedicado.** O disparo é só pelo painel (decisão do
  usuário). Backfill em lote via CLI continua existindo para dump JSON
  (`camucrm backfill`), mas não ganha um modo `.txt` do WhatsApp — se
  aparecer necessidade de importar em escala/scriptado, é uma extensão
  futura, não parte desta.
- **Grupos do WhatsApp.** O parser cobre só exportação de conversa 1:1 —
  formato de grupo tem linha de entrada/saída de participante e múltiplos
  remetentes, que não mapeiam para o modelo `contato` (uma pessoa/empresa
  por conversa). Arquivo de grupo é rejeitado com erro explícito, não
  importado parcialmente.
- **`editedMessage`/mensagem apagada (`protocolMessage`).** Mesma exceção já
  registrada em `project.md` para o caminho do webhook — o `.txt` exportado
  mostra "Esta mensagem foi apagada" como texto, tratado aqui como
  mensagem de sistema (ignorada, contada), não como fato de negócio.
- **Reconciliação com mensagem já recebida pelo webhook.** Se a mesma troca
  aparecer nos dois caminhos (ex.: rep usou número pessoal por um dia,
  depois voltou pro número Camu), pode duplicar mensagem com texto igual e
  `externa_id` sintético diferente — não há forma de deduplicar sem um id
  de mensagem real do WhatsApp no `.txt` exportado (ele não existe nesse
  formato). Risco aceito e registrado, não escondido; mitigação futura, se
  o volume justificar, é comparação por texto+timestamp aproximado.
