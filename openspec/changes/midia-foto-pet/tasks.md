# Tasks — foto sem legenda avança S2

**Antes de começar a implementar:** confirmar com o Marcos que o risco
aceito na Decisão 3 do `design.md` ("qualquer imagem no DM B2C conta como
foto do pet, sem visão computacional") é aceitável — ver `proposal.md`,
"Bloqueado por". Recomendado (não obrigatório) esperar a onda 0.1 do
roadmap (eval de 30 conversas) para medir antes/depois.

## 1. Schema

- [ ] 1.1 `camucrm/db.py::SCHEMA`: `ALTER TABLE mensagens ADD COLUMN IF
      NOT EXISTS midia_tipo VARCHAR(16)` — mesmo padrão idempotente das
      colunas de `prospeccoes` já adicionadas depois da tabela original
      (→ Requirement "Nenhum binário de mídia é baixado ou persistido").

## 2. Transporte

- [ ] 2.1 `camucrm/transport/base.py`: `EventoRecebido` ganha campo
      `midia_tipo: str | None = None`.
- [ ] 2.2 `camucrm/transport/evolution.py`: `receber()` propaga
      `_tipo_de_midia(mensagem)` para `EventoRecebido.midia_tipo` (hoje
      `_tipo_de_midia` só decide o marcador textual e é descartado depois).
- [ ] 2.3 `camucrm/transport/evolution.py::_texto_da_mensagem`:
      `imageMessage`/`videoMessage`/`documentMessage` sem `caption` passam
      a devolver o marcador de tipo (`[imagem]`/`[vídeo]`/`[documento]`)
      em vez de `""` — consistência com `_MARCADORES`, visibilidade no
      painel. NÃO é o gatilho de `foto_pet_recebida` (isso é `midia_tipo`,
      não o texto).

## 3. Ingestão — fato determinístico

- [ ] 3.1 `camucrm/extraction/deterministico.py` (novo, puro, sem I/O,
      sem LLM): `fato_de_midia(midia_tipo, direcao, funil) -> str | None`
      — `"foto_pet_recebida"` quando `funil=B2C`, `direcao="in"`,
      `midia_tipo="image"`; `None` em qualquer outro caso (→ Requirement
      "Imagem sem legenda gera foto_pet_recebida deterministicamente";
      → Requirement "Gatilho restrito a B2C, imagem, mensagem do
      cliente").
- [ ] 3.2 `camucrm/db.py`: método novo (`gravar_fato_deterministico` ou
      nome equivalente) — grava em `fatos` reaproveitando
      `fatos_dedupe_idx` (idempotente, mesmo índice de hoje),
      `evidencia='[imagem]'` fixo, `mensagem_em` = `enviada_em` da
      mensagem que disparou.
- [ ] 3.3 `camucrm/ingest.py::ingerir`: depois de gravar a mensagem,
      chama `extraction.deterministico.fato_de_midia` com o `midia_tipo`
      do evento e, se devolver uma chave, chama o método novo de 3.2 —
      funciona com `extrair_ao_receber()` desligado ou LLM indisponível
      (→ Requirement "Extração via LLM desligada ou indisponível não
      impede o gatilho").

## 4. Testes

- [ ] 4.1 `tests/test_transport.py`: `midia_tipo` propagado em
      `EventoRecebido` para cada tipo de mídia; marcador de
      imagem/vídeo/documento sem legenda.
- [ ] 4.2 `tests/test_extraction_deterministico.py` (novo, sem banco,
      sem LLM — só a função pura): as três condições (funil, direção,
      tipo) testadas isoladamente, incluindo os casos negativos da
      Requirement "Gatilho restrito..." (B2B, saída, vídeo/documento).
- [ ] 4.3 `tests/test_ingest.py` (ou onde `ingerir()` já é testado):
      mensagem `in` B2C com `midia_tipo=image` grava o fato sem chamar
      LLM nenhum (mock/spy em `get_extrator` provando que não foi
      chamado); com legenda irrelevante ("oi") o fato é gravado do mesmo
      jeito.
- [ ] 4.4 `tests/test_e2e.py` (estender, não duplicar — `CLAUDE.md`):
      cenário de conversa B2C onde a única mensagem do cliente é uma foto
      sem legenda chega a `S2`.
- [ ] 4.5 `tests/test_db.py` (ou equivalente): purga de mensagens antigas
      remove `midia_tipo` junto, sem tratamento especial (→ Requirement
      "Coluna midia_tipo segue a mesma retenção de mensagens").
- [ ] 4.6 Suíte completa (`make test`) sem regressão.

## 5. Sincronização

- [ ] 5.1 Ao concluir, confirmar que a implementação bateu com o
      `proposal.md`/`design.md`; registrar aqui qualquer divergência.
