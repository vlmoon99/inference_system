#!/usr/bin/env bash
# Qwen3-Embedding-0.6B on the Spark's GPU (:8013) — the embeddings behind the public LLM
# gateway's /v1/embeddings (backend llm_gateway; docs/LLM_API.md). Separate from the shared
# bge-m3 (:8011): clients that also use NEAR AI Cloud need the exact same model's vectors.
# Reuses product_dream's svc-embed image for its sm_121 torch; the app is mounted from
# inference/adapters/embed-qwen3. ~1.5 GB on the GPU. --restart unless-stopped: back after a reboot.
#
# Usage: embed-qwen3.sh {up|down|status|logs}
set -uo pipefail
HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$HOST_DIR/../../adapters/embed-qwen3" && pwd)"
PORT="${EMBED_QWEN3_PORT:-8013}"
NAME="${EMBED_QWEN3_NAME:-spark-embed-qwen3}"
IMAGE="${EMBED_IMAGE:-product_dream-svc-embed:latest}"
HF_CACHE="${HF_CACHE:-/home/server/.cache/huggingface}"

up() {
  docker ps --format '{{.Names}}' | grep -q "^${NAME}$" && { echo "embed-qwen3: already running (:$PORT)"; return; }
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" --gpus all --restart unless-stopped -p "$PORT:$PORT" \
    -e DEVICE=cuda -e HF_HOME=/data/models -e HF_TOKEN="${HF_TOKEN:-}" \
    -v "$HF_CACHE":/data/models -v "$APP_DIR":/svc:ro -w /svc \
    --entrypoint /opt/nvidia/nvidia_entrypoint.sh \
    "$IMAGE" uvicorn app:app --host 0.0.0.0 --port "$PORT" >/dev/null \
    && echo "embed-qwen3: up on :$PORT (first start downloads ~1.2 GB, then warms)"
}
down()   { docker rm -f "$NAME" >/dev/null 2>&1 && echo "embed-qwen3: stopped" || echo "embed-qwen3: not running"; }
logs()   { docker logs --tail 40 "$NAME" 2>&1; }
status() { curl -s -m 5 "http://localhost:$PORT/health" && echo || echo "embed-qwen3: down"; }

case "${1:-}" in
  up) up ;; down) down ;; status) status ;; logs) logs ;;
  *) echo "usage: $0 {up|down|status|logs}"; exit 1 ;;
esac
