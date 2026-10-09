#!/usr/bin/env bash
# dgx-spark — FULL LOCAL RUN. One command brings up the whole stack on the Spark:
# shared tier (started if down) + owned adapters + API + worker + web apps.
# Idempotent; pairs with down.sh. `make run` calls this.
#
#   shared   : spark-llm (Qwen3.6-35B vLLM :8010) · spark-embed (bge-m3 :8011)
#              pd-comfyui (:8188) · ads-postgres (:5433)
#   adapters : image :8102 (Qwen-Image ladder) · video :8104 (LTX-2.5 / Wan) · tts :8105 · avatar :8106 (InfiniteTalk)
#   app      : ads-api :8070 · ads-worker (separate process) · client-web :3100 · admin-web :3200
#
# Env knobs: NOWARM=1 (skip the FLUX warm-up render), NOAPP=1 (models only — a
# pure inference host for a worker elsewhere), QUEUE_EMBEDDED (default 0 here).
set -uo pipefail
HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AD_ROOT="$(cd "$HOST_DIR/../../.." && pwd)"
cd "$AD_ROOT"
mkdir -p logs
port_up() { ss -tlnH 2>/dev/null | grep -q ":$1 "; }

# Offload mode survives a reboot (offload.sh writes logs/.offloaded): the 3090 renders,
# so the Spark's ComfyUI and image/video/avatar adapters stay down and the API is a
# pure control plane. "render" keeps the Spark's LLM; the older "full" mode stops it.
MARK="$AD_ROOT/logs/.offloaded"
OFFLOAD=""; [ -f "$MARK" ] && { OFFLOAD="$(awk '{print $1; exit}' "$MARK")"; [ "$OFFLOAD" = render ] || OFFLOAD=full; }
# Which box renders, and what the Spark keeps warm meanwhile (offload.sh header).
RENDER_HOST="$(cat "$AD_ROOT/logs/.render-host" 2>/dev/null || echo rtx3090x3)"
SPARK_KEEPS="$(grep -E '^SPARK_KEEPS=' "$AD_ROOT/workers/$RENDER_HOST.env" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' ')"
SPARK_KEEPS="${SPARK_KEEPS:-edit}"

echo "▶ shared tier (LLM :8010, embeddings :8011, gate :8012, ComfyUI :8188, postgres :5433)…"
case "$OFFLOAD" in
  render) echo "  OFFLOADED (render): $RENDER_HOST renders; the Spark keeps LLM, voices and the $SPARK_KEEPS model"
          docker start spark-llm spark-embed ads-postgres pd-comfyui >/dev/null 2>&1 || true;;
  full)   echo "  OFFLOADED (full): LLM + ComfyUI stay down — make onload to undo"
          docker start ads-postgres >/dev/null 2>&1 || true;;
  *)      docker start spark-llm spark-embed ads-postgres pd-comfyui >/dev/null 2>&1 || true;;
esac
bash "$HOST_DIR/gate.sh" up >/dev/null 2>&1 || true   # translation quality gate (CPU), docs/AGENTIC_PLAN.md §2.4
echo -n "  waiting for LLM :8010 "
[ "$OFFLOAD" = full ] || for _ in $(seq 1 45); do curl -s -m3 http://localhost:8010/v1/models >/dev/null 2>&1 && { echo "ready"; break; }; echo -n "."; sleep 4; done

echo "▶ owned adapters (image :8102, video :8104, tts :8105, avatar :8106)…"
if [ -z "$OFFLOAD" ]; then
  bash "$HOST_DIR/services.sh" up image
  bash "$HOST_DIR/services.sh" up video
elif [ "$OFFLOAD" = render ] && [ "$SPARK_KEEPS" = edit ]; then
  IMAGE_MODELS=qwen-image-edit-2511 IMAGE_DEFAULT_MODEL=qwen-image-edit-2511 bash "$HOST_DIR/services.sh" up image
fi
bash "$HOST_DIR/services.sh" up tts
{ [ -z "$OFFLOAD" ] || { [ "$OFFLOAD" = render ] && [ "$SPARK_KEEPS" = avatar ]; }; } && bash "$HOST_DIR/services.sh" up avatar

# Pull FLUX into memory now (background) instead of on the first real job.
if [ -z "${NOWARM:-}" ] && [ -z "$OFFLOAD" ]; then
  echo "▶ warming image model (background 256px/1-step job)…"
  setsid -f bash -c 'for i in $(seq 1 30); do curl -s -m2 http://localhost:8102/health >/dev/null 2>&1 && break; sleep 2; done
    curl -s -m900 -X POST http://localhost:8102/generate -H "Content-Type: application/json" \
      -d "{\"prompt\":\"warmup\",\"product_id\":\"warmup\",\"width\":256,\"height\":256,\"steps\":1}"' \
    >"$AD_ROOT/logs/warmup.log" 2>&1 </dev/null
fi

if [ -n "${NOAPP:-}" ]; then
  echo "✔ models up (NOAPP=1: no API/worker/web on this box)"; bash "$HOST_DIR/services.sh" status; exit 0
fi

# Daemons are fully detached (setsid -f) so this script always exits promptly.
# No worker.env yet -> the API runs its embedded worker so generation still works
# from a hand-started stack; with worker.env the API is a pure control plane.
if [ -n "$OFFLOAD" ] || [ -f "$AD_ROOT/worker.env" ]; then QUEUE_EMBEDDED="${QUEUE_EMBEDDED:-0}"; else QUEUE_EMBEDDED="${QUEUE_EMBEDDED:-1}"; fi
echo "▶ ads-api (:8070, QUEUE_EMBEDDED=$QUEUE_EMBEDDED)…"
# The embedded worker leaves photo/short/campaign to a helper executor (dgx-spark-2, the
# 3090s) for HELPER_GRACE_S when one that can render them is online; with none online it
# claims at once (ads/worker/queue.py helper_online). PREFER_HELPER_FOR= turns it off.
port_up 8070 || QUEUE_EMBEDDED="$QUEUE_EMBEDDED" QUEUE_CONCURRENCY="${QUEUE_CONCURRENCY:-4}" \
  PREFER_HELPER_FOR="${PREFER_HELPER_FOR-photo,short,campaign}" HELPER_GRACE_S="${HELPER_GRACE_S:-45}" setsid -f bash -c "cd '$AD_ROOT/backend' && exec ../.venv/bin/uvicorn ads.api.main:app --host 0.0.0.0 --port 8070" >logs/api.log 2>&1 </dev/null

echo "▶ workers…"
# One ads-worker process per env file. worker.env = this box's own models;
# workers/<name>.env = a worker that runs HERE but dials another machine's model
# services (a model-only box: no code, no worker, nothing to re-upload there).
# Each has its own executor identity and its own video slot, so a second host
# adds real throughput. See deploy/README.md "Model-only boxes".
worker_up() {  # $1 = label — the label lives in the environment, not argv, so look there
  for pid in $(pgrep -f "(^|/)python[0-9.]* -m ads[.]worker$"); do
    tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | grep -qx "ADS_WORKER_LABEL=$1" && return 0
  done
  return 1
}
start_worker() {  # $1 = env file, $2 = label
  if worker_up "$2"; then echo "  worker $2: up"; return; fi
  setsid -f bash -c "cd '$AD_ROOT/backend' && set -a && source '$1' && set +a && exec env ADS_WORKER_LABEL=$2 ../.venv/bin/python -m ads.worker" >"logs/worker-$2.log" 2>&1 </dev/null
  echo "  worker $2: started ($1)"
}
if [ -f "$AD_ROOT/worker.env" ]; then start_worker "$AD_ROOT/worker.env" spark; else
  echo "  (no worker.env — the API's embedded worker runs this box's jobs)"; fi
for f in "$AD_ROOT"/workers/*.env; do [ -f "$f" ] && start_worker "$f" "$(basename "$f" .env)"; done

echo "▶ web apps (client-web :3100, admin-web :3200) — need a prior 'npm run build'…"
port_up 3200 || setsid -f bash -c "cd '$AD_ROOT/apps/admin-web' && exec npx next start -p 3200" >logs/admin-web.log 2>&1 </dev/null
if [ -d "$AD_ROOT/apps/client-web" ]; then
  port_up 3100 || setsid -f bash -c "cd '$AD_ROOT/apps/client-web' && exec npx next start -p 3100" >logs/client-web.log 2>&1 </dev/null
fi

# NEAR Agent Market adapter — only when configured (agents.json holds aat_ tokens).
if [ -f "$AD_ROOT/integrations/near-market/agents.json" ]; then
  echo "▶ NEAR market adapter (:8180)…"
  port_up 8180 || setsid -f bash -c "cd '$AD_ROOT' && set -a && source .env 2>/dev/null; set +a && exec .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8180 --app-dir integrations/near-market" >logs/market-agent.log 2>&1 </dev/null
fi

sleep 3
echo "▶ status:"; bash "$HOST_DIR/services.sh" status
for p in 8070 3100 3200; do echo -n "  :$p "; port_up "$p" && echo UP || echo down; done
echo "✔ ready — tailnet: http://100.64.0.1:3100 (customers) · http://100.64.0.1:3200 (admin)"
