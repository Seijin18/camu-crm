# Tasks — importação de conversas via exportação do WhatsApp

## 1. Parser

- [ ] 1.1 `camucrm/whatsapp_export.py`: dataclass `ParseResultado` (nome do
      contato inferido, `mensagens: list[dict]` no formato de
      `backfill.importar_conversas`, `midia_preservada: int`,
      `ignoradas: list[str]`) e função `parse(texto: str, *, nosso_nome:
      str) -> ParseResultado` (→ Requirement "Parser puro, sem I/O").
- [ ] 1.2 Reconhecer as duas variantes de linha (`DD/MM/AA, HH:MM - Nome:
      texto` e `[DD/MM/AA, HH:MM:SS] Nome: texto`), com `DD/MM/AA` e
      `DD/MM/AAAA`.
- [ ] 1.3 Linha de continuação (não bate com o padrão de 1.2) é anexada com
      `\n` à mensagem anterior, nunca vira mensagem própria.
- [ ] 1.4 Lista de placeholders de mídia reconhecidos → mensagem sem texto
      preservada, mesmo contrato de `mensagem-sem-texto-preservada` (→
      Requirement "Mensagem sem texto (mídia) é preservada").
- [ ] 1.5 Lista de linhas de sistema reconhecidas (criptografia, mensagem
      apagada, mudança de número) → ignoradas, contadas, não viram
      mensagem nem erro.
- [ ] 1.6 Linha que não bate com nenhum padrão de 1.2–1.5 → conta em
      `ignoradas` com o texto original (→ Requirement "Linha não
      reconhecida é reportada").
- [ ] 1.7 Detecção de grupo (mais de 2 remetentes distintos nas linhas
      reconhecidas como mensagem, ou linha de sistema de
      entrada/saída de participante) → `parse` levanta erro dedicado,
      nenhuma mensagem parcial retornada (→ Requirement "Exportação de
      grupo é rejeitada por inteiro").
- [ ] 1.8 `nosso_nome` não encontrado em nenhuma linha reconhecida →
      `parse` levanta erro dedicado (→ Requirement "Direção exige
      correspondência de nome").

## 2. Rota de importação (painel)

- [ ] 2.1 `POST /api/importacao-whatsapp` em `camucrm/painel/api.py`:
      `UploadFile` + campos de formulário (`telefone`, `tipo`,
      `nome_operador`, `nome?`, `origem?`). Lê em memória
      (`arquivo.file.read()`), nunca grava em disco (→ Requirement
      "Upload não persiste o arquivo bruto em disco").
- [ ] 2.2 Chama `whatsapp_export.parse`, monta `registro` e chama
      `backfill.importar_conversas(db, [registro])` — sem duplicar lógica
      de idempotência/`externa_id` sintético, que já existe em
      `backfill.py`.
- [ ] 2.3 Erro de `parse` (grupo detectado, nome sem correspondência) vira
      resposta HTTP de erro explícita, com a mesma mensagem do parser —
      nunca um "importado com sucesso" mascarando falha.
- [ ] 2.4 Resposta inclui `conversa_id` (via `db.upsert_contato` +
      `db.get_or_create_conversa`, mesmas chamadas que
      `importar_conversas` já faz internamente) para habilitar o botão de
      extração no painel.
- [ ] 2.5 `POST /api/importacao-whatsapp/{conversa_id}/extrair`: chama
      `Extrator(db, criar_llm()).processar_conversa(conversa_id,
      origem=ORIGEM_BACKFILL, forcar=True)` — passo separado do upload (→
      Requirement "Extração é passo separado do upload" e "Importação
      reaproveita origem='backfill'").

## 3. Painel — views/front

- [ ] 3.1 `camucrm/painel/views.py`: função de serialização do resumo de
      importação (mensagens novas, mídia preservada, ignoradas com
      amostra do texto) para JSON.
- [ ] 3.2 Aba nova "Importar conversa (fora do número Camu)" em
      `camucrm/painel/static/*`: formulário (arquivo, telefone, tipo,
      nome_operador, nome/origem opcionais), relatório do resultado, botão
      "extrair" habilitado após upload bem-sucedido.
- [ ] 3.3 Nunca aparece fundida com kanban/fila/lista de conversas —
      confirmar que nenhuma rota existente foi tocada para incluir este
      fluxo.

## 4. Testes

- [ ] 4.1 `tests/test_whatsapp_export.py` (sem DB, sem LLM — só o parser):
      as duas variantes de linha; continuação de mensagem; mídia
      preservada; linha de sistema ignorada; linha desconhecida reportada;
      grupo detectado e rejeitado; `nosso_nome` sem correspondência
      rejeitado.
- [ ] 4.2 Extensão de `tests/test_painel_api.py`: upload feliz cria
      mensagens e retorna resumo; upload de grupo retorna erro sem gravar
      nada; upload sem `nosso_nome` correspondente retorna erro sem
      gravar nada; nenhum arquivo aparece em disco após upload; rota de
      extração dispara `Extrator` só quando chamada explicitamente (upload
      sozinho não chama LLM).
- [ ] 4.3 Teste de reimportação: importar o mesmo `.txt` duas vezes não
      duplica mensagem (reaproveita idempotência de `externa_id` sintético
      de `backfill.py`).
- [ ] 4.4 Teste de métrica: evento de estágio gerado por esta importação
      não entra no cálculo de duração média de `metrics.py` (→ Requirement
      "Importação reaproveita origem='backfill'").
- [ ] 4.5 Suíte completa verde (`make test`).

## 5. Sincronização (antes de arquivar)

- [ ] 5.1 Conferir que a implementação não introduziu nenhuma mudança em
      `camucrm/db.py` (schema) nem em `camucrm/rules/` — se precisou,
      atualizar `design.md`/`proposal.md` explicando a divergência antes
      de arquivar.
- [ ] 5.2 Nota em `openspec/project.md` (tabela "Estado da implementação")
      quando o change for concluído.
