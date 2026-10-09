#!/usr/bin/env bash
# dgx-spark — OFFLOAD MODE: hand generation to the rtx3090x3 box and free the
# Spark's GPU memory for something else (a big coding LLM, an experiment).
#
#   offload.sh on       free the Spark: stop spark-llm, pd-comfyui, the image/video
#                       adapters (and spark-embed unless KEEP_EMBED=1); restart
#                       ads-api as a pure control plane (QUEUE_EMBEDDED=0) so it
#                       never claims a job against the stopped services. TTS :8105
#                       STAYS UP — the Ukrainian brand voice (RadTTS, ~1 GB) lives
#                       only here and the 3090 worker dials it for every short.
#   offload.sh render   RENDER ON THE 3090 (the everyday mode): like `on`, but the
#                       Spark KEEPS its LLM and embeddings, and keeps ONE image rung
#                       warm — qwen-image-edit-2511 (product-photo edits, the step a
#                       customer waits on). Video and avatar stop; the 3090 renders
#                       every other image and all video (workers list the 3090 first,
#                       the Spark second; image routing sends each rung to the host
#                       whose default it is). The Spark holds LLM + TTS + edit
#                       (~78 GB) instead of peaking at ~108 GB+: no swap, no OOM.
#                       up.sh honours the mode at boot (logs/.offloaded).
#   offload.sh off      bring the Spark back to normal (= up.sh: shared tier,
#                       adapters, embedded worker, FLUX warm-up).
#   offload.sh status   who serves what right now.
#   offload.sh watch    (ads-offload-watch.timer, every minute) the Spark ON DEMAND:
#                       in render mode, 3 failed checks of the 3090's image/video
#                       (~3 min) hand rendering back to the Spark (`off`, the wish
#                       kept in logs/.offload-fallback); 5 healthy checks (~5 min)
#                       after the box returns switch back to render. Customers never
#                       queue behind an offline box. A manual on/render/off clears it.
#                       Every tick writes logs/.render-state — the API turns it into
#                       the "render server restarting / running on the backup" banner:
#                         ok          nothing to say
#                         restarting  the 3090 stopped answering, or the Spark is
#                                     still loading the backup
#                         backup      the Spark renders (slower) until the box is back
#
# Render host (2026-10-06): which box renders is logs/.render-host (default rtx3090x3);
# `offload.sh render dgx-spark-2` switches it. Its worker env workers/<host>.env may say
# SPARK_KEEPS=avatar (the Spark keeps the avatar warm instead of the edit rung — the
# render host already holds every image rung warm). Default SPARK_KEEPS=edit.
#
# Preflight: the render host's llm/image/video must answer and its worker (label
# rtx3090x3, started by up.sh from workers/rtx3090x3.env) must be running —
# otherwise customers would queue forever. FORCE=1 skips the check.
# Control plane (API :8070, web :3100/:3200, postgres, tunnel) is untouched
# except for the API restart (a few seconds; in-flight embedded work is
# re-queued by recover_orphans on the next start).
set -uo pipefail
HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AD_ROOT="$(cd "$HOST_DIR/../../.." && pwd)"
cd "$AD_ROOT"
mkdir -p logs
MARK="$AD_ROOT/logs/.offloaded"
FALLBACK="$AD_ROOT/logs/.offload-fallback"   # render is wanted, but the 3090 is down
WATCH_STATE="$AD_ROOT/logs/.offload-watch"   # consecutive fail/ok counters
[ "${1:-}" = render ] && [ -n "${2:-}" ] && echo "$2" >"$AD_ROOT/logs/.render-host"
RENDER_HOST="$(cat "$AD_ROOT/logs/.render-host" 2>/dev/null || echo rtx3090x3)"
REMOTE_ENV="$AD_ROOT/workers/$RENDER_HOST.env"
SPARK_KEEPS="$(grep -E '^SPARK_KEEPS=' "$REMOTE_ENV" 2>/dev/null | head -1 | cut -d= -f2 | tr -d ' ')"
SPARK_KEEPS="${SPARK_KEEPS:-edit}"   # edit | avatar — what the Spark keeps warm in render mode
STATE="$AD_ROOT/logs/.render-state"           # {"state","since","updated"} read by /v1/status

port_up() { ss -tlnH 2>/dev/null | grep -q ":$1 "; }
pid_on_port() { ss -tlnHp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2; }
worker_pid() {  # $1 = label
  for pid in $(pgrep -f "(^|/)python[0-9.]* -m ads[.]worker$"); do
    tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | grep -qx "ADS_WORKER_LABEL=$1" && { echo "$pid"; return 0; }
  done
  return 1
}
api_embedded() {  # prints 0/1 for the running API, or "?" when it is down
  local pid; pid="$(pid_on_port 8070)"
  [ -n "${pid:-}" ] || { echo "?"; return; }
  tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | grep -x "QUEUE_EMBEDDED=." | cut -d= -f2 | grep . || echo 1
}
remote_url() { grep -E "^$1=" "$REMOTE_ENV" 2>/dev/null | head -1 | cut -d= -f2- | cut -d, -f1 | tr -d ' '; }
http_ok() { curl -s -m 5 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null | grep -q '^200$'; }

start_workers() {  # the Spark-side workers that dial the 3090 (same as up.sh)
  for f in "$AD_ROOT"/workers/*.env; do
    [ -f "$f" ] || continue
    local label; label="$(basename "$f" .env)"
    worker_pid "$label" >/dev/null && continue
    setsid -f bash -c "cd '$AD_ROOT/backend' && set -a && source '$f' && set +a && exec env ADS_WORKER_LABEL=$label ../.venv/bin/python -m ads.worker" >>"logs/worker-$label.log" 2>&1 </dev/null
    echo "  worker $label: started"
  done
  sleep 3
}

restart_api() {  # $1 = QUEUE_EMBEDDED value
  local pid; pid="$(pid_on_port 8070)"
  [ -n "${pid:-}" ] && { kill "$pid"; for _ in $(seq 1 20); do port_up 8070 || break; sleep 0.5; done; }
  QUEUE_EMBEDDED="$1" QUEUE_CONCURRENCY="${QUEUE_CONCURRENCY:-4}" setsid -f bash -c \
    "cd '$AD_ROOT/backend' && exec ../.venv/bin/uvicorn ads.api.main:app --host 0.0.0.0 --port 8070" \
    >logs/api.log 2>&1 </dev/null
  echo -n "  ads-api restarting (QUEUE_EMBEDDED=$1) "
  for _ in $(seq 1 40); do http_ok http://localhost:8070/health && { echo "ready"; return 0; }; echo -n "."; sleep 1; done
  echo " NOT answering — check logs/api.log"; return 1
}

preflight() {
  local ok=1 llm img vid
  # the LLM pool lists the Spark's own first — full offload stops that one, so check the
  # 3090's (last); render mode keeps the Spark's, so check that one (first)
  if [ -n "${KEEP_LLM:-}" ]; then llm="http://localhost:8010/v1"; else llm="$(grep -E "^LLM_BASE_URL=" "$REMOTE_ENV" 2>/dev/null | head -1 | cut -d= -f2- | tr ',' '\n' | tail -1 | tr -d ' ')"; fi
  img="$(remote_url GEN_IMAGE_URL)"; vid="$(remote_url GEN_VIDEO_URL)"
  [ -f "$REMOTE_ENV" ] || { echo "  ! $REMOTE_ENV missing — nothing would render while the Spark is offloaded"; ok=0; }
  for entry in "llm:${llm:-}/models" "image:${img:-}/health" "video:${vid:-}/health"; do
    n="${entry%%:*}"; u="${entry#*:}"
    if [ -n "${u#/}" ] && http_ok "$u"; then echo "  $RENDER_HOST $n: ok ($u)"; else echo "  ! $RENDER_HOST $n: NOT answering ($u)"; ok=0
      [ "$n" = llm ] && [ "$RENDER_HOST" = rtx3090x3 ] && echo "    GPU2 renders video by default — on the 3090 box run: bash inference/hosts/rtx3090x3/gpu2.sh llm"; fi
  done
  if worker_pid "$RENDER_HOST" >/dev/null; then echo "  worker $RENDER_HOST: running (pid $(worker_pid "$RENDER_HOST"))"
  else echo "  ! worker $RENDER_HOST: not running — up.sh starts it from workers/$RENDER_HOST.env"; ok=0; fi
  if [ "$RENDER_HOST" = rtx3090x3 ] && [ -f "$AD_ROOT/workers/rtx3090x3-gpu2.env" ]; then
    worker_pid rtx3090x3-gpu2 >/dev/null && echo "  worker rtx3090x3-gpu2: running" \
      || echo "  ! worker rtx3090x3-gpu2: not running (GPU2 idles; not fatal)"; fi
  [ "$ok" = 1 ] && return 0
  [ -n "${FORCE:-}" ] && { echo "  FORCE=1: continuing anyway"; return 0; }
  echo "✖ preflight failed — fix the above or run with FORCE=1"; return 1
}

on() {
  rm -f "$FALLBACK" "$WATCH_STATE"
  start_workers
  echo "▶ preflight: can $RENDER_HOST carry the customers?"
  preflight || exit 1

  echo "▶ ads-api → pure control plane (no embedded worker)…"
  if [ "$(api_embedded)" = 0 ]; then echo "  ads-api already QUEUE_EMBEDDED=0"; else restart_api 0 || exit 1; fi

  echo "▶ freeing the Spark's GPU (tts :8105 stays for the brand voice)…"
  # SPARK_KEEPS=avatar: the avatar adapter (:8106) stays — the render host has no room for it
  local stop="8102 8104 8106"; [ -n "${KEEP_LLM:-}" ] && [ "$SPARK_KEEPS" = avatar ] && stop="8102 8104"
  for p in $stop; do pid="$(pid_on_port "$p")"; [ -n "${pid:-}" ] && kill "$pid" && echo "  stopped adapter :$p"; done
  for _ in $(seq 1 30); do port_up 8102 || port_up 8104 || port_up 8106 || break; sleep 0.5; done  # edit_only must not see a dying :8102
  if [ -n "${KEEP_LLM:-}" ]; then
    # render mode: ComfyUI stays for the edit rung (or the avatar), but drops every cached model (LTX, Qwen, FLUX)
    curl -s -m 20 -X POST http://localhost:8188/free -H 'Content-Type: application/json' \
      -d '{"unload_models": true, "free_memory": true}' >/dev/null 2>&1 && echo "  ComfyUI: cached models unloaded"
    if [ "$SPARK_KEEPS" = avatar ]; then bash "$HOST_DIR/services.sh" up avatar; else edit_only; fi
  else docker stop pd-comfyui spark-llm >/dev/null 2>&1 && echo "  stopped pd-comfyui + spark-llm"; fi
  if [ -n "${KEEP_EMBED:-}" ]; then echo "  spark-embed kept (KEEP_EMBED=1)"; else
    docker stop spark-embed >/dev/null 2>&1 && echo "  stopped spark-embed (analytics relevance goes stale until 'off')"; fi
  port_up 8105 || { echo "  tts was down — starting it"; bash "$HOST_DIR/services.sh" up tts; }
  echo "$([ -n "${KEEP_LLM:-}" ] && echo render || echo full) $(date -Is)" >"$MARK"
  render_state ok
  sleep 2; status
  echo "✔ offloaded — generation runs on $RENDER_HOST (the Spark keeps: LLM, voices$([ -n "${KEEP_LLM:-}" ] && echo ", $SPARK_KEEPS")). Back: offload.sh off"
}

off() {
  [ -n "${KEEP_WISH:-}" ] || rm -f "$FALLBACK" "$WATCH_STATE"
  echo "▶ restoring the Spark (up.sh: shared tier + adapters + embedded worker)…"
  # up.sh leaves a running API alone, so put it back to embedded first.
  if [ "$(api_embedded)" = 0 ]; then
    if [ -f "$AD_ROOT/worker.env" ]; then echo "  ads-api stays QUEUE_EMBEDDED=0 (worker.env exists)"; else restart_api 1; fi
  fi
  rm -f "$MARK"
  # render mode left an edit-only gen-image on :8102 — up.sh keeps a running port, so restart it full
  pid="$(pid_on_port 8102)"; [ -n "${pid:-}" ] && kill "$pid" && sleep 2
  bash "$HOST_DIR/up.sh"
  [ -n "${KEEP_WISH:-}" ] || render_state ok
  [ "$RENDER_HOST" = rtx3090x3 ] && echo "  3090 box: GPU2 back to the second video renderer → on the box: bash inference/hosts/rtx3090x3/gpu2.sh video"
}

edit_only() {  # the Spark's gen-image with the edit rung only (render mode)
  port_up 8102 && return 0
  # KEEP_WARM=once: one tiny edit at start loads Qwen-Edit (~137 s cold vs ~7 s warm) before a customer asks
  KEEP_WARM=once IMAGE_MODELS=qwen-image-edit-2511 IMAGE_DEFAULT_MODEL=qwen-image-edit-2511 bash "$HOST_DIR/services.sh" up image
  echo "  gen-image :8102 on the Spark: qwen-image-edit-2511 only (warming in the background)"
}

render_state() {  # $1 = ok|restarting|backup; "since" survives while the state holds
  local since now; now="$(date -Is)"
  since="$(grep -o "\"state\": \"$1\", \"since\": \"[^\"]*\"" "$STATE" 2>/dev/null | sed 's/.*"since": "//; s/"$//')"
  printf '{"state": "%s", "since": "%s", "updated": "%s"}\n' "$1" "${since:-$now}" "$now" >"$STATE.tmp" && mv "$STATE.tmp" "$STATE"
}

local_ok() {  # the Spark's own image and video adapters answer with their models present
  curl -s -m 8 http://localhost:8102/health 2>/dev/null | grep -q '"loaded":true' &&
    curl -s -m 8 http://localhost:8104/health 2>/dev/null | grep -q '"loaded":true'
}

remote_ok() {  # the render host's image and video adapters answer and have their default model loaded
  local img vid; img="$(remote_url GEN_IMAGE_URL)"; vid="$(remote_url GEN_VIDEO_URL)"
  [ -n "$img" ] && [ -n "$vid" ] || return 1
  curl -s -m 8 "$img/health" 2>/dev/null | grep -q '"loaded":true' &&
    curl -s -m 8 "$vid/health" 2>/dev/null | grep -q '"loaded":true'
}

watch_tick() {
  local fails=0 oks=0
  [ -f "$WATCH_STATE" ] && read -r fails oks <"$WATCH_STATE"
  if [ -f "$FALLBACK" ]; then            # the Spark is covering; wait for the box to be back
    if remote_ok; then oks=$((oks + 1)); else oks=0; fi
    echo "0 $oks" >"$WATCH_STATE"
    if [ "$oks" -ge 5 ]; then
      echo "$(date -Is) $RENDER_HOST healthy for $oks checks — back to render mode"
      KEEP_LLM=1 KEEP_EMBED=1 on
    elif local_ok; then render_state backup
    else render_state restarting; fi
  elif [ -f "$MARK" ] && [ "$(awk '{print $1; exit}' "$MARK")" = render ]; then
    if remote_ok; then fails=0; render_state ok; else fails=$((fails + 1)); render_state restarting; fi
    echo "$fails 0" >"$WATCH_STATE"
    if [ "$fails" -ge 3 ]; then
      echo "$(date -Is) $RENDER_HOST down for $fails checks — the Spark renders until it is back"
      echo "render $(date -Is)" >"$FALLBACK"
      KEEP_WISH=1 off
      if local_ok; then render_state backup; else render_state restarting; fi
    fi
  else render_state ok
  fi
  return 0
}

status() {
  echo "  mode: $([ -f "$MARK" ] && echo "OFFLOADED since $(cat "$MARK")" || echo normal)$([ -f "$FALLBACK" ] && echo " — FALLBACK: $RENDER_HOST down, Spark rendering since $(cut -d' ' -f2 "$FALLBACK")")"
  echo "  ads-api :8070 $(port_up 8070 && echo UP || echo down) · embedded worker: $(api_embedded)"
  echo "  render host: $RENDER_HOST (the Spark keeps $SPARK_KEEPS in render mode)"
  echo "  worker $RENDER_HOST: $(worker_pid "$RENDER_HOST" >/dev/null && echo running || echo NOT running)"
  echo "  Spark services:"; bash "$HOST_DIR/services.sh" status | sed 's/^/  /'
  echo "  $RENDER_HOST services:"
  for entry in "llm:$(remote_url LLM_BASE_URL)/models" "image:$(remote_url GEN_IMAGE_URL)/health" "video:$(remote_url GEN_VIDEO_URL)/health"; do
    printf "    %-6s %s\n" "${entry%%:*}" "$(http_ok "${entry#*:}" && echo UP || echo down)"
  done
}

case "${1:-status}" in
  on) on;; render) KEEP_LLM=1 KEEP_EMBED=1 on;; watch) watch_tick;; off) off;; status) status;;
  *) echo "usage: offload.sh {on|render [host]|off|status|watch}   (env: FORCE=1, KEEP_EMBED=1)"; exit 2;;
esac
