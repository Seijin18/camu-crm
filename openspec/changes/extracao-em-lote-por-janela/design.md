# Design — extracao-em-lote-por-janela

## Por que atrasar a extração é seguro aqui

Duas garantias já existentes no código, verificadas antes deste desenho,
que juntas dizem "atrasar a extração não corrompe estado nem esconde
urgência":

1. **Temperatura não é derivada de fato nenhum extraído por LLM.**
   `rules/temperatura.py::classificar(sinais, fatos)` só lê `fatos` para a
   condição `recusa_explicita` (ENCERRADO) — todo o resto (QUENTE/MORNO/
   ESFRIANDO/FRIO) vem de `sinais.horas_desde_inbound`/`dias_sem_resposta`/
   `bola_com`, calculados em `pipeline.carregar_sinais` a partir de
   `mensagens.enviada_em`/`ultimo_inbound`/`ultimo_outbound` — colunas
   atualizadas em `registrar_mensagem`, portanto em TODO ingest,
   independente de a extração ter rodado. Uma conversa que acabou de
   receber mensagem aparece "bola com a Camu" / "respondeu há Xh" na hora,
   mesmo com a extração pendente.
2. **Eventos de estágio são carimbados pelo momento da evidência, não do
   processamento.** `pipeline.momentos_de_estagio` (caminho ao vivo) e o
   comentário em `Extrator._persistir`/`momento_da_evidencia` garantem que
   um evento gravado por uma extração atrasada leva o timestamp da
   mensagem que o disparou, não o de quando o LLM rodou. `metrics.py`
   nunca vê "tempo por estágio" inflado por causa de uma extração adiada
   alguns minutos.

O único custo real de atrasar é **o operador ver o estágio atualizado no
painel um pouco depois do que veria hoje** — nunca fila fria por engano
(temperatura já reage na hora), nunca métrica de tempo distorcida
(timestamp já é retroativo). Isso é o que abre espaço para um atraso da
ordem de minutos, não segundos.

## Por que não um timer em memória por conversa

A alternativa mais óbvia — `asyncio.sleep` por conversa, reagendado a cada
mensagem nova, cancelado e recriado a cada evento — foi descartada:

- **Não sobrevive a redeploy.** Um restart do processo do webhook (deploy,
  crash, `docker compose restart`) apaga todo timer pendente em memória,
  sem registro de que havia extração devida. O resto deste sistema evita
  exatamente isso — `ultima_mensagem_processada_id` é a prova de que
  "onde parei" precisa estar no banco, não na memória do processo, e
  timer por conversa reintroduziria a mesma classe de bug que aquele
  watermark existe para prevenir.
- **Estado por conversa em memória de um processo web não é
  auditável nem inspecionável** da forma que uma coluna/consulta é — não
  dá para responder "quantas conversas estão com extração pendente agora"
  sem instrumentar o processo.

## Por que contagem OU espera, não só um dos dois

Contagem pura (ex.: "a cada 20 mensagens") falha porque a maioria das
conversas de venda no WhatsApp nunca chega perto de 20 mensagens numa
sessão — um gatilho só por contagem raramente dispara sozinho na conversa
típica, e o trabalho real vira 100% dependente do cron (`make extrair`).

Espera pura (ex.: "sempre 3 minutos depois da última mensagem") readiciona
o problema que a extração imediata resolve para conversas ativas: um
cliente respondendo rápido, mensagem após mensagem, nunca dá 3 minutos de
silêncio, e a conversa fica sempre "quase processada, nunca processada" só
por live traffic contínuo.

**Os dois juntos, o que disparar primeiro**, resolve as duas pontas: uma
rajada fragmentada de 3-4 mensagens em segundos não atinge o limiar de
contagem (6) nem o teto de espera (3 min) até a rajada realmente parar —
colapsa numa chamada só. Uma conversa muito ativa e prolongada eventualmente
cruza o limiar de contagem mesmo sem silêncio. E o cron (`make extrair`)
continua sendo a rede de segurança real para o caso mais comum — uma
conversa com poucas mensagens que nunca atinge nenhum dos dois limiares.

## Por que os limiares são env-configuráveis, não constantes de código

`CAMU_EXTRACAO_LIMIAR_MENSAGENS`/`CAMU_EXTRACAO_TETO_ESPERA_MINUTOS` são
knobs operacionais, não decisão de arquitetura — o valor certo depende do
volume real de mensagens fragmentadas e da cadência real do cron, que só a
operação (Marcos) observa depois de rodar. Mesma categoria de
`CAMU_GEMINI_MODEL`/`TAMANHO_MAXIMO_BLOCO` (este último é constante porque
é sobre não estourar contexto do modelo — risco técnico fixo; os limiares
daqui são sobre custo vs. latência aceitável — trade-off de operação).

Defaults escolhidos (6 mensagens / 3 minutos), não os "20 mensagens ou 1h"
inicialmente cogitados: a maioria das conversas reais fica bem abaixo de
20 mensagens no total, então um limiar de contagem alto quase nunca
dispara — a "rede de segurança" vira só o cron, com todo atraso do
intervalo de cron. E um teto de espera de 1h deixaria o painel mostrando
estágio visivelmente desatualizado por até uma hora após uma única
mensagem isolada, o que pesa contra "a fila é o produto" mais do que o
ganho de token justifica. 3 minutos é pequeno o bastante para não incomodar
e grande o bastante para absorver qualquer rajada realista de digitação
fragmentada.

## Cadência de cron recomendada

O gatilho de espera (`CAMU_EXTRACAO_TETO_ESPERA_MINUTOS`) só se aplica de
verdade a conversas que continuam recebendo webhook — para a conversa mais
comum ("cliente manda uma mensagem, some"), nenhum webhook futuro vai
reavaliar o teto de espera, e só o cron (`make extrair`) processa. Por
isso a recomendação operacional (fora do escopo de código deste change,
mas registrada aqui): configurar `make extrair` num cron externo a cada
1–3 minutos. Sem isso, uma conversa isolada de mensagem única fica pendente
até o cron rodar, qualquer que seja o intervalo configurado — o mecanismo
deste change não piora essa espera, só deixa de mascará-la atrás de uma
chamada de LLM imediata que nem sempre era necessária.
