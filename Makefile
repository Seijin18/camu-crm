.PHONY: help up down restart status db-up db-down db-logs init test test-db fila extrair recalcular eval metricas backfill lint painel servir acompanhar purgar win-dev

PY = ./.venv/bin/python
COMPOSE = docker compose

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

up:  ## sobe o sistema inteiro (banco, transporte, receptor, painel)
	./start.sh

down:  ## para receptor e painel
	./stop.sh

restart:  ## para e sobe de novo — use depois de editar código (`up` sozinho não recarrega processo já no ar)
	./restart.sh

status:  ## mostra o que está no ar
	./status.sh

win-dev:  ## Windows: sobe tudo (venv + deps + Postgres na imagem mais nova + schema + servicos). SEM_PAINEL=1 pula o painel
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/win-dev.ps1

db-up:  ## sobe o Postgres
	$(COMPOSE) up -d db

db-down:  ## derruba o Postgres
	$(COMPOSE) down

db-logs:
	$(COMPOSE) logs -f db

init:  ## cria/atualiza o schema
	$(PY) -m camucrm init

test:  ## suíte unitária (sem rede, sem Postgres)
	$(PY) -m unittest discover -s tests -p 'test_*.py'

# Fora de `make test` de propósito: prova o CHECK do teto de follow-ups (§6)
# contra Postgres real. Requer `make db-up`.
test-db:  ## teste de constraint contra Postgres real
	CAMU_TEST_DSN=$${CAMU_TEST_DSN:-postgresql://camu:camu@localhost:5433/camucrm} \
		$(PY) -m unittest discover -s tests/integration -p 'test_*.py' -v

fila:  ## a fila do dia — o comando que precisa ser rodado toda manhã
	$(PY) -m camucrm fila --motivos

extrair:  ## roda a extração sobre os blocos novos
	$(PY) -m camucrm extrair

recalcular:  ## reaplica as regras sem chamar LLM
	$(PY) -m camucrm recalcular

eval:  ## roda o eval contra o conjunto rotulado (§7)
	$(PY) -m camucrm eval

metricas:  ## os três números da §14
	$(PY) -m camucrm metricas

backfill:  ## importa e extrai o histórico. Ex.: make backfill ARQUIVO=dump.json
	$(PY) -m camucrm backfill --arquivo $(ARQUIVO) --extrair

painel:  ## sobe o painel web de leitura (127.0.0.1:8093, §13 antecipado)
	$(PY) -m camucrm painel

servir:  ## sobe o receptor de webhook da Evolution API
	$(PY) -m camucrm servir

acompanhar:  ## painel de terminal ao vivo (não é o painel da §13)
	$(PY) -m camucrm acompanhar

purgar:  ## retenção de mensagens e eventos brutos antigos (§12) — ver deploy/systemd/ para agendar
	$(PY) -m camucrm purgar
