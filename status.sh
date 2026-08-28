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
aviso() { printf '\033[33m%s\033[0m' "$1"; }

printf '\033[1mcamu-crm\033[0m\n'

# Testa o banco que `CAMU_DB_DSN` de fato aponta — nunca assume Postgres
# local via docker. Rodar isto contra o container local enquanto o `.env`
# apontava para outro banco (Supabase, numa migração) foi o que escondeu o
# problema real de "o painel não carrega": o script dizia "tudo ok" porque
# testava o banco errado. Uma única consulta ao banco real também traz a
# contagem — nada de round-trip extra.
DB_INFO=$("$PY" - <<'PYEOF' 2>&1
import os, re, sys, time
try:
    from camucrm import config
except Exception as e:
    print(f"ERRO|import camucrm falhou: {e}")
    sys.exit(0)
dsn = config.dsn()
host = re.search(r'@([^:/?]+)', dsn)
host = host.group(1) if host else "?"
try:
    import psycopg
    t0 = time.time()
    conn = psycopg.connect(dsn, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT count(*) FROM conversas WHERE resultado IS NULL), "
            "(SELECT count(*) FROM mensagens)"
        )
        conv, msg = cur.fetchone()
    conn.close()
    print(f"OK|{host}|{conv}|{msg}|{time.time()-t0:.1f}")
except Exception as e:
    print(f"ERRO|{host}|{type(e).__name__}: {str(e)[:120]}")
PYEOF
)
IFS='|' read -r DB_STATUS DB_HOST DB_A DB_B DB_C <<< "$DB_INFO"

if [[ "$DB_STATUS" == "OK" ]]; then
  linha "Banco" "$(sim "no ar") ($DB_HOST, conexão em ${DB_C}s)"
  linha "Dados" "${DB_A} conversa(s) aberta(s), ${DB_B} mensagem(ns)"
  if awk "BEGIN{exit !($DB_C > 3)}"; then
    linha "" "$(aviso "conexão lenta (${DB_C}s) — se o painel demorar, é a mesma causa")"
  fi
else
  linha "Banco" "$(nao "falhou") ($DB_HOST) — $DB_A"
fi

# Postgres local via docker: informativo à parte, não é "o banco em uso" a
# menos que CAMU_DB_DSN aponte pra localhost/5433 — ver acima para o que
# está de fato configurado.
if docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep -q 'camucrm_db.*Up'; then
  if [[ "$DB_HOST" != "localhost" && "$DB_HOST" != "127.0.0.1" ]]; then
    linha "Postgres local (docker)" "$(aviso 'no ar, mas não é o banco em uso agora')"
  fi
fi

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
echo
