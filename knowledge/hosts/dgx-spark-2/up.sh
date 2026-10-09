#!/usr/bin/env bash
# dgx-spark-2 — start the model services (after install.sh). Idempotent.
set -uo pipefail
docker start ads-comfyui ads-comfyui-vid >/dev/null && echo "  ads-comfyui (:8188 image) + ads-comfyui-vid (:8189 video) up"
systemctl --user start gen-image gen-video && echo "  gen-image (:8102) + gen-video (:8104) up"
