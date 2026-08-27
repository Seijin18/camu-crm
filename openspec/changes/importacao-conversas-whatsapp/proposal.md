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

**Reaproveitar a máquina de backfill inteira, sem tocar em schema nem em
`rules/`.** `eventos_estagio.em` já é gravado como o momento do
*processamento* (`agora`), não da mensagem — verdade tanto para o backfill
original quanto para esta importação, porque as duas rodam pela mesma
`trilha()` de `rules/estagio.py`, que deriva estágio final a partir dos
fatos acumulados, não replay mensagem-a-mensagem. Por isso a importação usa
`origem='backfill'` sem criar um terceiro valor: a garantia que já existe
("backfill fica fora de métrica de tempo") é exatamente a garantia que esta
importação precisa, não uma aproximação dela. `conversas.ultimo_inbound`/
`ultimo_outbound` continuam vindo do timestamp real de cada mensagem
(`registrar_mensagem`), então temperatura e fila enxergam a data real da
última troca, não a data do upload.

Consequência prática: **nenhuma mudança em `camucrm/db.py` (schema),
`camucrm/rules/`, nem no CHECK de `eventos_estagio.origem`.** O trabalho
novo é só: (1) um parser puro do `.txt` exportado → mesmo formato de
`registro` que `importar_conversas` já consome; (2) uma rota no painel que
faz upload → parse → `importar_conversas` → opcionalmente
`Extrator.processar_conversa(..., origem=ORIGEM_BACKFILL, forcar=True)` na
conversa afetada.

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
- `POST /api/importacao-whatsapp/{conversa_id}/extrair` (painel): dispara
  `Extrator.processar_conversa(conversa_id, origem=ORIGEM_BACKFILL,
  forcar=True)` para a conversa recém-importada — passo separado do
  upload, mesma divisão que `camucrm backfill --arquivo` / `--extrair` já
  tem na CLI, porque é a chamada de LLM (pode demorar) e o operador deve
  poder revisar o resumo da importação antes de gastar uma chamada.
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
  (`importar_conversas`), `camucrm/extraction/extractor.py` (`Extrator`),
  `camucrm/rules/estagio.py` (`ORIGEM_BACKFILL`, `trilha`)
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
