#!/usr/bin/env bash
# Diz, em uma tela, se o sistema está inteiro — e o que falta se não estiver.
set -uo pipefail
cd "$(dirname "$0")"
PY="$PWD/.venv/bin/python"
[[ -f .env ]] && { set -a; source .env; set +a; }
PORTA_WEBHOOK="${CAMU_WEBHOOK_PORT:-8091}"
PORTA_PAINEL="${CAMU_PAINEL_PORT:-8093}"
INSTANCIA="${EVOLUTION_INSTANCE:-camu_whatsapp}"

linha() { printf '  %-22s %s\n' "$1" "$2"; }
sim()   { printf '\033[32m%s\033[0m' "$1"; }
nao()   { printf '\033[31m%s\033[0m' "$1"; }

printf '\033[1mcamu-crm\033[0m\n'

docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep -q 'camucrm_db.*Up' \
  && linha "Postgres" "$(sim 'no ar')" || linha "Postgres" "$(nao 'fora')"

curl -sf -m 2 "http://localhost:$PORTA_WEBHOOK/health" >/dev/null 2>&1 \
  && linha "Receptor" "$(sim "no ar :$PORTA_WEBHOOK")" || linha "Receptor" "$(nao 'fora')"

curl -sf -m 2 "http://localhost:$PORTA_PAINEL/health" >/dev/null 2>&1 \
  && linha "Painel" "$(sim "http://localhost:$PORTA_PAINEL")" || linha "Painel" "$(nao 'fora')"

# 8s e não 3: a Evolution demora a responder logo depois de reiniciar, e um
# timeout curto reporta "fora" para um serviço que está apenas subindo — o que
# manda alguém investigar a coisa errada.
if curl -sf -m 8 http://localhost:8080/ >/dev/null 2>&1; then
  ESTADO=$(curl -s -m 5 -H "apikey: ${EVOLUTION_API_KEY:-}" \
    "http://localhost:8080/instance/connectionState/$INSTANCIA" 2>/dev/null \
    | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["instance"]["state"])' 2>/dev/null || echo "?")
  [[ "$ESTADO" == "open" ]] && linha "WhatsApp" "$(sim "pareado ($INSTANCIA)")" \
                            || linha "WhatsApp" "$(nao "$ESTADO") — pareie: $PY scripts/parear.py"
  URL=$(curl -s -m 5 -H "apikey: ${EVOLUTION_API_KEY:-}" \
    "http://localhost:8080/webhook/find/$INSTANCIA" 2>/dev/null \
    | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("url",""))' 2>/dev/null || echo "")
  [[ "$URL" == *":$PORTA_WEBHOOK/webhook/evolution" ]] \
    && linha "Webhook" "$(sim 'aponta para o CRM')" \
    || linha "Webhook" "$(nao "${URL:-não configurado}")"
else
  linha "Evolution API" "$(nao 'não respondeu') — se acabou de reiniciar, aguarde"
fi

if docker exec camucrm_db pg_isready -U camu -d camucrm >/dev/null 2>&1; then
  read -r CONV MSG < <(docker exec camucrm_db psql -U camu -d camucrm -tAc \
    "SELECT (SELECT count(*) FROM conversas WHERE resultado IS NULL), (SELECT count(*) FROM mensagens)" 2>/dev/null | tr '|' ' ')
  linha "Dados" "${CONV:-0} conversa(s) aberta(s), ${MSG:-0} mensagem(ns)"
fi
echo
