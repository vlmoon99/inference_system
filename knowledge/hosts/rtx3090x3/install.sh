#!/usr/bin/env bash
# rtx3090x3 — one-shot install of the model services + worker on the 3090 box.
# Run as the linux user that will own the services, from a clone of this repo
# (read-only collaborator access; see LICENSE — this code stays the owner's).
#
#   git clone https://github.com/vlmoon99/advertisment_system.git ~/Documents/dev/advertisment_system
#   ADS_HOME=~/ads ADS_USER_UNITS=1 MODELS_ONLY=1 bash inference/hosts/rtx3090x3/install.sh   # how the box runs
#
# MODELS_ONLY=1 (recommended): install the model services only — no ads-worker here.
# A worker on the Spark (workers/rtx3090x3.env) drives this box; nothing has to be
# re-uploaded when the pipelines change. Without it, step 7 also installs ads-worker.
# Idempotent: re-running skips finished steps. Needs: NVIDIA driver ≥ 570, docker +
# nvidia-container-toolkit, python3.12, git, ffmpeg, curl, `hf` (huggingface_hub) logged
# in with the LTX-2.5 licence accepted. See README.md for the why behind every pin.
# GPU2_ROLE=video (default): GPU2 is a second LTX renderer (comfy-vid2/gen-video2) and the
# ads-llm container is created but left stopped; GPU2_ROLE=llm keeps vLLM there. gpu2.sh flips it.
# ADS_USER_UNITS=1: no sudo on this box — install systemd --user units
# (~/.config/systemd/user; needs `loginctl enable-linger $USER` once, by an admin).
set -euo pipefail
HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HOST_DIR/../../.." && pwd)"
ADS="${ADS_HOME:-/opt/ads}"
CF="$ADS/ComfyUI"
COMFY_TAG="${COMFY_TAG:-v0.33.3}"
USER_NAME="$(id -un)"
mkdir -p "$ADS/data/assets" "$ADS/data/models"

step() { echo; echo "▶ $*"; }

step "1/7 repo venv (adapters + worker)"
if [ ! -x "$REPO/.venv/bin/python" ]; then
  python3.12 -m venv "$REPO/.venv"
  "$REPO/.venv/bin/pip" install -q -U pip
  "$REPO/.venv/bin/pip" install -q -r "$REPO/backend/requirements.txt" -r "$REPO/inference/requirements.txt"
  "$REPO/.venv/bin/pip" install -q -e "$REPO/backend"
fi

step "2/7 ComfyUI $COMFY_TAG — the Spark's exact version (one process per GPU role)"
if [ ! -d "$CF" ]; then
  git clone https://github.com/comfyanonymous/ComfyUI "$CF"
  python3.12 -m venv "$CF/.venv" && "$CF/.venv/bin/pip" install -q -U pip
  "$CF/.venv/bin/pip" install -q torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
  mkdir -p "$CF"/{output-img,input-img,output-vid,input-vid}
fi
# Re-pinned on every run: an older box moves to the tag, and requirements.txt brings
# comfy-kitchen, whose int8 kernels (sm80+) run the Spark's int8-convrot LTX/Gemma files.
(cd "$CF" && git fetch -q --tags && git checkout -q "$COMFY_TAG")
"$CF/.venv/bin/pip" install -q -r "$CF/requirements.txt"
"$CF/.venv/bin/python" -c "import comfy_kitchen" || { echo "✗ comfy_kitchen missing — int8 LTX cannot load"; exit 1; }

step "3/7 weights — the Spark's files byte for byte (inference/workflows/weights.lock, ~58 GB)"
# Downloads what is missing, then checks every sha256 (a few minutes; VERIFY=0 skips).
cd "$CF/models"
while read -r sha dest repo src; do
  case "$sha" in ''|'#'*) continue;; esac
  if [ ! -f "$dest" ]; then
    hf download "$repo" "$src" --local-dir .dl </dev/null && mkdir -p "$(dirname "$dest")" && mv ".dl/$src" "$dest"
  fi
  if [ "${VERIFY:-1}" = 1 ]; then
    echo "$sha  $dest" | sha256sum -c --quiet || { echo "✗ $dest differs from the Spark's — delete it and re-run"; exit 1; }
  fi
done < "$REPO/inference/workflows/weights.lock"
rm -rf .dl
cd "$REPO"

step "4/7 retire the GGUF substitutes (Q4 LTX + Q4 Gemma; the lock above replaced them)"
rm -f "$CF/models/diffusion_models/LTX-2.5-Distilled-Q4_K_M.gguf" \
      "$CF/models/text_encoders/gemma4-12b-with-proj-ltx-2.5-Q4_K_M.gguf"
rm -rf "$ADS/wf-3090"

step "5/7 TTS assets (kokoro en + piper uk/ru; RadTTS uk stays on the Spark)"
mkdir -p "$REPO/data/models/kokoro" "$REPO/data/models/piper"
[ -f "$REPO/data/models/kokoro/kokoro-v1.0.onnx" ] || curl -sL -o "$REPO/data/models/kokoro/kokoro-v1.0.onnx" https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
[ -f "$REPO/data/models/kokoro/voices-v1.0.bin" ] || curl -sL -o "$REPO/data/models/kokoro/voices-v1.0.bin" https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
for v in uk_UA-ukrainian_tts-medium ru_RU-ruslan-medium; do
  lang="${v%%_*}"; code="${v%%-*}"
  for ext in onnx onnx.json; do
    [ -f "$REPO/data/models/piper/$v.$ext" ] || hf download rhasspy/piper-voices "$lang/$code/${v#*-}/medium/$v.$ext" --local-dir /tmp/piper >/dev/null && cp "/tmp/piper/$lang/$code/${v#*-}/medium/$v.$ext" "$REPO/data/models/piper/" 2>/dev/null || true
  done
done

step "6/7 LLM container (GPU2 fallback, vLLM; LLM_WEIGHTS=awq default | nvfp4 = the Spark's checkpoint)"
# nvfp4 is the Spark's exact file (23.4 GB) through vLLM's Marlin FP4 kernels (sm80+) —
# on a 24 GB card that leaves almost nothing for activations, so it is opt-in until it
# has run here: `docker rm -f ads-llm && LLM_WEIGHTS=nvfp4 bash install.sh`. The Spark's
# worker for this box pools the Spark's own LLM first anyway (workers/rtx3090x3.env).
case "${LLM_WEIGHTS:-awq}" in
  nvfp4) LLM_ARGS=(nvidia/Qwen3.6-35B-A3B-NVFP4 --served-model-name nvidia/Qwen3.6-35B-A3B-NVFP4
                   --moe-backend marlin --gpu-memory-utilization 0.96 --max-model-len 16384 --max-num-seqs 4) ;;
  *)     LLM_ARGS=(QuantTrio/Qwen3.6-35B-A3B-AWQ --served-model-name QuantTrio/Qwen3.6-35B-A3B-AWQ nvidia/Qwen3.6-35B-A3B-NVFP4
                   --gpu-memory-utilization 0.92 --max-model-len 16384 --max-num-seqs 8) ;;
esac
if ! docker ps -a --format '{{.Names}}' | grep -q '^ads-llm$'; then
  docker run -d --name ads-llm --restart unless-stopped --gpus '"device=2"' --ipc=host \
    -p 8010:8000 -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
    vllm/vllm-openai:latest "${LLM_ARGS[@]}" \
    --trust-remote-code --max-num-batched-tokens 8192 --enable-chunked-prefill --enable-prefix-caching \
    --kv-cache-dtype fp8 --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_xml
fi
# GPU2 renders video by default; the LLM only runs for offload mode (gpu2.sh llm).
# unless-stopped + stop = stays stopped across reboots until started again.
[ "${GPU2_ROLE:-video}" = video ] && docker stop ads-llm >/dev/null 2>&1 && echo "  ads-llm stopped (GPU2_ROLE=video)"

step "7/7 systemd units (comfy-img, comfy-vid, comfy-vid2, gen-image, gen-video, gen-video2, gen-tts, ads-worker)"
mkdir -p "$CF/input-vid2" "$CF/output-vid2"
cat > "$ADS/model-host.env" <<ENV
REPO=$REPO
CF=$CF
ASSETS_DIR=$ADS/data/assets
HOST_NAME=rtx3090x3
DEVICE=cuda
ENV
if [ "${MODELS_ONLY:-0}" != 1 ]; then
  [ -f "$ADS/worker.env" ] || { cp "$HOST_DIR/worker.env.example" "$ADS/worker.env"; chmod 600 "$ADS/worker.env"; echo "  ! fill in $ADS/worker.env (DATABASE_URL password, EXECUTOR_TOKEN)"; }
fi
if [ "${ADS_USER_UNITS:-0}" = 1 ]; then
  UNIT_DIR="$HOME/.config/systemd/user"; SC="systemctl --user"; mkdir -p "$UNIT_DIR"
  # a user manager has no User= and no multi-user.target
  USER_SED=(-e "/^User=/d" -e "s#WantedBy=multi-user.target#WantedBy=default.target#")
  put() { tee "$1" >/dev/null; }
else
  UNIT_DIR=/etc/systemd/system; SC="sudo systemctl"; USER_SED=()
  put() { sudo tee "$1" >/dev/null; }
fi
for unit in "$HOST_DIR"/units/*.service; do
  [ "${MODELS_ONLY:-0}" = 1 ] && [ "$(basename "$unit")" = ads-worker.service ] && continue
  sed -e "s#@USER@#$USER_NAME#g" -e "s#@ADS@#$ADS#g" -e "s#@REPO@#$REPO#g" -e "s#@CF@#$CF#g" "${USER_SED[@]}" "$unit" \
    | put "$UNIT_DIR/$(basename "$unit")"
done
$SC daemon-reload
$SC enable --now comfy-img comfy-vid gen-image gen-video gen-tts
if [ "${GPU2_ROLE:-video}" = video ]; then $SC enable --now comfy-vid2 gen-video2
else $SC disable --now gen-video2 comfy-vid2 2>/dev/null || true; fi
echo
echo "✔ services enabled. Next:"
if [ "${MODELS_ONLY:-0}" = 1 ]; then
  echo "   1. on the Spark: cp workers/rtx3090x3.env.example workers/rtx3090x3.env, paste an executor token, make run"
else
  echo "   1. fill $ADS/worker.env, then: $SC enable --now ads-worker"
fi
echo "   2. from any tailnet machine: python -m inference.conformance --host rtx3090x3 --at $(tailscale ip -4 2>/dev/null | head -1 || echo '<this-ip>')"
echo "   3. from the Spark: .venv/bin/python inference/hosts/rtx3090x3/parity.py --at <this-ip> (same seed → same picture?)"
echo "   4. record timings in inference/hosts/rtx3090x3/README.md and flip status: built"
