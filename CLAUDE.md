# camu-crm — instruções para o Claude Code

## O documento manda

`docs/04-crm-conversas-definicoes.md` é a fonte de verdade deste projeto. As
seções são citadas por número (§1, §6, §14) em docstrings e comentários por todo
o código, e essas referências são para serem seguidas: antes de mudar
comportamento, leia a seção citada.

Quando a implementação divergir do documento, a divergência precisa ser
explícita — um comentário dizendo o que mudou e por quê — e não silenciosa.

## A divisão que não pode ser quebrada (§1)

```
LLM extrai fatos  →  regra determinística decide  →  humano envia
```

O LLM aparece em exatamente dois lugares: `camucrm/extraction/` (extrair fatos
binários com evidência) e `camucrm/drafts.py` (rascunhar duas opções). Se um
módulo de `camucrm/rules/` passar a importar `llm`, a arquitetura vazou.

Consequência prática que justifica a regra: `make recalcular` reprocessa toda a
base sem custo de LLM. Se o estágio dependesse do modelo, cada mudança de
critério exigiria reprocessar tudo, com resultado diferente a cada vez.

## Invariantes

Nenhum destes é preferência. Quebrar qualquer um corrompe o histórico, e §0 é
claro sobre o custo: código se reescreve numa tarde, taxonomia mal desenhada
contamina tudo.

1. **Todo `true` exige evidência literal** (`extraction/contract.py`). Trecho
   que não aparece na conversa rebaixa o campo. Nunca afrouxe `_fold` para
   "casar mais" — a conferência de literalidade é o que impede o modo de falha
   mais caro.
2. **Estágio nunca regride** (`rules/estagio.py::transicao`).
3. **Teto de 2 follow-ups é constraint de banco** (`db.py`, `SCHEMA`), não
   validação de aplicação. §6 é explícito. O teste que prova isso é
   `tests/integration/test_teto_followup.py`, contra Postgres real.
4. **`eventos_estagio.origem='backfill'` fica fora de métrica de tempo**
   (`metrics.py`). O filtro mora no SQL, não numa checagem opcional.
5. **Envio exige `aprovado_por`** (`transport/base.py`). Nunca torne opcional.

## Convenções

- **Stack:** Python 3.12, sem framework. Pacote único `camucrm/`. Postgres via
  `psycopg` + `psycopg_pool`.
- **Testes:** `unittest` puro, sem `conftest.py` e sem fixtures de pytest.
  `make test` roda `python -m unittest discover -s tests -p 'test_*.py'`.
  Fakes compartilhados em `tests/fakes.py` — **nada de rede e nada de Postgres**
  na suíte unitária.
- **Teste end-to-end único:** `tests/test_e2e.py`, cobrindo
  mensagem → extração → fatos → estágio → temperatura → fila → rascunho. Toda
  mudança que altera esse ciclo **estende este arquivo**, nunca duplica um E2E
  paralelo — dois arquivos E2E sempre acabam testando versões diferentes do
  mesmo caminho, e a divergência passa despercebida até produção.
- **Exceção de infraestrutura:** `tests/integration/` fala com Postgres e fica
  fora de `make test`. Só entra ali o que não pode ser provado por fake — hoje,
  o teto de follow-ups.
- **Idioma:** funções e identificadores em inglês; nomes de domínio (estágio,
  objeção, temperatura, funil, fatos) em português, casando com o schema da §9.
  Mensagens ao usuário, logs de negócio e documentação em português.
- **Camadas:** `camucrm/transport/` é a única fronteira de leitura e envio.
  Nenhum módulo de domínio deve segurar um cliente concreto — sempre o
  protocolo `Transporte`.

## Rodar o eval a cada mudança de prompt (§7)

Mudou `extraction/prompt.py`? Rode `make eval` e incremente `PROMPT_VERSAO`.
É barato — o que torna injustificável não fazer. As metas: fatos ≥90%,
objeção ≥80%, **falso positivo de avanço de estágio = 0** (esta reprova
sozinha).

O conjunto de avaliação real (`data/eval/conversas.jsonl`) é rotulado à mão
pelo Marcos e não está versionado (§12). Sem ele, `make eval` roda contra o
exemplo e o número não significa nada.

## OpenSpec

`openspec/` é a fonte de verdade do planejamento. Ver `openspec/project.md`
para o estado atual e a ordem de dependência. As regras gerais do fluxo
(propor → aplicar → sincronizar → arquivar) estão nas instruções globais.
