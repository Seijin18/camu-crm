#!/usr/bin/env bash
# Sobe o CRM inteiro, na ordem de dependência, e conta o que encontrou.
#
# Idempotente: rodar duas vezes não duplica processo nem reaplica o que já
# está de pé. Cada etapa é verificada em vez de assumida — subir metade do
# sistema e não dizer qual metade é pior que não subir nada, porque a fila
# parece vazia em vez de parecer quebrada.
#
#   ./start.sh              sobe tudo
#   ./start.sh --sem-painel sobe só a recepção (útil em servidor sem UI)
set -uo pipefail

cd "$(dirname "$0")"
RAIZ="$PWD"
PY="$RAIZ/.venv/bin/python"
LOGS="$RAIZ/logs"
mkdir -p "$LOGS"

COM_PAINEL=1
[[ "${1:-}" == "--sem-painel" ]] && COM_PAINEL=0

# A Evolution API e o banco dela vivem no compose do WhatBot. Este projeto só
# depende deles; não os gerencia (§11: o transporte é externo e frágil por
# natureza). Os nomes ficam aqui para o diagnóstico ser específico.
EVOLUTION_CONTAINER="evolution_api"
EVOLUTION_DB_CONTAINER="whatbot-evolution-db-1"
INSTANCIA="${EVOLUTION_INSTANCE:-camu_whatsapp}"

ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
aviso() { printf '  \033[33m!\033[0m %s\n' "$*"; }
erro()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
etapa() { printf '\n\033[1m%s\033[0m\n' "$*"; }

falhou=0

# --------------------------------------------------------------------------
etapa "Configuração"

if [[ ! -x "$PY" ]]; then
  erro "venv ausente em .venv/ — veja o README para criá-la"
  exit 1
fi
ok "venv"

if [[ ! -f .env ]]; then
  erro ".env não existe. Copie .env.example e preencha CAMU_TELEFONE_SALT e GEMINI_API_KEY"
  exit 1
fi
set -a; source .env; set +a
ok ".env carregado"

if [[ -z "${CAMU_TELEFONE_SALT:-}" ]]; then
  erro "CAMU_TELEFONE_SALT vazio — o hash de telefone (§12) precisa de salt estável"
  exit 1
fi
ok "salt de telefone definido"

PORTA_WEBHOOK="${CAMU_WEBHOOK_PORT:-8091}"
PORTA_PAINEL="${CAMU_PAINEL_PORT:-8093}"

# --------------------------------------------------------------------------
etapa "Banco"

# `CAMU_DB_DSN` decide qual banco é usado — nunca assumir que é o Postgres
# local do docker-compose. Assumir isso escondeu, de verdade, uma migração
# para o Supabase: este script dizia "Postgres no ar" testando o container
# certo enquanto o processo real conectava em outro banco, e não havia
# nenhum sinal de que os dois tinham se desalinhado.
DB_HOST=$("$PY" -c "
from camucrm import config
import re
m = re.search(r'@([^:/?]+)', config.dsn())
print(m.group(1) if m else '?')
" 2>/dev/null || echo "?")

if [[ "$DB_HOST" == "localhost" || "$DB_HOST" == "127.0.0.1" ]]; then
  if docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep -q "camucrm_db.*Up"; then
    ok "Postgres local já no ar"
  else
    docker compose up -d db >/dev/null 2>&1 && ok "Postgres local subindo" \
      || { erro "falha ao subir o Postgres local"; exit 1; }
  fi
  # `pg_isready` e não um sleep fixo: o schema aplicado contra um banco ainda
  # subindo falha de formas que parecem bug de aplicação.
  if timeout 60 bash -c 'until docker exec camucrm_db pg_isready -U camu -d camucrm >/dev/null 2>&1; do sleep 1; done'; then
    ok "Postgres local aceitando conexão"
  else
    erro "Postgres local não ficou pronto em 60s"; exit 1
  fi
else
  # Banco remoto (Supabase ou outro) — nada para subir aqui; só confirmar
  # que dá para conectar antes de tentar aplicar o schema.
  if saida=$("$PY" -c "
from camucrm import config
import psycopg
psycopg.connect(config.dsn(), connect_timeout=8).close()
" 2>&1); then
    ok "banco remoto respondendo ($DB_HOST)"
  else
    erro "não conectou no banco remoto ($DB_HOST):"
    printf '%s\n' "$saida" | tail -3 | sed 's/^/      /'
    exit 1
  fi
fi

# A saída é capturada e mostrada só em caso de falha. Descartá-la esconderia
# a causa exatamente quando ela importa — foi o que aconteceu na primeira
# execução deste script, e o erro teve de ser reproduzido à mão.
if saida=$("$PY" -m camucrm init 2>&1); then
  ok "schema aplicado"
else
  erro "falha aplicando o schema:"
  printf '%s\n' "$saida" | sed 's/^/      /'
  exit 1
fi

# --------------------------------------------------------------------------
etapa "Transporte (WhatsApp)"

if ! docker ps --format '{{.Names}}' | grep -qx "$EVOLUTION_DB_CONTAINER"; then
  if docker start "$EVOLUTION_DB_CONTAINER" >/dev/null 2>&1; then
    ok "banco da Evolution iniciado"
    sleep 5
    docker restart "$EVOLUTION_CONTAINER" >/dev/null 2>&1 && ok "Evolution reiniciada para reconectar ao banco"
  else
    aviso "banco da Evolution ($EVOLUTION_DB_CONTAINER) não encontrado — o WhatsApp ficará fora"
  fi
else
  ok "banco da Evolution no ar"
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$EVOLUTION_CONTAINER"; then
  docker start "$EVOLUTION_CONTAINER" >/dev/null 2>&1 && ok "Evolution iniciada" \
    || aviso "container $EVOLUTION_CONTAINER não encontrado"
fi

if timeout 90 bash -c 'until curl -sf -m 2 http://localhost:8080/ >/dev/null 2>&1; do sleep 3; done'; then
  ok "Evolution API respondendo"

  ESTADO=$(curl -s -m 5 -H "apikey: ${EVOLUTION_API_KEY:-}" \
    "http://localhost:8080/instance/connectionState/$INSTANCIA" 2>/dev/null \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["instance"]["state"])' 2>/dev/null || echo "?")
  case "$ESTADO" in
    open) ok "instância $INSTANCIA pareada" ;;
    *)    aviso "instância $INSTANCIA em '$ESTADO' — pareie com: $PY scripts/parear.py"
          falhou=1 ;;
  esac

  URL_WEBHOOK=$(curl -s -m 5 -H "apikey: ${EVOLUTION_API_KEY:-}" \
    "http://localhost:8080/webhook/find/$INSTANCIA" 2>/dev/null \
    | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("url",""))' 2>/dev/null || echo "")
  if [[ "$URL_WEBHOOK" == *":$PORTA_WEBHOOK/webhook/evolution" ]]; then
    ok "webhook aponta para o CRM"
  else
    aviso "webhook aponta para '${URL_WEBHOOK:-nada}' — as mensagens NÃO chegam aqui"
    aviso "conserte com: ./scripts/apontar_webhook.sh"
    falhou=1
  fi
else
  aviso "Evolution API não respondeu — o CRM sobe, mas sem mensagens novas"
  falhou=1
fi

# --------------------------------------------------------------------------
etapa "Serviços do CRM"

subir() {
  local nome="$1" porta="$2" pidfile="$LOGS/$1.pid"; shift 2
  if curl -sf -m 2 "http://localhost:$porta/health" >/dev/null 2>&1; then
    ok "$nome já no ar (:$porta)"
    return 0
  fi
  nohup "$@" >>"$LOGS/$nome.log" 2>&1 &
  echo $! > "$pidfile"
  if timeout 40 bash -c "until curl -sf -m 2 http://localhost:$porta/health >/dev/null 2>&1; do sleep 1; done"; then
    ok "$nome no ar (:$porta)"
  else
    erro "$nome não respondeu em 40s — veja logs/$nome.log"
    falhou=1
  fi
}

subir receptor "$PORTA_WEBHOOK" "$PY" -m camucrm servir
[[ $COM_PAINEL -eq 1 ]] && subir painel "$PORTA_PAINEL" "$PY" -m camucrm painel

# --------------------------------------------------------------------------
etapa "Pronto"
echo "  Painel     http://localhost:$PORTA_PAINEL"
echo "  Receptor   http://localhost:$PORTA_WEBHOOK"
echo "  Fila       $PY -m camucrm fila"
echo "  Logs       tail -f logs/receptor.log"
echo "  Parar      ./stop.sh"

if [[ $falhou -eq 1 ]]; then
  printf '\n\033[33mSubiu com ressalvas — veja os avisos acima.\033[0m\n'
  exit 2
fi
echo
