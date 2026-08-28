# Tasks — escolher a instância no envio de prospecção

- [x] 1.1 `EvolutionTransporte.listar_instancias()` + dataclass
      `InstanciaEvolution(nome, conectada)` — normaliza v1/v2 de
      `fetchInstances`, `TransporteError` sem credencial / rede fora do ar
- [x] 1.2 `criar_transporte("evolution", instancia=...)` sobrepõe
      `EVOLUTION_INSTANCE`; `listar_instancias_evolution()` helper de fábrica
- [x] 2.1 Schema: `prospeccoes.enviado_instancia VARCHAR(64)`
      (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)
- [x] 2.2 `ProspeccaoRegistro.enviado_instancia`; `_PROSPECCAO_SELECT` e o
      SELECT de `listar_prospeccoes` atualizados
- [x] 2.3 `registrar_envio_prospeccao(..., instancia=None)` — grava no
      sucesso e na falha
- [x] 3.1 `camucrm/painel/envio.py`: `enviar_prospeccao(..., instancia=None)`
      repassa para `criar_transporte`/`registrar_envio_prospeccao`;
      `instancias_disponiveis()` novo
- [x] 3.2 Rota `GET /api/prospeccao/instancias` — 502 em `TransporteError`
- [x] 3.3 `EnviarProspeccaoBody.instancia`; rota de envio repassa
- [x] 3.4 `views.prospeccao_para_json` inclui `enviado_instancia`
- [x] 4.1 Frontend: `<select>` "Enviar pelo número" no popup, populado por
      `/api/prospeccao/instancias`, oculto quando a lista não carrega
- [x] 4.2 Linha da prospecção mostra "pelo &lt;número&gt;"; CSS do `<select>`
- [x] 5.1 `tests/fakes.py`: `enviado_instancia` em `criar_prospeccao`,
      `importar_prospeccoes`, `_prospeccao_registro`, `prospeccao_por_
      telefone_hash`, `registrar_envio_prospeccao`
- [x] 5.2 `tests/test_transport.py::TesteListarInstancias` (v1, v2, sem
      credencial, rede fora do ar)
- [x] 5.3 `tests/test_painel_api.py`: instância repassada e registrada;
      ausência usa padrão; falha grava a instância; `GET .../instancias`
      sucesso e 502
- [x] 6.1 `.env.example`: comentário de `EVOLUTION_INSTANCE`
- [x] 6.2 `openspec/project.md`: registrar a extensão
- [x] 7.1 `make test` verde (2 falhas pré-existentes de path no Windows em
      `test_eval_painel`, não relacionadas)
