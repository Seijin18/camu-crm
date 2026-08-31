# Delta: midia-foto-pet

## ADDED Requirements

### Requirement: Imagem sem legenda gera `foto_pet_recebida` deterministicamente

Uma mensagem `in`, numa conversa do funil B2C, cujo `midia_tipo` gravado é
`image`, DEVE fazer o fato `foto_pet_recebida=true` ser gravado em
`fatos` — independente de haver legenda, independente de o LLM ter sido
chamado ou estar disponível. A evidência gravada é o marcador fixo
`[imagem]`, nunca o conteúdo da imagem (que o sistema não baixa).

#### Scenario: Foto sem legenda avança para S2

- **WHEN** chega uma mensagem `in` de uma conversa B2C cujo payload é
  `imageMessage` sem `caption`
- **THEN** `mensagens.midia_tipo` é gravado como `image`
- **AND** um fato `foto_pet_recebida=true` é gravado em `fatos`, com
  `evidencia='[imagem]'`
- **AND** o próximo recálculo de estágio (`rules/estagio.py::derivar`)
  deriva `S2`

#### Scenario: Foto com legenda também dispara o fato, mesmo que a legenda não mencione o pet

- **WHEN** chega uma mensagem `in` de uma conversa B2C cujo payload é
  `imageMessage` com `caption="oi"` (texto que sozinho não seria
  evidência de nada via `extraction/contract.py`)
- **THEN** o fato `foto_pet_recebida=true` é gravado do mesmo jeito, via
  `midia_tipo`, não via a legenda

#### Scenario: Extração via LLM desligada ou indisponível não impede o gatilho

- **WHEN** `extrair_ao_receber()` está desligado, ou o LLM está
  indisponível (`get_extrator()` devolve `None`)
- **AND** chega uma mensagem `in` B2C com `midia_tipo='image'`
- **THEN** o fato `foto_pet_recebida=true` é gravado do mesmo jeito — o
  gatilho não depende de nenhuma chamada ao LLM

### Requirement: Gatilho restrito a B2C, imagem, mensagem do cliente

O gatilho determinístico da requirement anterior NÃO DEVE disparar fora
das três condições: `funil=B2C`, `midia_tipo='image'`, `direcao='in'`.

#### Scenario: Imagem numa conversa B2B não gera fato análogo

- **WHEN** chega uma mensagem `in` com `midia_tipo='image'` numa conversa
  cujo `funil` é `B2B`
- **THEN** nenhum fato é gravado deterministicamente por este mecanismo

#### Scenario: Foto enviada pela Camu (saída) não conta como o cliente mandando a própria foto

- **WHEN** chega uma mensagem `out` (a Camu enviando uma imagem, ex. a
  prévia do produto) com `midia_tipo='image'`
- **THEN** nenhum fato `foto_pet_recebida` é gravado por este mecanismo
  (`previa_enviada`, se aplicável, continua exclusivamente via
  `extraction/`, evidência literal de texto)

#### Scenario: Vídeo ou documento não disparam o gatilho

- **WHEN** chega uma mensagem `in` B2C com `midia_tipo` igual a `video`
  ou `document`
- **THEN** nenhum fato é gravado deterministicamente — fora de escopo
  deste change (ver `proposal.md`)

### Requirement: Nenhum binário de mídia é baixado ou persistido

Este sistema NÃO DEVE, em nenhum ponto deste change, baixar o conteúdo
binário de uma mídia recebida nem gravar uma URL/chave que permita buscá-lo
depois. `midia_tipo` é sempre um valor de um enum fechado curto — nunca o
conteúdo, nunca uma referência a ele.

#### Scenario: Payload de mídia não é persistido além do tipo

- **WHEN** uma mensagem com `imageMessage` é ingerida
- **THEN** a única informação de mídia gravada em `mensagens` é
  `midia_tipo='image'` — nenhuma URL, `mediaKey`, ou binário

### Requirement: Coluna `midia_tipo` segue a mesma retenção de `mensagens`

`midia_tipo` NÃO DEVE ter caminho de retenção próprio — é purgada junto
com o resto da linha por `Database.purgar_mensagens_antigas` (§12, 12
meses), como qualquer outra coluna de `mensagens`.

#### Scenario: Purga de mensagens antigas remove midia_tipo junto

- **WHEN** `camucrm purgar` remove uma mensagem com mais de 12 meses que
  tinha `midia_tipo` preenchido
- **THEN** a linha inteira é removida, sem exceção para essa coluna

## MODIFIED Requirements

### Requirement: Marcador nunca vira evidência de fato (herdado de `mensagem-sem-texto-preservada`)

O marcador textual de mídia sem legenda continua NÃO sendo evidência
válida para `extraction/contract.py::_fold` — **com uma exceção
explícita e única**: o fato `foto_pet_recebida`, quando gravado pelo
mecanismo determinístico deste change, não passa por `_fold` (não é
avaliado pelo LLM), então a restrição de `_fold` simplesmente não se
aplica a ele — não é uma exceção DENTRO de `_fold`, é uma gravação que
acontece por um caminho inteiramente separado. Para todos os outros
fatos do §2, e para toda extração feita pelo LLM, a regra original
continua vigorando sem alteração.

#### Scenario: Marcador de imagem não vira evidência de outro fato via LLM

- **WHEN** o único conteúdo novo de um bloco é uma mensagem cujo texto é
  o marcador `[imagem]`
- **THEN** a extração via LLM sobre esse bloco não afirma nenhum fato
  (que não seja `foto_pet_recebida`) tendo esse marcador como evidência
