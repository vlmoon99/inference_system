#!/bin/bash
# Start / stop the Mac inference host: MTPLX (LLM + embeddings, :8001, this Mac only) and the gateway (:8000).
#   ./run.sh start | stop | status | logs
# Needs: brew install youssofal/mtplx/mtplx, uv, ./fetch.sh done, .env with API_KEY.
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
llm_up() { curl -sf -m 3 "http://127.0.0.1:$LLM_PORT/v1/models" 2>/dev/null | grep -q '"id":"llm"'; }
gw_up()  { curl -sf -m 3 "http://127.0.0.1:$PORT/v1/models" -H "authorization: Bearer $API_KEY" >/dev/null 2>&1; }

case "${1:-status}" in
  start)
    if ! llm_up; then
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
    if ! gw_up; then
      LLM_URL="http://127.0.0.1:$LLM_PORT" LLM_MODEL=llm IMAGE_MODEL_PATH="${IMAGE_MODEL_PATH:-$MODELS_DIR/flux2-klein-4b-mflux-q4}" \
        nohup uv run python gateway.py > "$LOGS/gateway.log" 2>&1 &
      echo $! > "$LOGS/gateway.pid"
      for i in $(seq 60); do gw_up && break; sleep 1; done
      gw_up && echo "gateway up (:$PORT)" || { echo "gateway did NOT start: $LOGS/gateway.log"; exit 1; }
    fi ;;
  stop)
    for n in gateway mtplx; do [ -f "$LOGS/$n.pid" ] && pkill -P "$(cat "$LOGS/$n.pid")" 2>/dev/null; [ -f "$LOGS/$n.pid" ] && kill "$(cat "$LOGS/$n.pid")" 2>/dev/null; rm -f "$LOGS/$n.pid"; done
    pkill -f "mtplx.*serve.*--port $LLM_PORT" 2>/dev/null; pkill -f "python gateway.py" 2>/dev/null; echo stopped ;;
  status)
    llm_up && echo "llm      up" || echo "llm      DOWN"
    gw_up && curl -s -m 3 "http://127.0.0.1:$PORT/health" || echo "gateway  DOWN"; echo ;;
  logs) tail -n 40 -f "$LOGS/gateway.log" "$LOGS/mtplx.log" ;;
  *) echo "usage: $0 start|stop|status|logs"; exit 1 ;;
esac
