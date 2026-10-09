#!/usr/bin/env bash
# dgx-spark-2 — one-shot install of the image + video model services (no worker here;
# a worker on dgx-spark drives this box: workers/dgx-spark-2.env). README.md has the why.
#
#   git clone git@github.com:vlmoon99/advertisment_system.git ~/Documents/dev/advertisment_system
#   bash ~/Documents/dev/advertisment_system/inference/hosts/dgx-spark-2/install.sh
#
# Idempotent: re-running skips finished steps. Needs: docker + nvidia runtime (DGX OS),
# python3.12, git, `hf` logged in with the Lightricks/LTX-2.5 licence accepted,
# `loginctl enable-linger $USER` once (user units survive logout/reboot).
set -euo pipefail
HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HOST_DIR/../../.." && pwd)"
ADS="${ADS_HOME:-$HOME/ads}"
CF="${CF:-$HOME/ComfyUI}"
COMFY_TAG=v0.33.3
IMAGE=ads-comfyui:$COMFY_TAG
mkdir -p "$ADS/data/assets"

step() { echo; echo "▶ $*"; }

step "1/5 repo venv (adapters + conformance)"
if [ ! -x "$REPO/.venv/bin/python" ]; then
  python3.12 -m venv "$REPO/.venv"
  "$REPO/.venv/bin/pip" install -q -U pip
  "$REPO/.venv/bin/pip" install -q -r "$REPO/inference/requirements.txt" -r "$REPO/backend/requirements.txt"
  "$REPO/.venv/bin/pip" install -q -e "$REPO/backend"
fi

step "2/5 ComfyUI $COMFY_TAG + custom nodes — dgx-spark's exact pins"
[ -d "$CF/.git" ] || git clone https://github.com/comfyanonymous/ComfyUI "$CF"
(cd "$CF" && git fetch -q --tags && git checkout -q "$COMFY_TAG")
pin() {  # pin <dir> <url> <commit>
  [ -d "$CF/custom_nodes/$1" ] || git clone -q "$2" "$CF/custom_nodes/$1"
  git -C "$CF/custom_nodes/$1" fetch -q && git -C "$CF/custom_nodes/$1" checkout -q "$3"
}
pin ComfyUI-GGUF    https://github.com/city96/ComfyUI-GGUF.git 6ea2651
pin ComfyUI-KJNodes https://github.com/kijai/ComfyUI-KJNodes.git d3cfe21
mkdir -p "$CF/torchaudio" "$CF/input" "$CF/output"
cp "$HOST_DIR/torchaudio_stub.py" "$CF/torchaudio/__init__.py"
# submodules too — ComfyUI does `import torchaudio.functional` (comfy/text_encoders/gemma4.py)
for sub in functional compliance transforms; do cp "$HOST_DIR/torchaudio_stub.py" "$CF/torchaudio/$sub.py"; done

step "3/5 weights — dgx-spark's files byte for byte (image + LTX rows of weights.lock, ~90 GB)"
# FLUX rows are skipped (NC licence), avatar rows too (avatar stays on dgx-spark).
cd "$CF/models"
grep -E 'qwen|ltx-2.5|gemma4' "$REPO/inference/workflows/weights.lock" | grep -v '^#' |
while read -r sha dest repo src; do
  if [ ! -f "$dest" ]; then
    hf download "$repo" "$src" --local-dir .dl </dev/null && mkdir -p "$(dirname "$dest")" && mv ".dl/$src" "$dest"
  fi
  if [ "${VERIFY:-1}" = 1 ]; then
    echo "$sha  $dest" | sha256sum -c --quiet || { echo "✗ $dest differs from dgx-spark's — delete it and re-run"; exit 1; }
  fi
done
rm -rf .dl
cd "$REPO"

step "4/5 ComfyUI containers ($IMAGE): ads-comfyui :8188 = image, ads-comfyui-vid :8189 = video"
# One ComfyUI per role: a ComfyUI only evicts its OWN models, so image and video stay
# resident side by side, and an image never queues behind a video render.
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" -f "$HOST_DIR/comfyui.Dockerfile" "$CF"
comfy() {  # comfy <name> <port>
  # --disable-async-offload --disable-dynamic-vram: required on GB10 (sampler crashes without them).
  # --cache-lru 64: the default RAM-pressure cache evicts every *inactive* node result once
  # MemAvailable < 100% of RAM (i.e. always), so the 2512 and Edit loaders dropped each
  # other and keep-warm re-loaded ~20 GB every cycle. LRU keeps both workflows' loaders.
  local cmd="cd /comfy && python main.py --listen 0.0.0.0 --port $2 --disable-async-offload --disable-dynamic-vram --cache-lru 64"
  if docker ps -a --format '{{.Names}}' | grep -q "^$1\$" && \
     [ "$(docker inspect "$1" --format '{{index .Config.Cmd 2}}')" != "$cmd" ]; then
    docker rm -f "$1" >/dev/null   # launch flags changed → recreate (models live in $CF, nothing lost)
  fi
  if ! docker ps -a --format '{{.Names}}' | grep -q "^$1\$"; then
    docker run -d --name "$1" --restart unless-stopped --gpus all --ipc=host \
      -p "$2:$2" -v "$CF:/comfy" "$IMAGE" bash -lc "$cmd"
  fi
  docker start "$1" >/dev/null
  for _ in $(seq 60); do curl -sf "localhost:$2/system_stats" >/dev/null && break; sleep 2; done
  curl -sf "localhost:$2/system_stats" >/dev/null || { echo "✗ $1 did not come up — docker logs $1"; exit 1; }
}
comfy ads-comfyui 8188
comfy ads-comfyui-vid 8189

step "4b/5 page-cache guard (user timer, no sudo)"
# CUDA on GB10 reports MemFree: the page cache left by weight reads looks like used GPU
# memory and makes ComfyUI evict resident models. dropcache.sh explains; the timer acts
# only when MemAvailable − MemFree > 6 GB. Replaces the old root timer if present.
if systemctl is-enabled ads-dropcache.timer >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  sudo systemctl disable --now ads-dropcache.timer
fi

step "5/5 adapter units (gen-image :8102, gen-video :8104) as systemd --user"
cat > "$ADS/model-host.env" <<ENV
REPO=$REPO
CF=$CF
ASSETS_DIR=$ADS/data/assets
HOST_NAME=dgx-spark-2
DEVICE=cuda
ENV
UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR"
for unit in "$HOST_DIR"/units/gen-*.service "$HOST_DIR"/units/ads-dropcache.*; do
  sed -e "s#@ADS@#$ADS#g" -e "s#@REPO@#$REPO#g" -e "s#@CF@#$CF#g" "$unit" > "$UNIT_DIR/$(basename "$unit")"
done
systemctl --user daemon-reload
systemctl --user enable gen-image gen-video
systemctl --user enable --now ads-dropcache.timer
# restart, not start: a re-run must pick up changed unit settings (keep-warm, ComfyUI port)
systemctl --user restart gen-image gen-video

echo
echo "✔ dgx-spark-2 services up. Next (README.md steps 3–5):"
echo "   1. from dgx-spark: .venv/bin/python -m inference.conformance --host dgx-spark-2 --at $(tailscale ip -4 2>/dev/null | head -1 || echo '<this-ip>')"
echo "   2. measure peaks with free -g, fill the README table"
echo "   3. on dgx-spark: register executor dgx-spark-2, cp workers/dgx-spark-2.env.example workers/dgx-spark-2.env, make run"
