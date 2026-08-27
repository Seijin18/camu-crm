# Extração em lote por janela — não uma chamada de LLM por mensagem

## Why

`webhook.py::_processar` chama `_extrair(conversa_id)` a cada evento
recebido, sem agrupar. `Extrator.processar_conversa` já lê quantas
mensagens novas estiverem pendentes numa chamada só (`mensagens_novas`), o
mecanismo de lote já existe — mas ele nunca tem chance de agir, porque a
extração dispara antes que uma segunda mensagem tenha chance de chegar.

Medido na auditoria de custo de LLM (2026-08-27, `openspec/project.md`):
`system_prompt()` da extração é ~737 tokens fixos; uma chamada com 1
mensagem nova carrega ~65 tokens de conteúdo real — 11:1 de overhead por
chamada. Mensagens fragmentadas do WhatsApp (cliente manda 3-4 seguidas em
segundos) viram 3-4 chamadas de ~800 tokens cada, em vez de uma chamada de
~900 cobrindo as quatro.

## Decisão de arquitetura (ver `design.md` para o raciocínio completo)

**A temperatura não depende de extração — só o estágio depende, e o
estágio já é carimbado retroativamente.** `rules/temperatura.py::classificar`
usa sinais (`horas_desde_inbound`, `dias_sem_resposta`, `bola_com`) que
vêm direto dos timestamps de `mensagens` (`pipeline.carregar_sinais`),
recalculados em TODO ingest, LLM nenhum envolvido. E quando a extração roda
em lote, cada evento de estágio é carimbado com o momento da mensagem que
o evidenciou (`pipeline.momentos_de_estagio`), não o momento do
processamento. **Atrasar a extração custa só "o operador vê o estágio
atualizado um pouco depois" — nunca fila fria por engano, nunca métrica de
tempo distorcida.** Isso é o que abre espaço para agrupar sem violar a
premissa "a fila é o produto" que hoje justifica disparar a cada evento.

**Gatilho híbrido (contagem OU espera), sem infraestrutura nova.** Webhook
continua chamando `_extrair` a cada evento — o que muda é o que `_extrair`
faz: antes de chamar o LLM, pergunta se vale a pena AGORA ou se pode
esperar o próximo `camucrm extrair` (cron já existente, `make extrair`).
Extrai imediatamente se `mensagens_desde` (já existe, usado por
`resumo_vigente`) atingir um limiar, OU se a mensagem pendente mais antiga
já espera além de um teto — os dois calculáveis a partir de dados que já
existem (`mensagens`, `ultima_mensagem_processada_id`), sem tabela nova,
sem timer em memória por processo (que se perde a cada redeploy — o
oposto do "recalculável a partir do estado gravado" que
`ultima_mensagem_processada_id` já exemplifica). Abaixo dos dois limiares,
o evento fica pendente e o próximo `make extrair` (cron externo,
recomendado a cada poucos minutos — ver `design.md`) processa o lote
inteiro numa chamada só.

**Botão manual complementa, não substitui.** `POST
/conversas/{id}/extrair` no painel chama a extração na hora,
incondicionalmente — para quando o operador quer o estágio atualizado
agora, sem esperar limiar nem cron. Mesmo padrão de `/rascunho`/`/resumo`
(POST, nunca GET, gasta cota).

## What Changes

- **`camucrm/db.py`**: `Database.primeira_mensagem_pendente_em(conversa_id,
  desde_id) -> datetime | None` — `MIN(enviada_em)` das mensagens
  pendentes, para medir há quanto tempo a mais antiga espera.
- **`camucrm/webhook.py`**: `_extrair(conversa_id)` passa a consultar
  `_deve_extrair_agora` antes de chamar `processar_conversa`. Dois limiares
  configuráveis por ambiente, mesma convenção de `CAMU_EXTRAIR_AO_RECEBER`:
  - `CAMU_EXTRACAO_LIMIAR_MENSAGENS` (default 6): mensagens pendentes
    suficientes disparam na hora, sem esperar o teto de tempo.
  - `CAMU_EXTRACAO_TETO_ESPERA_MINUTOS` (default 3): mensagem pendente mais
    antiga além deste tempo dispara na hora, mesmo sem atingir o limiar de
    contagem.
  Abaixo dos dois, `_extrair` retorna sem chamar o LLM — a mensagem
  continua pendente (`ultima_mensagem_processada_id` não avança) para o
  próximo `camucrm extrair` processar.
- **`camucrm/painel/api.py`**: `POST /conversas/{id}/extrair` — dispara
  `Extrator.processar_conversa` incondicionalmente (sem gatilho), mesmo
  padrão de autenticação/erro de `/rascunho` e `/resumo`.
- **`camucrm/painel/static/*`**: botão "Extrair agora" na conversa, mesmo
  padrão visual de "Gerar resumo".
- **Operação**: recomendação (documentada em `design.md`, não em código)
  de configurar `make extrair` num cron externo a cada poucos minutos —
  ele passa a ser o mecanismo que garante que uma conversa que nunca
  atinge os limiares (a maioria, no volume real) ainda é processada em
  tempo razoável.
- **Testes**: `tests/test_webhook.py` (gatilho híbrido — contagem, espera,
  os dois abaixo do limiar não extrai), extensão de
  `tests/test_painel_api.py` (nova rota).

## Impact

- Specs afetadas: `extracao-em-lote-por-janela` (nova)
- Código alterado: `camucrm/db.py`, `camucrm/webhook.py`,
  `camucrm/painel/api.py`, `camucrm/painel/static/app.js`
- Testes alterados: `tests/test_webhook.py` (inclui atualizar
  `test_falha_na_extracao_nao_propaga` para configurar o mock do novo
  gatilho), `tests/test_painel_api.py`, `tests/fakes.py`
- Bloqueado por: nenhum
- Bloqueia: nenhum

## Fora de escopo (decisão explícita)

- **Timer em memória por conversa.** Descartado no design (ver
  `design.md`) — não sobrevive a redeploy, e o gatilho híbrido resolve o
  mesmo problema sem ele.
- **Mudar o default de `CAMU_EXTRAIR_AO_RECEBER`.** Continua ligado por
  padrão; o que muda é o que acontece quando está ligado, não se está.
- **Configurar o cron externo dentro deste repositório.** `make extrair`
  já existe como comando; agendá-lo (crontab, systemd timer) é decisão de
  operação, documentada como recomendação, não como código deste change.
- **Painel mostrar "N mensagens aguardando extração".** Ficaria natural
  depois deste change, mas é tela nova, não parte do mecanismo — candidato
  futuro, não escondido, só não incluído aqui.
