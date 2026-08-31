.ONESHELL:
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

.PHONY: help up up-sem-painel down status db-up db-down db-logs init test test-db fila extrair recalcular eval metricas backfill lint painel servir acompanhar

# venv portável: Linux/macOS usam .venv/bin, Windows usa .venv/Scripts
PY := $(shell [ -x .venv/Scripts/python.exe ] && echo .venv/Scripts/python.exe || echo .venv/bin/python)
COMPOSE = docker compose

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

up:  ## sobe o sistema inteiro (banco, transporte, receptor, painel)
	$(MAKE) _subir COM_PAINEL=1

up-sem-painel:  ## sobe apenas a recepção, sem a interface web
	$(MAKE) _subir COM_PAINEL=0

_subir:
	RAIZ="$$PWD"; LOGS="$$RAIZ/logs"
	mkdir -p "$$LOGS"
	COM_PAINEL="$${COM_PAINEL:-1}"; INSTANCIA="$${EVOLUTION_INSTANCE:-camu_whatsapp}"
	PORTA_WEBHOOK="$${CAMU_WEBHOOK_PORT:-8091}"; PORTA_PAINEL="$${CAMU_PAINEL_PORT:-8093}"
	EVOLUTION_CONTAINER="evolution_api"; EVOLUTION_DB_CONTAINER="whatbot-evolution-db-1"; falhou=0
	ok() { printf '  \033[32m✓\033[0m %s\n' "$$*"; }; aviso() { printf '  \033[33m!\033[0m %s\n' "$$*"; }; erro() { printf '  \033[31m✗\033[0m %s\n' "$$*"; }; etapa() { printf '\n\033[1m%s\033[0m\n' "$$*"; }

	etapa "Configuração"
	PY="$$RAIZ/.venv/Scripts/python.exe"; [[ -x "$$PY" ]] || PY="$$RAIZ/.venv/bin/python"
	if [[ ! -x "$$PY" ]]; then
		aviso "venv ausente — criando em .venv/"
		PYSYS="$$(command -v python3 || command -v python || true)"
		if [[ -z "$$PYSYS" ]]; then erro "Python não encontrado no PATH — instale o Python 3.12+"; exit 1; fi
		"$$PYSYS" -m venv .venv || { erro "falha criando a venv"; exit 1; }
		PY="$$RAIZ/.venv/Scripts/python.exe"; [[ -x "$$PY" ]] || PY="$$RAIZ/.venv/bin/python"
	fi
	if ! "$$PY" -c 'import psycopg, google.genai, fastapi, dotenv' >/dev/null 2>&1; then
		aviso "instalando dependências (requirements.txt)"
		"$$PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
		if ! "$$PY" -m pip install -q -r requirements.txt; then erro "falha instalando dependências"; exit 1; fi
	fi
	ok "venv"
	if [[ ! -f .env ]]; then erro ".env não existe. Copie .env.example e preencha CAMU_TELEFONE_SALT e GEMINI_API_KEY"; exit 1; fi
	set -a; source ./.env; set +a; ok ".env carregado"
	if [[ -z "$${CAMU_TELEFONE_SALT:-}" ]]; then erro "CAMU_TELEFONE_SALT vazio — o hash de telefone precisa de salt estável"; exit 1; fi; ok "salt de telefone definido"

	etapa "Banco"
	DB_HOST="$$("$$PY" -c 'from camucrm import config; import re; m = re.search(r"@([^:/?]+)", config.dsn()); print(m.group(1) if m else "?")' 2>/dev/null || echo '?')"
	if [[ "$$DB_HOST" == "localhost" || "$$DB_HOST" == "127.0.0.1" ]]; then
		if docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep -q "camucrm_db.*Up"; then ok "Postgres local já no ar"; else docker compose up -d db >/dev/null 2>&1 && ok "Postgres local subindo" || { erro "falha ao subir o Postgres local"; exit 1; }; fi
		if timeout 60 bash -c 'until docker exec camucrm_db pg_isready -U camu -d camucrm >/dev/null 2>&1; do sleep 1; done'; then ok "Postgres local aceitando conexão"; else erro "Postgres local não ficou pronto em 60s"; exit 1; fi
	else
		if saida="$$("$$PY" -c 'from camucrm import config; import psycopg; psycopg.connect(config.dsn(), connect_timeout=8).close()' 2>&1)"; then ok "banco remoto respondendo ($$DB_HOST)"; else erro "não conectou no banco remoto ($$DB_HOST):"; printf '%s\n' "$$saida" | tail -3 | sed 's/^/      /'; exit 1; fi
	fi
	if saida="$$("$$PY" -m camucrm init 2>&1)"; then ok "schema aplicado"; else erro "falha aplicando o schema:"; printf '%s\n' "$$saida" | sed 's/^/      /'; exit 1; fi

	etapa "Transporte (WhatsApp)"
	if ! docker ps --format '{{.Names}}' | grep -qx "$$EVOLUTION_DB_CONTAINER"; then docker start "$$EVOLUTION_DB_CONTAINER" >/dev/null 2>&1 && { ok "banco da Evolution iniciado"; sleep 5; docker restart "$$EVOLUTION_CONTAINER" >/dev/null 2>&1 && ok "Evolution reiniciada para reconectar ao banco" || true; } || aviso "banco da Evolution ($$EVOLUTION_DB_CONTAINER) não encontrado — o WhatsApp ficará fora"; else ok "banco da Evolution no ar"; fi
	if ! docker ps --format '{{.Names}}' | grep -qx "$$EVOLUTION_CONTAINER"; then docker start "$$EVOLUTION_CONTAINER" >/dev/null 2>&1 && ok "Evolution iniciada" || aviso "container $$EVOLUTION_CONTAINER não encontrado"; fi
	if timeout 90 bash -c 'until curl -sf -m 2 http://localhost:8080/ >/dev/null 2>&1; do sleep 3; done'; then
		ok "Evolution API respondendo"
		ESTADO="$$(curl -s -m 5 -H "apikey: $${EVOLUTION_API_KEY:-}" "http://localhost:8080/instance/connectionState/$$INSTANCIA" 2>/dev/null | "$$PY" -c 'import json,sys; print(json.load(sys.stdin)["instance"]["state"])' 2>/dev/null || echo '?')"
		if [[ "$$ESTADO" == open ]]; then ok "instância $$INSTANCIA pareada"; else aviso "instância $$INSTANCIA em '$$ESTADO' — pareie com: $$PY scripts/parear.py"; falhou=1; fi
		URL_WEBHOOK="$$(curl -s -m 5 -H "apikey: $${EVOLUTION_API_KEY:-}" "http://localhost:8080/webhook/find/$$INSTANCIA" 2>/dev/null | "$$PY" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))' 2>/dev/null || echo '')"
		if [[ "$$URL_WEBHOOK" == *":$$PORTA_WEBHOOK/webhook/evolution" ]]; then ok "webhook aponta para o CRM"; else aviso "webhook aponta para '$${URL_WEBHOOK:-nada}' — as mensagens NÃO chegam aqui"; aviso "conserte com: ./scripts/apontar_webhook.sh"; falhou=1; fi
	else aviso "Evolution API não respondeu — o CRM sobe, mas sem mensagens novas"; falhou=1; fi

	etapa "Serviços do CRM"
	subir() { local nome="$$1" porta="$$2" pidfile="$$LOGS/$$1.pid"; shift 2; if curl -sf -m 2 "http://localhost:$$porta/health" >/dev/null 2>&1; then ok "$$nome já no ar (:$$porta)"; return; fi; nohup "$$@" >>"$$LOGS/$$nome.log" 2>&1 & echo $$! > "$$pidfile"; if timeout 40 bash -c "until curl -sf -m 2 http://localhost:$$porta/health >/dev/null 2>&1; do sleep 1; done"; then ok "$$nome no ar (:$$porta)"; else erro "$$nome não respondeu em 40s — veja logs/$$nome.log"; falhou=1; fi; }
	subir receptor "$$PORTA_WEBHOOK" "$$PY" -m camucrm servir
	[[ "$$COM_PAINEL" -eq 1 ]] && subir painel "$$PORTA_PAINEL" "$$PY" -m camucrm painel

	etapa "Pronto"; echo "  Painel     http://localhost:$$PORTA_PAINEL"; echo "  Receptor   http://localhost:$$PORTA_WEBHOOK"; echo "  Fila       $$PY -m camucrm fila"; echo "  Logs       tail -f logs/receptor.log"; echo "  Parar      ./stop.sh"
	if [[ "$$falhou" -eq 1 ]]; then printf '\n\033[33mSubiu com ressalvas — veja os avisos acima.\033[0m\n'; exit 2; fi

down:  ## para receptor e painel
	./stop.sh

status:  ## mostra o que está no ar
	./status.sh

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
