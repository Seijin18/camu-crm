#!/usr/bin/env bash
# Reinicia o CRM depois de uma atualização de código.
#
# `./start.sh` sozinho NÃO basta para testar uma mudança de código: `subir()`
# (dentro de `start.sh`) é idempotente de propósito — se o processo já
# responde em `/health`, ele conta como "no ar" e não reinicia nada. Isso é
# correto para "subir o sistema sem duplicar processo", mas significa que
# receptor e painel continuam rodando o código de ANTES da edição até
# alguém pará-los e subir de novo. Encontrado na prática (2026-08-31):
# `python -m camucrm painel` já tinha o processo vivo, `./start.sh` disse
# "painel já no ar" e nunca recarregou o `db.py` corrigido.
#
# Este script só automatiza o par que resolve isso: `./stop.sh` + `./start.sh`.
# Nenhuma lógica nova — se um dia `start.sh`/`stop.sh` mudar de comportamento,
# este script muda de graça junto.
#
#   ./restart.sh              reinicia tudo (receptor + painel)
#   ./restart.sh --sem-painel reinicia só o receptor
set -uo pipefail
cd "$(dirname "$0")"

printf '\033[1mReiniciando o CRM (parar + subir, para o código novo entrar em vigor)\033[0m\n\n'

./stop.sh
echo
./start.sh "$@"
