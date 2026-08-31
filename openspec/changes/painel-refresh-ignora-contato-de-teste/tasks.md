# Tasks — refresh de tempo real ignora contato de teste

## 1. Implementação

- [x] 1.1 `camucrm/db.py::token_de_mudanca`: `JOIN conversas`/`contatos` +
      `WHERE ct.e_teste = FALSE` nas três subconsultas (→ Requirement
      "Cursor de tempo real ignora contato de teste").
- [x] 1.2 `tests/fakes.py::FakeDatabase`: `_tocar_conversa` novo (só
      incrementa `_toques_conversa` quando a conversa não é de teste),
      chamado por `registrar_mensagem`/`gravar_evento_estagio`/
      `atualizar_estado_conversa`; `token_de_mudanca` filtra as duas
      primeiras partes por `_e_teste_da_conversa` (→ Requirement "Cursor de
      tempo real ignora contato de teste").

## 2. Testes

- [x] 2.1 `tests/test_painel_stream.py::test_token_nao_muda_com_mensagem_
      em_conversa_de_teste`: mensagem + evento de estágio numa conversa de
      teste não mudam o token (→ Requirement "Cursor de tempo real ignora
      contato de teste", cenário "Mensagem para contato de teste não move
      o token").
- [x] 2.2 `tests/test_painel_stream.py::test_token_muda_com_mensagem_em_
      conversa_real_apesar_de_conversa_de_teste_existir`: conversa real ao
      lado de uma de teste continua disparando o token normalmente — o
      filtro é seletivo, não global (→ Requirement "Cursor de tempo real
      ignora contato de teste", cenário "Mensagem para contato real move o
      token normalmente").
- [x] 2.3 Suíte completa (`make test`): 742 testes, OK (era 740 antes deste
      change — 2 testes novos).

## 3. Verificação manual

- [ ] 3.1 Contra o painel real (`./start.sh`): abrir uma aba qualquer sem
      "Modo teste" ativo, mandar mensagem para um contato marcado de teste
      (ex.: Felipe) — a aba não recarrega. Pendente do operador, mesmo
      padrão de `painel-preserva-estado-em-refresh` (infraestrutura de
      produção que este agente não sobe sozinho).

## 4. Sincronização

- [x] 4.1 Implementação bateu com o `proposal.md`, sem divergência. Achado
      durante os testes (não previsto no proposal): `_toques_conversa` no
      fake era um contador global sem noção de conversa — precisou virar
      `_tocar_conversa(conversa_id)`, guardado por `_e_teste_da_conversa`,
      para o fake continuar espelhando a consulta real (que filtra as três
      partes, não só duas). Sem isso `test_token_nao_muda_com_mensagem_em_
      conversa_de_teste` falhava mesmo com `db.py` correto — o fake que
      estava errado, não a implementação real.
