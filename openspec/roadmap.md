---
atualizado: 2026-08-31
natureza: planejamento
---

# Roadmap — ordem recomendada de implementação

Este documento consolida todo item pendente conhecido (changes propostos,
tarefas operacionais sem código, e backlog registrado em `project.md`) numa
única ordem, com o porquê da posição de cada um. Não substitui
`project.md` (que continua sendo o registro de decisões e estado) nem os
`proposal.md` individuais — é a camada que falta acima deles: em que ordem
puxar o próximo item.

**Critério de ordenação:** dependência técnica primeiro (não dá pra pular),
depois risco/custo de não fazer agora, depois valor. Itens que dependem do
Marcos ficam marcados — são os únicos que não podem ser só "o Claude Code
faz".

---

## Onda 0 — bloqueadores, dependem do Marcos

Nenhum código impede o resto do roadmap de avançar em paralelo a estes
dois, mas sem eles duas partes do sistema continuam funcionando "no
escuro": sem saber se a extração está certa, e sem saber se a taxonomia
está calibrada. Ambos custam ~1-2h de trabalho humano, não são grandes.

1. **Rodar o eval de 30 conversas rotuladas (§7).** `ground-truth-no-painel`
   já dá o fluxo (rotular pelo painel, sem editar JSONL à mão) — falta
   alguém abrir `#/groundtruth/novo` e rotular. Até isso acontecer,
   `data/eval/conversas.jsonl` não existe de verdade e `make eval` não
   significa nada; a tela `/funciona` continua proibida de falar de
   acurácia (`project.md`, nota já registrada).
2. **Revisão da taxonomia/schema pelo Marcos (§13, passo 1).** Pendente
   desde a implementação inicial — não bloqueia código novo, mas qualquer
   ajuste de taxonomia encontrado agora é mais barato que depois de mais
   meses de dado acumulado sob o critério atual.

Não são changes OpenSpec — são tarefas operacionais. Não têm `proposal.md`
porque não mudam código.

---

## Onda 1 — dois itens pequenos, resolvem risco real, sem dependência

Nenhum dos dois exige decisão do Marcos nem é bloqueado por nada. Ordem
entre eles é indiferente; ambos são pequenos o bastante para caberem juntos
numa sessão.

3. **Agendar `camucrm purgar` (§12, LGPD).** O comando existe e é testado,
   mas nada o chama sozinho — depende de alguém lembrar de rodar
   manualmente. Sem `proposal.md` próprio (não é mudança de comportamento
   do sistema, é infraestrutura de operação — um cron/systemd timer
   chamando um comando que já existe). Se preferir tratar como change
   formal por tocar retenção de dado pessoal, é o próximo candidato a virar
   um.
4. **Arquivar `painel-leitura` corretamente.** `tasks.md` está com as
   caixas todas `[ ]` apesar de o painel estar em produção e seis outros
   changes terem sido construídos em cima dele — bookkeeping, não código.
   Marcar as tarefas batendo com o que existe e mover para o arquivo
   OpenSpec (`openspec archive`, ou o equivalente manual do projeto).

---

## Onda 2 — `painel-preserva-estado-em-refresh`

5. **`painel-preserva-estado-em-refresh`** (proposto, ver
   `openspec/changes/painel-preserva-estado-em-refresh/`). Entra antes de
   `prospeccao-filtro-e-ordenacao` por uma razão concreta: o novo campo
   `ordenar` que aquele change adiciona à aba Prospecção só sobrevive ao
   refresh automático se o mecanismo de persistência de filtro deste já
   existir. Implementar na ordem inversa não quebra nada — só significa que
   o `ordenar` novo herda o mesmo bug por um tempo, até este entrar.
   Prioridade alta apesar de ser só front-end: o achado mais sério (escrita
   em voo descartada silenciosamente — rascunho gerado que não aparece na
   tela) é o tipo de coisa que corrói confiança no painel sem deixar rastro
   nos logs.

---

## Onda 3 — `prospeccao-filtro-e-ordenacao`

6. **`prospeccao-filtro-e-ordenacao`** (proposto, ver
   `openspec/changes/prospeccao-filtro-e-ordenacao/`). Pequeno, isolado,
   sem tocar schema nem regra de negócio — só ordenação numa listagem que
   já existe. Depois da Onda 2 para o filtro novo já nascer persistente.

---

## Onda 4 — `midia-foto-pet`

7. **`midia-foto-pet`** — ainda sem `proposal.md`, é o maior item de valor
   pendente no sistema inteiro. **S2 (foto recebida) é o estágio-chave do
   funil B2C (§3)** e hoje só avança se o cliente escrever algo junto da
   foto — a extração não olha a mídia em si, então um cliente que manda só
   a foto, sem texto, nunca avança. Isso é uma lacuna estrutural na métrica
   que justifica o sistema (§14: `S1→S2`), não um detalhe de UI. Traz LGPD
   junto (§12: mídia é dado pessoal, retenção precisa de critério próprio)
   — por isso é capability separada, não um ajuste pontual em
   `extraction/`.
   - Depende logicamente da Onda 0 item 1 (eval): mudar o que aciona S2 é
     exatamente o tipo de mudança de critério que o eval existe para medir
     antes/depois (§7, "Falso positivo de avanço de estágio: 0"). Não é um
     bloqueio de implementação, mas rodar `midia-foto-pet` sem eval
     calibrado tira a única forma de saber se a mudança ajudou ou piorou.

---

## Onda 5 — backlog já identificado, sem `proposal.md` ainda

Nenhum destes tem push do usuário para virar change agora — ficam
registrados na ordem de risco/custo, para puxar quando fizer sentido.

8. **Reconciliação LID↔PN** (backlog de `identificacao-e-relogio-
   confiaveis`). Risco: contato fantasma ou histórico duplicado quando a
   Evolution API alterna entre os dois identificadores para o mesmo número.
   Sem sinal de estar acontecendo em produção ainda — motivo de não ter
   subido de prioridade sozinho.
9. **`editedMessage`/`protocolMessage` (REVOKE) ignorados.** Impacto é
   retenção maior que o ideal (mensagem apagada pelo cliente continua no
   CRM), não perda nem corrupção de dado — por isso fica atrás de tudo que
   toca corretude ou LGPD de forma mais direta.
10. **Payload em lote da Evolution API** — investigação (não
    implementação) já é tarefa dentro do escopo de `ingestao-a-prova-de-
    falha`, que está implementado; o que falta é confirmar contra
    documentação/comportamento real se vale desmembrar em change próprio.
    Baixo custo de verificar, então pode subir de prioridade se alguém
    tiver 30 minutos livres antes mesmo da Onda 4.

---

## Fora do roadmap (não é trabalho pendente)

- `prospeccao-b2b-shortlist`, `importacao-conversas-whatsapp`,
  `ingestao-restrita-por-instancia` e todos os outros 21 changes já
  implementados — não repetidos aqui, ver a tabela de estado em
  `project.md`.
- Os nove changes da auditoria de pipeline de 2026-08
  (`literalidade-e-idempotencia-da-extracao` e os demais listados em
  `project.md`, seção "Correções pendentes") — todos implementados; a
  tabela permanece em `project.md` como registro histórico de causa raiz,
  não como pendência.

---

## Resumo em uma linha por onda

| Onda | Item | Depende de |
|---|---|---|
| 0.1 | Rodar eval de 30 conversas | Marcos |
| 0.2 | Revisão de taxonomia/schema | Marcos |
| 1.3 | Agendar `camucrm purgar` | nada |
| 1.4 | Arquivar `painel-leitura` | nada |
| 2 | `painel-preserva-estado-em-refresh` | nada |
| 3 | `prospeccao-filtro-e-ordenacao` | Onda 2 (recomendado, não obrigatório) |
| 4 | `midia-foto-pet` | Onda 0.1 (recomendado, não obrigatório) |
| 5.8 | Reconciliação LID↔PN | nada |
| 5.9 | REVOKE (`editedMessage`/`protocolMessage`) | nada |
| 5.10 | Investigar payload em lote | nada |
