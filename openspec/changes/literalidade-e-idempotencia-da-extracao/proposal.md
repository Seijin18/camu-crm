# Literalidade e idempotência da extração

## Why

Auditoria completa do pipeline (recepção → ingestão → extração → regras →
fila/rascunho → painel → purga), pedida pelo usuário antes de operar com
petshops e consumidores de verdade, encontrou quatro problemas confirmados
por leitura direta de código (não só relato de agente) em
`extraction/contract.py`, `extraction/extractor.py` e `db.py`, todos violando
diretamente o invariante #1 do `CLAUDE.md` ("todo `true` exige evidência
literal") ou o #2 ("estágio nunca regride" — via reprocessamento espúrio):

1. **`_fold`/`build_corpus` colapsa o separador `\n`.** O próprio docstring
   de `build_corpus` diz que o `\n` existe para impedir evidência que
   atravessa a fronteira de duas mensagens distintas. `_fold`
   (`re.sub(r"\s+", " ", ...)`) trata `\n` como qualquer outro espaço e o
   substitui por `" "` — reproduzido com um teste Python ad-hoc antes desta
   proposta. Um trecho que só existe porque o fim de uma mensagem se colou
   ao começo da próxima passa na conferência de literalidade como se fosse
   contíguo na conversa real.
2. **A conferência de literalidade não distingue quem falou.** Fatos que
   exigem fala do CLIENTE (`foto_pet_recebida`, `intencao_compra_explicita`,
   `recusa_explicita`, `autorizou_envio_material`, `visita_aceita`) podem ser
   confirmados por um trecho que veio de uma mensagem da Camu (pergunta ou
   script nosso), porque o teste de literalidade hoje só verifica se o
   trecho existe em algum lugar do corpus, não de qual lado da conversa.
3. **`ultima_mensagem_processada_id` é escrito sem `GREATEST`.** Sob
   concorrência real (webhook e `camucrm extrair` rodando ao mesmo tempo, ou
   dois webhooks quase simultâneos), o watermark de idempotência pode
   regredir e reapresentar ao LLM um bloco de mensagens já processado.
4. **`gravar_objecao` não tem nenhuma proteção de idempotência** — confirmado
   por leitura direta: sem `ON CONFLICT`, sem índice único. O reprocessamento
   causado pelo item 3, ou qualquer `forcar=True` (`camucrm extrair
   --forcar`, `make backfill` reexecutado), duplica a linha de objeção a
   cada rodada, poluindo permanentemente `distribuicao_objecoes` — a métrica
   que a §4 pede para revisão mensal.

Prioridade máxima entre os changes desta auditoria: cada dia de operação real
sem esta correção grava fatos e objeções potencialmente errados de forma
permanente, e `make recalcular` (a garantia central da divisão do §1 —
reprocessar sem custo de LLM) não desfaz um fato já gravado errado por falta
de evidência literal genuína.

## What Changes

- `extraction/contract.py::build_corpus`: trocar o separador de mensagem por
  um caractere que sobrevive a `_fold` sem virar espaço (ex. `"\x00"` em vez
  de `"\n"` puro) — a fronteira entre mensagens continua detectável depois
  da normalização.
- `extraction/contract.py` / `extractor.py`: mapear cada campo do contrato à
  direção exigida — CLIENTE (`foto_pet_recebida`, `intencao_compra_explicita`,
  `recusa_explicita`, `autorizou_envio_material`, `visita_aceita`) vs. CAMU
  (`preco_apresentado`, `previa_enviada`) — e verificar a evidência apenas
  contra o corpus do lado correto. Um trecho que só aparece do lado errado
  não valida o fato, mesmo que o texto exista literalmente na conversa.
- `db.py` (onde `ultima_mensagem_processada_id` é escrito): usar `GREATEST`,
  no mesmo padrão já usado para `ultimo_inbound`/`ultimo_outbound` — o
  watermark nunca anda para trás.
- `db.py::gravar_objecao`: idempotência real — índice único (ex.
  `(conversa_id, categoria, estagio, md5(coalesce(trecho, '')))`) com
  `ON CONFLICT DO NOTHING`, mesma família de solução já usada em `fatos`.

## Impact

- Specs afetadas: `literalidade-e-idempotencia-da-extracao` (nova)
- Código alterado: `camucrm/extraction/contract.py`,
  `camucrm/extraction/extractor.py`, `camucrm/db.py` (`gravar_objecao`,
  escrita de `ultima_mensagem_processada_id`, `SCHEMA` — índice único novo)
- Testes alterados: `tests/test_extraction.py` (ou equivalente já existente
  — fronteira de mensagem em `build_corpus`; evidência de lado errado não
  valida fato de cliente), `tests/integration/` (novo teste de constraint do
  índice único de `objecoes`), `tests/test_e2e.py` (não duplicar um E2E
  paralelo — estender com verificação de que reprocessamento concorrente não
  duplica `objecoes`)
- Bloqueado por: nenhum
- Bloqueia: `backfill-seguro-para-reexecucao` (a parte de duplicação de
  objeções em `--forcar` depende desta correção)

## Fora de escopo (decisão explícita)

- Chunking de histórico grande para o LLM — isso é `backfill-seguro-para-
  reexecucao`.
- Qualquer mudança em `rules/estagio.py` sobre como um fato já gravado afeta
  transição de estágio — este change só protege a gravação do fato/objeção,
  não a regra que os consome.
