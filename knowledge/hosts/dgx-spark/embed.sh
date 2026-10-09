#!/usr/bin/env bash
# Shared bge-m3 embedding service on the Spark — hosted for OTHER projects on the tailnet
# (e.g. the books AI system on the student PC), NOT strictly part of the ad pipeline.
# Reuses product_dream's already-built svc-embed image (BAAI/bge-m3, OpenAI-compatible).
# Bound on 0.0.0.0 so tailnet peers reach it at http://100.64.0.1:${EMBED_PORT}.
#
# Usage: scripts/embed.sh {up|down|status|logs}
set -uo pipefail

EMBED_PORT="${EMBED_PORT:-8011}"
EMBED_NAME="${EMBED_NAME:-spark-embed}"
EMBED_IMAGE="${EMBED_IMAGE:-product_dream-svc-embed:latest}"
HF_CACHE="${HF_CACHE:-/home/server/.cache/huggingface}"
HF_TOKEN="${HF_TOKEN:-}"
TAILNET_IP="$(tailscale ip -4 2>/dev/null | head -1)"

up() {
  if docker ps --format '{{.Names}}' | grep -q "^${EMBED_NAME}$"; then
    echo "embed: already running (:${EMBED_PORT})"; return
  fi
  echo "embed: starting bge-m3 (:${EMBED_PORT}) — first boot downloads ~2.3GB…"
  docker run -d --name "$EMBED_NAME" $([ "${EMBED_DEVICE:-cpu}" = cuda ] && echo --gpus all) --restart unless-stopped \
    -p "${EMBED_PORT}:${EMBED_PORT}" \
    -e PORT="$EMBED_PORT" -e DEVICE="${EMBED_DEVICE:-cpu}" -e EMBED_MODEL_ID=BAAI/bge-m3 -e MAX_LEN=1024 \
    -e HF_HOME=/data/models -e HF_TOKEN="$HF_TOKEN" \
    -v "$HF_CACHE":/data/models \
    "$EMBED_IMAGE" >/dev/null && echo "embed: up. tailnet: http://${TAILNET_IP:-100.64.0.1}:${EMBED_PORT}"
}

down()   { docker rm -f "$EMBED_NAME" 2>/dev/null && echo "embed: stopped" || echo "embed: not running"; }
logs()   { docker logs --tail 40 "$EMBED_NAME" 2>&1; }
status() {
  if docker ps --format '{{.Names}}' | grep -q "^${EMBED_NAME}$"; then
    echo "embed: UP  http://${TAILNET_IP:-100.64.0.1}:${EMBED_PORT}"
    curl -s -m 5 "http://localhost:${EMBED_PORT}/health" && echo
  else echo "embed: down"; fi
}

case "${1:-}" in
  up) up ;; down) down ;; status) status ;; logs) logs ;;
  *) echo "usage: $0 {up|down|status|logs}"; exit 1 ;;
esac
