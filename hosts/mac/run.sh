#!/bin/bash
# Start / stop the Mac inference host: MTPLX (LLM + embeddings, :8001, this Mac only) and the gateway (:8000).
#   ./run.sh start | stop | status | logs
# Needs: brew install youssofal/mtplx/mtplx, uv, ./fetch.sh done, .env with API_KEY.
# Where the models run is two settings in .env, each on its own:
#   LLM_PROVIDER=local|nearai          nearai: NEAR AI Cloud (NEARAI_API_KEY), MTPLX is not started
#   IMAGE_PROVIDER=local|comfy_cloud   comfy_cloud: services/image on :8002 renders on Comfy Cloud (COMFY_API_KEY,
#                                      IMAGE_QUALITY=low|medium|high), mflux is not loaded
# With both on a cloud the Mac needs no model files and no MTPLX.
# MTPLX refuses `response_format: json_object` without llguidance; `start` adds it to MTPLX's own venv.
set -uo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "no .env: cp .env.example .env and set API_KEY"; exit 1; }
set -a; . ./.env; set +a
MODELS_DIR=${MODELS_DIR:-$HOME/.cache/inf-mac/models}
LOGS=${LOGS:-$HOME/.cache/inf-mac/logs}; mkdir -p "$LOGS"
LLM_PORT=${LLM_PORT:-8001}; PORT=${PORT:-8000}
# The LLM pack: a folder or a Hugging Face id. Default: the small pack fetch.sh downloads.
LLM_PACK=${LLM_PACK:-$MODELS_DIR/Qwen3.5-4B-MTPLX-Optimized-Speed}
LLM_PROVIDER=${LLM_PROVIDER:-local}; IMAGE_PROVIDER=${IMAGE_PROVIDER:-local}; IMAGE_PORT=${IMAGE_PORT:-8002}
case "$LLM_PROVIDER" in local|nearai) ;; *) echo "LLM_PROVIDER must be local or nearai"; exit 1 ;; esac
case "$IMAGE_PROVIDER" in local|comfy_cloud) ;; *) echo "IMAGE_PROVIDER must be local or comfy_cloud"; exit 1 ;; esac
img_up() { curl -sf -m 5 "http://127.0.0.1:$IMAGE_PORT/health" 2>/dev/null | grep -q '"ok":true'; }
llm_up() { curl -sf -m 3 "http://127.0.0.1:$LLM_PORT/v1/models" 2>/dev/null | grep -q '"id":"llm"'; }
gw_up()  { curl -sf -m 3 "http://127.0.0.1:$PORT/v1/models" -H "authorization: Bearer $API_KEY" >/dev/null 2>&1; }

case "${1:-status}" in
  start)
    if [ "$LLM_PROVIDER" = nearai ]; then
      [ -n "${NEARAI_API_KEY:-}" ] || { echo "LLM_PROVIDER=nearai needs NEARAI_API_KEY in .env"; exit 1; }
      NEARAI_URL=${NEARAI_URL:-https://cloud-api.near.ai}
      export LLM_URL="$NEARAI_URL" LLM_KEY="$NEARAI_API_KEY" LLM_MODEL="${NEARAI_MODEL:-Qwen/Qwen3.6-35B-A3B-FP8}" \
             EMBED_MODEL="${NEARAI_EMBED_MODEL:-Qwen/Qwen3-Embedding-0.6B}" LLM_VISION="${LLM_VISION:-1}" \
             LLM_HEALTH_PATH=/v1/models LLM_ATTESTATION_URL="$NEARAI_URL/v1/attestation/report"
    elif ! llm_up; then
      venv=$(ls -d /opt/homebrew/var/mtplx/venv-* 2>/dev/null | sort -V | tail -1)
      [ -n "$venv" ] && "$venv/bin/python3" -c "import llguidance" 2>/dev/null || "$venv/bin/python3" -m pip install -q llguidance
      # MTPLX keeps finished conversations in memory to resume them (up to 25 GB on a 64 GB Mac). Projects here
      # send one-off requests, so that memory is better left to the picture model and Docker: without the cap
      # MTPLX refused a request with "insufficient memory" (HTTP 507) on 2026-10-10.
      MTPLX_SESSION_BANK_MAX_BYTES=${MTPLX_SESSION_BANK_MAX_BYTES:-2147483648} \
      nohup mtplx serve --model "$LLM_PACK" --model-id llm --context-window "${LLM_CONTEXT:-32768}" \
        --embedding-model "$MODELS_DIR/Qwen3-Embedding-0.6B-4bit-DWQ=qwen3-embedding-0.6b" \
        --host 127.0.0.1 --port "$LLM_PORT" --no-auth --reasoning off --yes > "$LOGS/mtplx.log" 2>&1 &
      echo $! > "$LOGS/mtplx.pid"
      for i in $(seq 300); do llm_up && break; sleep 2; done
      llm_up && echo "llm up (:$LLM_PORT)" || { echo "llm did NOT start: $LOGS/mtplx.log"; exit 1; }
    fi
    if [ "$IMAGE_PROVIDER" = comfy_cloud ]; then
      [ -n "${COMFY_API_KEY:-}" ] || { echo "IMAGE_PROVIDER=comfy_cloud needs COMFY_API_KEY in .env"; exit 1; }
      if ! img_up; then
        ( cd ../../services/image && IMAGE_BACKEND=comfy_cloud BIND=127.0.0.1 \
            nohup uv run --project ../../hosts/mac --with python-multipart==0.0.20 \
            uvicorn app:app --host 127.0.0.1 --port "$IMAGE_PORT" > "$LOGS/image.log" 2>&1 & echo $! > "$LOGS/image.pid" )
        for i in $(seq 60); do img_up && break; sleep 1; done
        img_up && echo "image up (:$IMAGE_PORT, Comfy Cloud, quality ${IMAGE_QUALITY:-high})" || { echo "image did NOT start: $LOGS/image.log"; exit 1; }
      fi
      export IMAGE_URL="http://127.0.0.1:$IMAGE_PORT" IMAGE_KEY="$API_KEY"
    fi
    if ! gw_up; then
      LLM_URL="${LLM_URL:-http://127.0.0.1:$LLM_PORT}" LLM_MODEL="${LLM_MODEL:-llm}" IMAGE_MODEL_PATH="${IMAGE_MODEL_PATH:-$MODELS_DIR/flux2-klein-4b-mflux-q4}" \
        nohup uv run python gateway.py > "$LOGS/gateway.log" 2>&1 &
      echo $! > "$LOGS/gateway.pid"
      for i in $(seq 60); do gw_up && break; sleep 1; done
      gw_up && echo "gateway up (:$PORT)" || { echo "gateway did NOT start: $LOGS/gateway.log"; exit 1; }
    fi ;;
  stop)
    for n in gateway image mtplx; do [ -f "$LOGS/$n.pid" ] && pkill -P "$(cat "$LOGS/$n.pid")" 2>/dev/null; [ -f "$LOGS/$n.pid" ] && kill "$(cat "$LOGS/$n.pid")" 2>/dev/null; rm -f "$LOGS/$n.pid"; done
    pkill -f "mtplx.*serve.*--port $LLM_PORT" 2>/dev/null; pkill -f "python gateway.py" 2>/dev/null; pkill -f "uvicorn app:app --host 127.0.0.1 --port $IMAGE_PORT" 2>/dev/null; echo stopped ;;
  status)
    echo "llm      $LLM_PROVIDER   image    $IMAGE_PROVIDER"
    [ "$LLM_PROVIDER" = local ] && { llm_up && echo "llm      up" || echo "llm      DOWN"; }
    [ "$IMAGE_PROVIDER" = comfy_cloud ] && { img_up && echo "image    up" || echo "image    DOWN"; }
    gw_up && curl -s -m 3 "http://127.0.0.1:$PORT/health" || echo "gateway  DOWN"; echo ;;
  logs) tail -n 40 -f "$LOGS"/*.log ;;
  *) echo "usage: $0 start|stop|status|logs"; exit 1 ;;
esac
