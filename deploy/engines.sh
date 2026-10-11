#!/bin/bash
# This node's own GPU models (hosts/core.yaml, profile "engines": llm, comfyui, image) on or off. Run it on EVERY
# node, so the cluster is the same everywhere.
#   deploy/engines.sh off      # stop them and free their memory (a model that takes the Sparks whole: README "GLM")
#   deploy/engines.sh on       # start them again
#   deploy/engines.sh status
# The choice is COMPOSE_PROFILES in the host's .env, so it holds over a reboot; the watchdog is restarted to read
# it and starts what is missing. Everything else (gateway, balancer, database, embeddings, search) runs either way.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
dir=$(for d in "$root"/hosts/*/; do [ ! -f "$d.env" ] || echo "$d"; done | head -1)   # the host dir that has a .env
[ -n "$dir" ] || { echo "no hosts/<host>/.env on this machine"; exit 1; }
cd "$dir"
put() { if grep -qE "^$1=" .env; then sed -i -e "s#^$1=.*#$1=$2#" .env; else echo "$1=$2" >> .env; fi; }
case "${1:-status}" in
  on)  put COMPOSE_PROFILES engines ;;
  off) put COMPOSE_PROFILES ""
       docker compose --profile engines stop image comfyui llm 2>&1 | grep -v 'Container ' || true ;;
  status) grep -qE '^COMPOSE_PROFILES=.*engines' .env && echo "own GPU models: on" || echo "own GPU models: off"; exit 0 ;;
  *) echo "usage: $0 on|off|status"; exit 1 ;;
esac
systemctl --user restart inf-node
echo "own GPU models: $1 (journalctl --user -u inf-node -f)"
