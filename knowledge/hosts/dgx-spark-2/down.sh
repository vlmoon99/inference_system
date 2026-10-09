#!/usr/bin/env bash
# dgx-spark-2 — stop the model services. Nothing here is shared with other projects.
#   down.sh          adapters only (ComfyUI keeps its weights in memory)
#   down.sh --all    also both ComfyUI containers (frees the box)
set -uo pipefail
systemctl --user stop gen-image gen-video 2>/dev/null || true
[ "${1:-}" = "--all" ] && docker stop ads-comfyui ads-comfyui-vid >/dev/null 2>&1 && echo "  stopped both ComfyUIs (:8188, :8189)"
echo "  stopped model services"
