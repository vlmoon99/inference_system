#!/usr/bin/env bash
# dgx-spark — start/stop the model services on the DGX Spark (GB10, 122 GB unified).
#
#   services.sh up   [llm|image|video|tts|all]      start (idempotent)
#   services.sh down [hard]                          stop AD-ONLY adapters; `hard` also
#                                                    stops the SHARED tier (see host.yaml)
#   services.sh status
#
# Shared tier (referenced, never stopped without `hard`): spark-llm (vLLM :8010),
# spark-embed (bge-m3 :8011), pd-comfyui (ComfyUI :8188). Other projects on the
# tailnet use them too. compose.shared.yaml is their reinstall recipe.
# Owned: the three contract adapters (image :8102, video :8104, tts :8105) and
# the optional Ukrainian TTS sidecar (:8106).
set -uo pipefail

HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AD_ROOT="$(cd "$HOST_DIR/../../.." && pwd)"
ADAPTERS="$AD_ROOT/inference/adapters"
ASSETS_DIR="${ASSETS_DIR:-$AD_ROOT/backend/data/assets}"
PY="$AD_ROOT/.venv/bin"
export HOST_NAME=dgx-spark DEVICE=cuda
mkdir -p "$AD_ROOT/logs" "$ASSETS_DIR"
# Local TTS secrets (ElevenLabs key etc.) — optional, untracked
[ -f "$AD_ROOT/.env.tts" ] && . "$AD_ROOT/.env.tts"

port_up() { ss -tlnH 2>/dev/null | grep -q ":$1 "; }

start_shared() {
  # docker containers with restart=unless-stopped; `docker start` is a no-op when up
  local name="$1" port="$2"
  if docker ps --format '{{.Names}}' | grep -q "^${name}$"; then echo "$name: up (:$port)"; return; fi
  echo "$name: starting (:$port)…"
  docker start "$name" >/dev/null 2>&1 || echo "  ! $name missing — recreate with: $HOST_DIR/llm.sh recreate (spark-llm) or docker compose -f $HOST_DIR/compose.shared.yaml up -d $name"
}

up_llm() { start_shared spark-llm 8010; }
up_comfy() { start_shared pd-comfyui 8188; sleep 2; }

up_image() {
  if port_up 8102; then echo "image: adapter up (:8102)"; return; fi
  port_up 8188 || up_comfy
  # Owner's decision 2026-10-03: the Spark loads Qwen-Image-Edit-2511 ONLY, in every mode —
  # it cannot keep Qwen-Image-2512 warm next to it (each evicts the other, ~130 s reload).
  # Edit renders text-only requests on a blank canvas; product photos are its specialty.
  # IMAGE_MODELS/IMAGE_DEFAULT_MODEL still override (e.g. IMAGE_MODELS= for the full ladder).
  local models="${IMAGE_MODELS-qwen-image-edit-2511}" default="${IMAGE_DEFAULT_MODEL:-qwen-image-edit-2511}"
  echo "image: gen-image (:8102 -> ComfyUI :8188, models ${models:-all}, default $default) assets in $ASSETS_DIR"
  KEEP_WARM="${KEEP_WARM:-once}" COMFY_URL=http://localhost:8188 ASSETS_DIR="$ASSETS_DIR" IMAGE_DEFAULT_MODEL="$default" \
    IMAGE_MODELS="$models" \
    nohup "$PY/uvicorn" app:app --host 0.0.0.0 --port 8102 \
    --app-dir "$ADAPTERS/gen-image" >"$AD_ROOT/logs/gen-image.log" 2>&1 &
}

up_video() {
  if port_up 8104; then echo "video: adapter up (:8104)"; return; fi
  port_up 8188 || up_comfy
  # WAN_MODEL is only the fallback for requests that carry no `model`; the host
  # advertises what it serves (detected from ComfyUI's weights: ltx2 only since
  # the Wan files left). LTX-2.5 is the video model of record, so it is the default.
  echo "video: gen-video (:8104 -> LTX-2.5 via ComfyUI) assets in $ASSETS_DIR"
  COMFY_URL=http://localhost:8188 ASSETS_DIR="$ASSETS_DIR" WAN_MODEL="${WAN_MODEL:-ltx2}" \
    nohup "$PY/uvicorn" app:app --host 0.0.0.0 --port 8104 \
    --app-dir "$ADAPTERS/gen-video" >"$AD_ROOT/logs/gen-video.log" 2>&1 &
}

up_avatar() {
  if port_up 8106; then echo "avatar: adapter up (:8106)"; return; fi
  port_up 8188 || up_comfy
  echo "avatar: gen-avatar (:8106 -> InfiniteTalk on Wan2.1-14B via ComfyUI) assets in $ASSETS_DIR"
  COMFY_URL=http://localhost:8188 ASSETS_DIR="$ASSETS_DIR" VLLM_SLEEP_URL="${VLLM_SLEEP_URL:-http://localhost:8010}" HOST_NAME=dgx-spark \
    nohup "$PY/uvicorn" app:app --host 0.0.0.0 --port 8106 \
    --app-dir "$ADAPTERS/gen-avatar" >"$AD_ROOT/logs/gen-avatar.log" 2>&1 &
}

up_tts() {
  if port_up 8105; then echo "tts: adapter up (:8105)"; else
    echo "tts: gen-tts (:8105, kokoro en · radtts uk · f5 ru) -> assets in $ASSETS_DIR"
    # Model assets are repo-owned under data/models/ (see data/models/.gitignore for
    # the download recipe). uk default is radtts (RAD-TTS++ Ukrainian, voice mykyta);
    # ru default is f5; both degrade to piper when assets or libs are missing.
    ASSETS_DIR="$ASSETS_DIR" \
      KOKORO_ONNX="$AD_ROOT/data/models/kokoro/kokoro-v1.0.onnx" \
      KOKORO_VOICES="$AD_ROOT/data/models/kokoro/voices-v1.0.bin" \
      PIPER_VOICES_DIR="$AD_ROOT/data/models/piper" \
      TTS_ENGINE_UK="${TTS_ENGINE_UK:-radtts}" \
      TTS_ENGINE_RU="${TTS_ENGINE_RU:-f5}" \
      ELEVENLABS_VOICE="${ELEVENLABS_VOICE:-George}" \
      STYLETTS2_UK_VOICE="${STYLETTS2_UK_VOICE:-Денис Денисенко}" \
      RADTTS_UK_VOICE="${RADTTS_UK_VOICE:-mykyta}" \
      RADTTS_UK_MODEL_DIR="$AD_ROOT/data/models/radtts/uk" \
      STYLETTS2_UK_MODEL_DIR="$AD_ROOT/data/models/styletts2/uk" \
      STYLETTS2_MULTI_UK_MODEL_DIR="$AD_ROOT/data/models/styletts2/uk-multi" \
      F5_UK_MODEL_DIR="$AD_ROOT/data/models/f5/uk" \
      F5_RU_MODEL_DIR="$AD_ROOT/data/models/f5/ru" \
      F5_REFS_DIR="$AD_ROOT/data/models/f5/refs" \
      nohup "$PY/uvicorn" app:app --host 0.0.0.0 --port 8105 \
      --app-dir "$ADAPTERS/gen-tts" >"$AD_ROOT/logs/gen-tts.log" 2>&1 &
  fi
  # OPTIONAL isolated sidecar (robinhad/ukrainian-tts, ESPnet — own venv). GPL v3
  # weights: server-side use only. Starts only when the venv + model exist.
  [ -x "$AD_ROOT/.venv-uktts/bin/uvicorn" ] || return 0
  [ -f "$AD_ROOT/data/models/uktts/model.pth" ] || return 0
  if port_up 8106; then echo "tts: ukrainian sidecar up (:8106)"; return; fi
  echo "tts: gen-tts-ukrainian sidecar (:8106, espnet, isolated venv)"
  UKTTS_MODEL_DIR="$AD_ROOT/data/models/uktts" \
    nohup "$AD_ROOT/.venv-uktts/bin/uvicorn" app:app --host 0.0.0.0 --port 8106 \
    --app-dir "$ADAPTERS/gen-tts-ukrainian" >"$AD_ROOT/logs/gen-tts-ukrainian.log" 2>&1 &
}

kill_port() {
  local pid
  pid=$(ss -tlnHp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null && echo "  stopped :$1 (pid $pid)"
}

down_owned() {
  echo "stopping owned adapters (image/video/tts) — shared tier stays up…"
  for p in 8102 8104 8105 8106; do kill_port "$p"; done
}

down_hard() {
  echo "HARD stop: owned adapters AND the shared tier (spark-llm, spark-embed, pd-comfyui)…"
  down_owned
  docker stop pd-comfyui spark-llm spark-embed 2>/dev/null || true
}

status() {
  # A port answering says a process is alive, not that a model is in memory
  # (ComfyUI loads weights on the first job) — so also show warm/cold.
  echo "  service        port  state"
  for entry in "llm-qwen:8010" "embeddings:8011" "image:8102" "video:8104" "tts:8105" "avatar:8106" "comfyui:8188"; do
    name="${entry%%:*}"; p="${entry##*:}"
    if port_up "$p"; then st="UP"; else st="down"; fi
    printf "  %-13s :%-4s %s\n" "$name" "$p" "$st"
  done
  comfy_pid="$(pgrep -f 'main.py --listen' | head -1)"
  comfy_mb="$(( $(ps -o rss= -p "${comfy_pid:-0}" 2>/dev/null | tr -d ' ' || echo 0) / 1024 ))"
  if [ "${comfy_mb:-0}" -gt 5000 ]; then
    echo "  generation models: WARM in ComfyUI (${comfy_mb} MiB resident)"
  else
    echo "  generation models: COLD — weights load on the first image/video job"
  fi
  echo "  memory: $(free -g | awk '/^Mem:/{print $3" / "$2" GB used"}')"
}

case "${1:-status}" in
  up)
    case "${2:-all}" in
      llm) up_llm;; image) up_image;; video) up_video;; tts) up_tts;; avatar) up_avatar;;
      all) up_llm; up_image; up_video; up_tts; up_avatar;;
      *) echo "unknown service ${2}"; exit 2;;
    esac;;
  down) [ "${2:-}" = hard ] && down_hard || down_owned;;
  status) status;;
  *) echo "usage: services.sh {up|down|status} [llm|image|video|tts|all | hard]"; exit 2;;
esac
