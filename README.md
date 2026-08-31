# camu-crm

CRM de conversas de WhatsApp da Camu, para converter mais vendas B2B e B2C.

A divisão que estrutura tudo (§1 de [`docs/04-crm-conversas-definicoes.md`](docs/04-crm-conversas-definicoes.md)):

```
LLM extrai fatos  →  regra determinística decide  →  humano envia
```

O LLM nunca decide estágio, temperatura, prioridade ou envio. Ele responde
perguntas factuais fechadas, com evidência literal obrigatória. Se as regras
mudarem, basta reprocessar os fatos já extraídos — sem custo de LLM e sem
reinterpretar conversa antiga.

**A saída do sistema não é um painel. É uma lista de no máximo 10 nomes por dia.**

## Começar

```bash
cp .env.example .env   # defina CAMU_TELEFONE_SALT e GEMINI_API_KEY
make up
```

`make up` sobe tudo na ordem de dependência e verifica cada etapa em vez de
assumi-la: Postgres, schema, o banco da Evolution, a Evolution API, o
pareamento do chip, para onde o webhook aponta, o receptor e o painel. É
idempotente — rodar duas vezes não duplica processo.

Se alguma peça faltar, ele diz qual e sai com código 2. Subir metade do
sistema em silêncio é pior que não subir nada, porque a fila parece vazia em
vez de parecer quebrada.

| Script                           | O que faz                                           |
| -------------------------------- | --------------------------------------------------- |
| `make up`                      | Sobe tudo                                         |
| `make up-sem-painel`           | Sobe só a recepção, sem UI                       |
| `./stop.sh`                    | Para receptor e painel; preserva banco e pareamento |
| `./restart.sh`                 | Para e sobe de novo — use depois de editar código   |
| `./status.sh`                  | Uma tela dizendo o que está no ar e o que falta    |
| `./scripts/parear.py`          | Página local com o QR, renovado sozinho            |
| `./scripts/apontar_webhook.sh` | Aponta o webhook da Evolution para o CRM            |

Os mesmos comandos estão no Makefile: `make up`, `make down`, `make restart`,
`make status`.

`stop.sh` não derruba a Evolution de propósito: ela segura o pareamento do
chip, e §11 já avisa que essa é a parte frágil. Derrubá-la a cada parada do
CRM significaria repareamento frequente.

**`./start.sh` sozinho não recarrega código.** `subir()` (dentro de
`start.sh`) é idempotente de propósito — se o processo já responde em
`/health`, ele considera "no ar" e não reinicia. Isso é o comportamento
certo para "sobe o sistema sem duplicar processo", mas significa que editar
`camucrm/` e rodar `./start.sh`/`make up` de novo **não** coloca o código
novo em produção: receptor e painel continuam com o processo antigo na
memória. Depois de qualquer mudança em `camucrm/`, use `./restart.sh` (ou
`make restart`) para testar — nunca `./start.sh` sozinho.

## Comandos

| Comando                                           | O que faz                                                 |
| ------------------------------------------------- | --------------------------------------------------------- |
| `make fila`                                     | A fila do dia. É este que precisa ser aberto toda manhã |
| `make extrair`                                  | Roda a extração sobre os blocos novos de mensagem       |
| `make recalcular`                               | Reaplica as regras sobre os fatos já extraídos, sem LLM |
| `make eval`                                     | Roda o eval contra o conjunto rotulado (§7)              |
| `make metricas`                                 | Os três números da §14                                 |
| `make backfill ARQUIVO=dump.json`               | Importa e extrai o histórico (§8)                       |
| `camucrm rascunho <id>`                         | Duas opções de resposta, para escolher                  |
| `camucrm marcar <id> ganho`                     | Registra um marco manual (S6/P5/P6)                       |
| `camucrm corrigir <id> <campo> --de X --para Y` | Grava uma correção humana (§7)                         |

`camucrm --help` lista tudo.

## Arquitetura

```
camucrm/
  taxonomia.py       Funis, estágios, objeções, temperaturas — tudo que é fechado
  db.py              Schema (§9) e consultas. O teto de 2 follow-ups vive aqui
  extraction/        A única coisa que o LLM faz: extrair fatos com evidência
    contract.py      Valida a saída do modelo. Todo `true` exige trecho literal
    prompt.py        As perguntas fechadas. Mudou aqui, roda o eval
    extractor.py     Delta + resumo rolante + idempotência
  rules/             As decisões determinísticas. Sem LLM, replayable
    sinais.py        Tempo e reciprocidade, derivados das mensagens
    estagio.py       Deriva o estágio; garante que ele nunca regride
    temperatura.py   QUENTE..ENCERRADO, com o sinal que disparou
    fila.py          Prioridade 1..4 e o teto de 10 nomes
  transport/         A fronteira única de leitura e envio (§11)
    base.py          `enviar(contato, texto)` / `receber(evento)`
    evolution.py     WhatsApp não oficial. A peça frágil, isolada de propósito
    console.py       Dry-run. É o padrão
  pipeline.py        Junta fatos + regras + banco. Live avança, backfill reconstrói
  drafts.py          Duas opções de resposta, nunca uma (§10)
  backfill.py        Histórico, marcado com `origem='backfill'` (§8)
  evaluation/        Ground truth e as metas da §7
  metrics.py         S1→S2, S4→S6, P5→P6 (§14)
```

### Três invariantes que não podem ser quebrados

**Todo `true` exige evidência literal.** Um campo afirmado sem trecho — ou com
trecho que não aparece na conversa — volta a `false` e a demoção é registrada.
É o que impede o modelo de avançar estágio por otimismo, o modo de falha mais
caro: um lead marcado como quente sem ter sido nunca é revisitado.

**O teto de 2 follow-ups é constraint de banco.** `conversas.followups_enviados`
tem `CHECK (<= 2)` e `followups.numero` só admite `{1, 2}` com `UNIQUE` por
conversa. Um terceiro follow-up não é proibido — é irrepresentável. Provado em
[`tests/integration/test_teto_followup.py`](tests/integration/test_teto_followup.py)
contra Postgres real, porque um fake que "garante" uma constraint prova apenas
que concorda consigo mesmo.

**Eventos de backfill ficam fora de métrica de tempo.** O backfill recupera o
estado final, não *quando* cada transição ocorreu. `eventos_estagio.origem`
separa os dois, e `metrics.tempo_por_estagio` filtra por `origem = 'live'` no
próprio SQL — esquecer o filtro produziria um número plausível e inventado.

## Testes

```bash
make test      # 145 testes, sem rede e sem Postgres
make test-db   # a constraint do teto, contra Postgres real
```

## O que ainda depende do Marcos

Dois passos de §13 não podem ser delegados, e são os que determinam se o resto
vale alguma coisa:

1. **Taxonomias** — os vocabulários fechados estão em `camucrm/taxonomia.py`,
   escritos a partir de §3 e §4. Precisam de uma leitura de quem conhece o
   cliente antes de contaminarem o histórico.
2. **O conjunto de avaliação** — 30 conversas reais rotuladas à mão. Ver
   [`data/eval/README.md`](data/eval/README.md). Sem ele, os números do eval
   não significam nada.

O **painel** (§13, passo 8) não foi construído, deliberadamente: ele é o
último, "porque só faz sentido com histórico". A fila é a saída.
