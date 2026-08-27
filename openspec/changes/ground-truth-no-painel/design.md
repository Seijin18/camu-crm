## Context

O dataset de ground truth (`data/eval/conversas.jsonl`) hoje é editado à mão
num editor de texto. O pedido do usuário é rotular pelo painel — a decisão
que precisa de justificativa por escrito é ONDE esse dataset passa a viver:
continua arquivo, ou migra para uma tabela Postgres agora que o painel vai
escrever nele por uma rota HTTP?

## Decisão: o dataset continua sendo o arquivo `data/eval/conversas.jsonl`

Não migra para Postgres. Motivos:

1. **É o formato que o pipeline de avaliação já lê.** `make eval`/
   `evaluation/dataset.py::carregar` leem esse arquivo hoje, sem qualquer
   mudança necessária no pipeline de avaliação em si. Migrar para Postgres
   exigiria reescrever `carregar()` e qualquer script/Makefile que dependa
   do formato JSONL, para um ganho que não é claro (o dataset tem no máximo
   algumas dezenas de linhas, TAMANHO_MINIMO=30 é o próprio critério de
   completude).
2. **O arquivo já é proposital e permanentemente fora do git** (§12 —
   contém conversas reais de clientes). Manter o ground truth fora do
   Postgres operacional preserva essa fronteira: um Postgres de produção
   não deveria acumular o dataset de avaliação (dado pessoal, retido para
   fins de teste de prompt) junto com o dado operacional (conversas reais
   em andamento, sujeitas às regras de purga de §12). Misturar as duas
   coisas na mesma base tornaria a política de retenção/purga mais difícil
   de raciocinar — teria que se justificar por que uma tabela é purgada e
   outra (o dataset de eval) não.
3. **O painel já tem acesso de filesystem local.** É processo local, único
   usuário, bind `127.0.0.1` — ler/escrever esse arquivo é consistente com
   o resto do desenho do painel (que já lê `CAMU_PLAYBOOK` do filesystem),
   não introduz superfície nova de ataque nem de operação.

## Decisão: `validar_entrada` é extraída para função reusável

Hoje a validação de uma entrada do dataset (estágio pertence à taxonomia,
objeção pertence à lista, cada fato pertence ao contrato) vive dentro de
`_para_conversa`, privada e acoplada à leitura de arquivo linha a linha.
Este change extrai essa validação para `validar_entrada(bruto, onde) ->
ConversaRotulada`, chamada tanto por `carregar()` (lendo do arquivo) quanto
pelas rotas novas do painel (recebendo JSON do formulário). Isso segue a
mesma disciplina já estabelecida no projeto para `db.py` (único lugar com
SQL): aqui, `dataset.py` passa a ser o único lugar com validação de rótulo
— o painel nunca reimplementa a regra de validação por conta própria, o que
evitaria a validação divergir entre o caminho de arquivo e o caminho HTTP.

## Decisão: resultado do eval é cacheado em arquivo, não em tabela

`RelatorioEval` (resultado de `POST /eval/rodar`) é salvo em
`data/eval/ultimo_resultado.json` — mesma fronteira file-based do dataset,
pela mesma razão do item 2 acima. `ResultadoConversa` não carrega texto de
mensagem, só métricas agregadas (percentuais de fatos/objeção corretos,
contagem de falsos positivos) — cachear esse resultado não amplia a
superfície de dado pessoal exposta, mesmo sendo um arquivo relativamente
fácil de ler.

## Alternativa descartada

Tabela Postgres `ground_truth` espelhando o schema do JSONL — rejeitada
pelos três motivos acima. Reconsiderar essa alternativa só faria sentido se
o volume do dataset crescesse ordens de magnitude além do necessário para
`make eval` (hoje limitado a nº pequeno de conversas rotuladas à mão), o que
não é o caso previsto.
