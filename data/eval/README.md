# Conjunto de avaliação (§7)

`conversas.exemplo.jsonl` mostra **o formato**. Ele não é o conjunto de
avaliação e não deve ser usado para decidir nada sobre o prompt.

O conjunto real é `conversas.jsonl`: **30 conversas reais rotuladas à mão pelo
Marcos** — estágio final e objeção. §7 é explícito sobre por que isso não pode
ser delegado:

> Custa ~1h e é insubstituível: só quem conhece o cliente sabe o rótulo correto.

Rotular com LLM mediria o modelo contra ele mesmo e produziria um número alto e
sem informação.

## Como rotular

Uma conversa por linha (JSONL). Campos:

| Campo | O que é |
|---|---|
| `id` | Identificador curto e estável |
| `funil` | `b2c` ou `b2b` |
| `mensagens[]` | `direcao` (`in`/`out`), `texto`, `enviada_em` (ISO 8601) |
| `rotulo.estagio_final` | O estágio em que a conversa realmente parou |
| `rotulo.objecao` | Categoria da §4, ou `null` se não houve |
| `rotulo.fatos` | Os 7 campos do contrato (§2), como você os leria |
| `rotulo.marcos` | `ganho`, `consignacao_assinada`, `primeira_reposicao` quando houver |
| `nota` | Livre — por que este caso é interessante |

Comentários (`//`) e linhas em branco são ignorados.

## Rodar

```bash
make eval
```

As metas (§7): fatos ≥90%, objeção ≥80%, **falso positivo de avanço de estágio = 0**.
A terceira reprova sozinha, independentemente das outras duas.

Rode a cada mudança de prompt. Com o Claude Code isso é barato — o que torna
injustificável não fazer.
