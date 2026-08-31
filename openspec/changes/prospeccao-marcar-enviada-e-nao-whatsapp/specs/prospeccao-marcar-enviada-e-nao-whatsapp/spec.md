# Delta: prospeccao-marcar-enviada-e-nao-whatsapp

## ADDED Requirements

### Requirement: Operador marca uma prospecção como já enviada à mão

`POST /api/prospeccao/{id}/enviada-manual` com `por` não-vazio MUST (DEVE)
registrar a linha como enviada sem chamar a Evolution API, gravando
`enviado_em = now()` e `enviado_instancia = 'manual'`. `por` vazio DEVE
resultar em 422, sem gravar nada. `valor: false` DEVE desfazer a marca, mas
SÓ quando ela era manual — um envio confirmado pela API nunca é apagado por
esta rota.

#### Scenario: Marca manual grava e aparece no JSON

- **WHEN** o operador clica "Marcar como já enviado"
- **THEN** `GET /api/prospeccao` traz a linha com `enviado_manual: true` e
  `enviado_em` preenchido, e a tela some com o botão de disparo

#### Scenario: Desfazer não apaga envio real

- **WHEN** a linha já tem um `enviado_em` gravado por um envio da Evolution
  API e o operador clica "Desfazer 'já enviado'"
- **THEN** `enviado_em` e `enviado_instancia` continuam como estavam

### Requirement: Operador marca um telefone como "não é WhatsApp"

`POST /api/prospeccao/{id}/nao-whatsapp` com `por` não-vazio MUST (DEVE)
gravar `nao_whatsapp = TRUE` na linha. A linha NÃO DEVE ser removida da tabela nem
da listagem — só perde os botões de disparo na tela. A marca DEVE
sobreviver à reimportação da mesma planilha. `valor: false` desfaz. `por`
vazio DEVE resultar em 422.

#### Scenario: Reimportar a planilha não ressuscita o número

- **WHEN** uma linha está marcada `nao_whatsapp` e a mesma planilha é
  importada de novo
- **THEN** os dados da linha são atualizados e `nao_whatsapp` continua
  `TRUE`

#### Scenario: Linha marcada continua visível, sem disparo

- **WHEN** o operador abre a aba de prospecção
- **THEN** a linha marcada aparece com o selo "não é número de WhatsApp" e
  um botão "Desfazer", e nenhum botão de envio

### Requirement: As rotas de marca manual não são rotas de envio

Nenhuma das duas rotas MUST (DEVE) importar `camucrm.transport` nem conter a
substring `enviar` no path — o único path de prospecção autorizado a conter
`enviar` continua sendo `POST /api/prospeccao/{prospeccao_id}/enviar`.

#### Scenario: Teste-guarda de path continua com um único envio

- **WHEN** `server.app.openapi()` é inspecionado
- **THEN** o conjunto de paths de prospecção que contêm `enviar` é
  exatamente `{"/api/prospeccao/{prospeccao_id}/enviar"}`
