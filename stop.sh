#!/usr/bin/env bash
# Para os serviços do CRM. Não derruba o Postgres nem a Evolution.
#
# A separação é deliberada: o banco guarda o histórico e a Evolution segura o
# pareamento do chip. Derrubar a Evolution a cada parada do CRM significaria
# repareamento frequente, e §11 já avisa que o chip é a parte frágil.
#
#   ./stop.sh          para receptor e painel
#   ./stop.sh --tudo   para também o Postgres
set -uo pipefail
cd "$(dirname "$0")"
LOGS="$PWD/logs"

ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
aviso() { printf '  \033[33m!\033[0m %s\n' "$*"; }

parar() {
  local nome="$1" pidfile="$LOGS/$1.pid"
  local pid=""
  [[ -f "$pidfile" ]] && pid=$(cat "$pidfile" 2>/dev/null)

  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    # SIGTERM primeiro: uvicorn fecha as conexões em curso em vez de cortar
    # um webhook no meio, o que perderia a mensagem que estava chegando.
    for _ in {1..10}; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -9 "$pid" 2>/dev/null
    rm -f "$pidfile"
    ok "$nome parado"
  elif pgrep -f "camucrm $2" >/dev/null 2>&1; then
    pkill -f "camucrm $2" && ok "$nome parado (sem pidfile)"
    rm -f "$pidfile"
  else
    aviso "$nome já estava parado"
    rm -f "$pidfile"
  fi
}

printf '\033[1mParando o CRM\033[0m\n'
parar receptor servir
parar painel painel

if [[ "${1:-}" == "--tudo" ]]; then
  docker compose stop db >/dev/null 2>&1 && ok "Postgres parado"
  aviso "Evolution mantida no ar (parar derrubaria o pareamento do chip)"
fi
echo
