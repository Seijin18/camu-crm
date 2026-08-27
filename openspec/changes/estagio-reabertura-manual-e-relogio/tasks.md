# Tasks — reabertura manual de estágio e relógio de temperatura confiável

## 1. Implementação — desconsideração de recusa e reabertura manual

- [x] 1.1 `camucrm/db.py`: registrar desconsideração de
      `recusa_explicita` em `correcoes` (campo `"recusa_explicita"`,
      antes/depois) (→ Requirement "Desconsideração de recusa é registrada
      sem apagar o fato").
- [x] 1.2 `camucrm/rules/estagio.py::_derive_b2c`/`_derive_b2b`: consultar
      desconsideração ativa antes de tratar `recusa_explicita=true` como
      terminal — ignora o fato desconsiderado, reabre no maior estágio já
      alcançado (→ Requirement "Recusa desconsiderada permite avanço de
      novo").
- [x] 1.3 `camucrm/rules/estagio.py::reabrir`: checagem própria (não só do
      chamador) de que a conversa não está terminal por `recusa_explicita`
      não-desconsiderada (→ Requirement "reabrir() recusa reabertura de
      recusa não-desconsiderada sozinha").
- [x] 1.4 `camucrm/cli.py`: comando novo dedicado (ver `design.md` — não
      reaproveita `camucrm corrigir`) para desconsiderar recusa, exigindo
      `--por` (→ Requirement "Desconsideração exige identificação de quem
      decidiu").
- [x] 1.5 `camucrm/painel/api.py` + `static/*`: botão no detalhe da conversa
      para desconsiderar recusa, exigindo `por` (→ Requirement
      "Desconsideração exige identificação de quem decidiu").

## 2. Implementação — reconciliação e direção do avanço

- [x] 2.1 `camucrm/acoes.py::mudar_funil_conversa`: usar a mesma
      reconciliação contra `eventos_estagio` que `pipeline.recalcular`/
      `_avanco_ao_vivo` já usam, em vez de ler `conversas.estagio` cru (→
      Requirement "mudar_funil_conversa reconcilia contra o histórico").
- [x] 2.2 `camucrm/rules/estagio.py` (ou onde `Transicao`/`Derivacao` é
      definido): campo novo `causada_por: "cliente" | "camu"`, formalizando
      o mapa já implícito em `_derive_b2c`/`_derive_b2b` (→ Requirement
      "Avanço causado pela Camu não classifica QUENTE").
- [x] 2.3 `camucrm/rules/temperatura.py`: consultar `causada_por` antes de
      classificar QUENTE por "avançou de estágio hoje" (→ Requirement
      "Avanço causado pela Camu não classifica QUENTE").

## 3. Implementação — clamp de timestamp em sinais

- [x] 3.1 `camucrm/rules/sinais.py`: clampar `enviada_em` (`min(timestamp,
      agora())`) antes de decidir qual mensagem é "a última" para
      `bola_com` (→ Requirement "Timestamp futuro não congela bola_com").

## 4. Testes

- [x] 4.1 `tests/test_estagio.py`: `recusa_explicita` falso positivo,
      uma vez desconsiderado via a ação nova, permite a conversa voltar a
      avançar (→ Requirement "Recusa desconsiderada permite avanço de
      novo"). (Nome do arquivo real do repo — não `test_rules_estagio.py`,
      que não existe aqui.)
- [x] 4.2 `tests/test_estagio.py`: `reabrir()` recusa sozinha reabrir
      uma conversa terminal por recusa não-desconsiderada, mesmo chamada
      diretamente sem passar pelo guard de `pipeline.py` (→ Requirement
      "reabrir() recusa reabertura de recusa não-desconsiderada sozinha").
- [x] 4.3 `tests/test_acoes.py`: `mudar_funil_conversa` com
      `conversas.estagio` divergente do histórico grava o `de` correto,
      reconciliado (→ Requirement "mudar_funil_conversa reconcilia contra o
      histórico").
- [x] 4.4 `tests/test_temperatura.py`: avanço de estágio causado
      inteiramente por mensagem `out` (Camu) não classifica QUENTE (→
      Requirement "Avanço causado pela Camu não classifica QUENTE"). (Nome
      real do arquivo — não `test_rules_temperatura.py`.)
- [x] 4.5 `tests/test_sinais.py` (arquivo novo, módulo `rules/sinais.py`
      ainda não tinha teste dedicado): timestamp futuro em `enviada_em` não
      "trava" `bola_com` à frente de mensagens reais subsequentes (→
      Requirement "Timestamp futuro não congela bola_com").
- [x] 4.6 `tests/test_e2e.py` (estendido, não duplicado): ciclo completo de
      recusa falso-positiva → desconsideração manual → conversa volta a
      avançar de estágio com mensagem nova do cliente
      (`TesteReaberturaManualDeRecusa`).
- [x] 4.7 Suíte completa verde (`make test`: 570 testes, `make test-db`: 54
      testes contra Postgres real). Verificado manualmente também contra
      Postgres real (`make init` + `camucrm desconsiderar-recusa`): conversa
      travada em SX por recusa falso-positiva reabriu para S2 (maior
      estágio já alcançado), fato `recusa_explicita` permaneceu íntegro, e a
      desconsideração ficou registrada em `correcoes`.

Testes extras cobertos além do mínimo pedido (mesmos arquivos acima):
regressão de "sem desconsideração continua travada", `reabrir()` com
`recusa_explicita=False` inalterado (regressão de timeout), avanço causado
pelo cliente continua QUENTE (regressão), CLI/painel exigem `--por`/`por` e
recusam sem fato `recusa_explicita` registrado.
