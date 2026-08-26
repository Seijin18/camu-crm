---
atualizado: 2026-08-26
natureza: referencia
responsavel: Felipe
---

# CRM de conversas — definições

**Destino no repositório:** `04-operacao/`
**Contexto:** complementa o sistema de atendimento via LLM já em construção (Evolution API), que hoje lê e categoriza contatos mas não tem as definições de funil, temperatura e follow-up.

---

## 0. Onde está o custo real

Com o Claude Code construindo, tempo de implementação deixa de ser a restrição. Ela se desloca para dois lugares:

**O que não dá para refazer depois.** Código se reescreve numa tarde. Taxonomia mal desenhada contamina todo o histórico, e conversa que passou sem ser instrumentada não volta — não existe como saber, retroativamente, *quando* um lead mudou de estágio. Por isso a ordem abaixo começa por schema e taxonomia, e a primeira coisa a rodar é o backfill.

**Atenção diária.** A saída do sistema é uma fila que alguém precisa abrir. Se a fila não for aberta por 5 dias úteis seguidos, o problema não é o sistema — e nenhuma feature conserta isso.

---

## 1. Divisão de responsabilidade: LLM extrai, regra decide

Esta é a definição estruturante. Sem ela o sistema vira caixa-preta que ninguém confia.

| Camada | Responsável | Por quê |
|---|---|---|
| Extrair **fatos** da conversa | LLM | Linguagem natural é o problema dele |
| Derivar **estágio** dos fatos | Regra determinística | Precisa ser replayable e auditável |
| Calcular **temperatura** | Regra determinística | Depende de tempo e reciprocidade, não de texto |
| **Priorizar** a fila | Regra determinística | Política de negócio, não inferência |
| **Rascunhar** resposta | LLM | Com o humano escolhendo entre 2 opções |
| **Enviar** | Humano | Sempre |

O LLM nunca decide estágio, temperatura, prioridade ou envio. Ele responde perguntas factuais fechadas.

**Consequência prática:** se as regras mudarem, basta reprocessar os fatos já extraídos — sem custo de LLM e sem reinterpretar conversa antiga. Se o LLM decidisse estágio, cada mudança de critério exigiria reprocessar tudo, com resultado diferente a cada vez.

---

## 2. Contrato de extração

Saída estrita por conversa, a cada bloco de mensagens novas:

```json
{
  "foto_pet_recebida": false,
  "preco_apresentado": false,
  "previa_enviada": false,
  "intencao_compra_explicita": false,
  "recusa_explicita": false,
  "autorizou_envio_material": false,
  "visita_aceita": false,
  "objecao": null,
  "evidencias": {
    "foto_pet_recebida": null,
    "objecao": null
  }
}
```

### Regras do contrato

**Todo `true` exige evidência.** Trecho literal da conversa em `evidencias`. Sem evidência, o campo volta a `false`. É o que impede o modelo de avançar estágio por otimismo — o modo de falha mais comum e mais caro, porque um lead marcado como quente sem ter sido nunca é revisitado.

**Campos são fechados e binários.** Nada de escala de 0 a 10, nada de campo livre exceto o texto da evidência.

**`objecao` só de uma lista fechada** (seção 4). Fora da lista, `outro` com o trecho.

**Idempotência.** Guardar `ultima_mensagem_processada_id` por conversa; reprocessar não pode duplicar evento nem regredir estágio.

**Custo.** Processar delta com resumo rolante da conversa, não histórico completo a cada chamada.

---

## 3. Estágios

Estágio é fato observável. A regra de avanço precisa ser inequívoca, ou o dado vira opinião.

### Funil B2C (DM)

| # | Estágio | Avança quando | Fato-gatilho |
|---|---|---|---|
| S0 | Lead | Conversa existe | — |
| S1 | Respondeu | Mensagem espontânea do cliente | — |
| S2 | **Foto recebida** | Cliente mandou foto do pet | `foto_pet_recebida` |
| S3 | Prévia enviada | Camu mostrou o resultado | `previa_enviada` |
| S4 | Preço apresentado | Valor + frete informados | `preco_apresentado` |
| S5 | Negociação | Respondeu ao preço sem recusar | — |
| S6 | Ganho | Pagamento confirmado | manual |
| SX | Perdido | Recusa explícita ou 14 dias sem resposta | `recusa_explicita` / timeout |

**S2 é o estágio-chave.** Quem manda a foto do pet já se comprometeu. S1→S2 mede a abordagem; S4→S6 mede preço e frete. São problemas diferentes com soluções diferentes, e hoje estão somados num número só.

### Funil B2B (petshop)

| # | Estágio | Avança quando | Fato-gatilho |
|---|---|---|---|
| P0 | Não abordado | Está na shortlist | — |
| P1 | Msg 1 enviada | — | — |
| P2 | Autorizou | Respondeu ao "posso mandar uma foto?" | `autorizou_envio_material` |
| P3 | Proposta apresentada | Msg 2 entregue | — |
| P4 | Visita agendada | Data marcada | `visita_aceita` |
| P5 | Consignação assinada | Termo em duas vias | manual |
| P6 | **Primeira reposição** | Vendeu e pediu mais | manual |
| PX | Descartado | Recusa ou 2 follow-ups sem retorno | — |

**P6 é a única validação real.** P5 prova que o lojista aceitou algo de graça; P6 prova que o produto vende. A métrica que importa é P5→P6, não a contagem de consignações assinadas.

**Regra geral:** estágio nunca regride. Cliente que volta atrás gera objeção, não retrocesso — senão o histórico deixa de ser reconstituível.

---

## 4. Taxonomia de objeções — fechada

| Código | Significado | Sinal para o negócio |
|---|---|---|
| `preco` | Valor da peça alto | Tabela ou posicionamento |
| `frete` | Custo ou prazo do envio | **Testa a hipótese do choque de frete** |
| `prazo` | Tempo de produção | Capacidade / restrição de pose |
| `confianca` | Dúvida sobre o resultado | Prévia insuficiente ou portfólio fraco |
| `momento` | "Depois", "mês que vem" | Não é objeção de produto |
| `alternativa` | Comparou com outra opção | Diferenciação |
| `sem_resposta` | Sumiu (default no timeout) | Abordagem ou canal |
| `outro` | Fora da lista | Requer trecho literal |

**`preco` e `frete` são separados de propósito** — somá-los apagaria exatamente a pergunta em aberto.

**Revisão mensal:** se `outro` passar de 15% das objeções, a taxonomia está errada e precisa de uma categoria nova. Se ficar abaixo de 3%, provavelmente o modelo está forçando encaixe — vale conferir na amostra.

---

## 5. Temperatura — regra, não sentimento

Análise de sentimento responde a pergunta errada. O que prevê fechamento é reciprocidade e ritmo, não simpatia: cliente educado e sumido é frio; cliente seco que responde em 2 minutos é quente.

### Sinais

| Sinal | Peso |
|---|---|
| `bola_com` (quem falou por último) | Alto |
| Horas desde a última mensagem do cliente | Alto |
| Cliente puxou assunto sem ser provocado | Alto |
| Latência de resposta vs. média dele | Médio |
| Avanço de estágio nas últimas 48h | Médio |
| Tendência de comprimento das mensagens dele | Baixo |

### Classificação

```
QUENTE     bola com a Camu, OU cliente respondeu <6h, OU avançou de estágio hoje
MORNO      bola com o cliente, última mensagem dele <48h
ESFRIANDO  48h a 5 dias sem resposta, follow-up ainda não enviado
FRIO       >5 dias, ou 1 follow-up já enviado sem retorno
ENCERRADO  2 follow-ups sem retorno, ou recusa explícita
```

Auditável: quando você discordar, dá para ver exatamente qual sinal disparou.

---

## 6. Fila de follow-up

A saída do sistema **não é um painel**. É uma lista de no máximo 10 nomes por dia.

| Prioridade | Situação | Ação |
|---|---|---|
| 1 | QUENTE com bola na Camu | Responder agora — isso é dívida, não follow-up |
| 2 | ESFRIANDO em S2/S3 | Lead mais caro de perder: já mandou a foto |
| 3 | ESFRIANDO em P2/P3 | Petshop autorizou e não decidiu |
| 4 | FRIO com 0 follow-ups | Um único toque, depois encerra |
| — | FRIO com 1 follow-up | **Não aparece.** Encerrado |

**Teto rígido de 2 follow-ups por conversa.** O sistema deve tornar isso impossível de furar — não é preferência, é preservação de chip e de marca. Implementar como constraint no banco, não como validação de aplicação.

---

## 7. Ground truth — a parte que quase sempre falta

Sem isso, categorização por LLM é decoração: ela sempre devolve um rótulo, e ninguém sabe se está certo.

**Conjunto de avaliação:** 30 conversas reais rotuladas à mão pelo Marcos — estágio final e objeção. Custa ~1h e é insubstituível: só quem conhece o cliente sabe o rótulo correto.

**Metas mínimas:**
- Extração de fatos: ≥90% de concordância
- Objeção: ≥80%
- **Falso positivo de avanço de estágio: 0.** Errar para menos é perder tempo; errar para mais é abandonar um lead quente achando que ele já foi tratado

**Rodar o eval a cada mudança de prompt.** Com o Claude Code isso é barato — o que torna injustificável não fazer.

**Loop de correção.** Toda correção humana grava em `correcoes` (antes, depois, conversa, momento). Duas funções: alimenta o eval, e o padrão das correções mostra o que o prompt não está vendo. Correção que só ajusta a tela e não é gravada é informação jogada fora.

---

## 8. Backfill do histórico existente

Rodar a extração sobre as conversas que já existem. É o que dá base de comparação desde o dia um.

**Cuidado que precisa estar no código:** o backfill recupera o estado *final*, não *quando* cada transição ocorreu. Gravar `eventos_estagio` de backfill com `origem: 'backfill'` e **excluí-los de qualquer métrica de tempo por estágio** — senão a média de duração fica contaminada por timestamps inventados.

Métricas de conversão (quantos chegaram a cada estágio) podem usar backfill. Métricas de tempo, não.

---

## 9. Modelo de dados

```
contatos          id, nome, telefone_hash, telefone, tipo (b2b|b2c),
                  origem, criado_em

conversas         id, contato_id, funil, estagio, bola_com, temperatura,
                  ultimo_inbound, ultimo_outbound, followups_enviados,
                  resultado, ultima_mensagem_processada_id

mensagens         id, conversa_id, direcao, texto, enviada_em

fatos             conversa_id, chave, valor, evidencia, extraido_em
                  ← saída bruta do LLM, preservada

eventos_estagio   conversa_id, de, para, em, origem (live|backfill)

objecoes          conversa_id, categoria, estagio, trecho, em

correcoes         conversa_id, campo, antes, depois, em
```

`fatos` separado de `conversas` é o que permite reprocessar regras sem chamar o LLC de novo. `eventos_estagio` é o que responde "onde a conversa trava" — sem ele só se conhece o estado final.

---

## 10. Rascunho de resposta

Entrada: histórico, estágio, temperatura, funil, e `06-playbooks/petshops-b2b.md` como referência de tom.

**Restrições no prompt:**
- 2 a 4 linhas. Texto longo mata interesse
- Voz direta, neutra a consultiva. Nunca diminutivo ou infantilização
- Nunca a tabela de preço completa — um preço relevante por vez
- Em S1/S2, nunca abrir com preço: pedir a foto do pet
- Se FRIO com 1 follow-up, recusar-se a gerar e devolver `encerrar`
- **Sempre 2 opções**, para o humano escolher em vez de aprovar

A última importa: rascunho único vira aprovação automática. Duas opções obrigam a ler.

**Nunca enviar automaticamente.** Disparo automático em API não oficial acelera banimento, produto personalizado exige contexto que o modelo não tem, e erro em escala custa mais que o tempo economizado.

---

## 11. Transporte isolado

Toda leitura e envio passa por uma interface única: `enviar(contato, texto)` / `receber(evento)`.

A Evolution API é frágil por natureza — viola o ToS do WhatsApp e o chip pode cair a qualquer momento, independentemente do volume. Migrar para a Cloud API oficial, ou trocar de chip, deve significar substituir um adaptador. É a única parte da arquitetura que precisa estar certa desde o início, porque é a que dói refatorar com o sistema em produção.

---

## 12. LGPD

**Base legal.** B2C: o cliente iniciou o contato — diligência pré-contratual. B2B: legítimo interesse, contato comercial público. Nenhuma das duas cobre lista fria comprada ou raspada de perfil pessoal.

**Retenção.** Descartar `mensagens` de conversas encerradas há mais de 12 meses, mantendo `fatos`, `objecoes` e `eventos_estagio` — que é o que serve para análise e não guarda conteúdo pessoal.

**Telefone com hash** para lookup; original apenas onde necessário para envio.

---

## 13. Ordem de implementação

Por dependência, não por gating:

1. **Schema e taxonomias** — Marcos define. ~2h, insubstituível
2. **Contrato de extração** + `fatos`
3. **Backfill** com marcação de origem
4. **Eval de 30 conversas** — antes de confiar em qualquer número
5. **Regras** de estágio e temperatura
6. **Fila** de follow-up com o teto de 2
7. **Rascunhos** com 2 opções
8. **Painel** — por último, porque só faz sentido com histórico

Os passos 1 e 4 são os únicos que exigem o Marcos e não podem ser delegados ao Claude Code. São também os que determinam se o resto vale alguma coisa.

## 14. A métrica que justifica o sistema

`S1→S2` e `S4→S6` no B2C, `P5→P6` no B2B.

Se em 30 dias o sistema não tiver produzido esses três números, ele virou INFRA que se sustenta sozinha — e volta a valer a regra da seção 0.
