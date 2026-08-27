#!/usr/bin/env bash
# Aponta o webhook da Evolution para este CRM.
#
# Separado do start.sh de propósito: reapontar o webhook tira as mensagens de
# quem estava recebendo antes (hoje, o WhatBot). Isso é decisão, não rotina de
# inicialização, e por isso exige rodar um comando com esse nome.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && { set -a; source .env; set +a; }

INSTANCIA="${EVOLUTION_INSTANCE:-camu_whatsapp}"
PORTA="${CAMU_WEBHOOK_PORT:-8091}"
# Endereço do host visto de dentro do container da Evolution. Detectado da
# rede em que ela roda, e não fixo: o IP do gateway muda quando o compose
# recria a rede, e um valor obsoleto aqui faz as mensagens sumirem em silêncio.
if [[ -z "${CAMU_HOST_GATEWAY:-}" ]]; then
  REDE=$(docker inspect evolution_api -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{break}}{{end}}' 2>/dev/null || echo "")
  GATEWAY=$(docker network inspect "$REDE" -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo "")
  GATEWAY="${GATEWAY:-172.19.0.1}"
else
  GATEWAY="$CAMU_HOST_GATEWAY"
fi

echo "Apontando o webhook de $INSTANCIA para http://$GATEWAY:$PORTA/webhook/evolution"
[[ -f data/backup/webhook-$INSTANCIA-original.json ]] || {
  mkdir -p data/backup
  curl -s -H "apikey: $EVOLUTION_API_KEY" \
    "${EVOLUTION_API_BASE_URL:-http://localhost:8080}/webhook/find/$INSTANCIA" \
    > "data/backup/webhook-$INSTANCIA-original.json"
  echo "Configuração anterior salva em data/backup/"
}

curl -s -X POST "${EVOLUTION_API_BASE_URL:-http://localhost:8080}/webhook/set/$INSTANCIA" \
  -H "apikey: $EVOLUTION_API_KEY" -H "Content-Type: application/json" \
  -d "{\"webhook\":{\"enabled\":true,\"url\":\"http://$GATEWAY:$PORTA/webhook/evolution\",\"webhookByEvents\":false,\"webhookBase64\":false,\"events\":[\"MESSAGES_UPSERT\"],\"headers\":{\"x-camu-token\":\"${CAMU_WEBHOOK_TOKEN:-}\"}}}" \
  >/dev/null && echo "Pronto."
