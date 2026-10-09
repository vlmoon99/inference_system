#!/usr/bin/env bash
# rtx3090x3 — stop the model services on this box. The vLLM container (ads-llm,
# :8010) is shared with other projects and stays up unless --all is given.
#   down.sh          adapters + ComfyUI processes (+ a local ads-worker if any)
#   down.sh --all    also the ads-llm docker container
set -uo pipefail
if [ -f "$HOME/.config/systemd/user/comfy-img.service" ]; then SC="systemctl --user"; else SC="sudo systemctl"; fi
$SC stop ads-worker gen-image gen-video gen-video2 gen-tts comfy-img comfy-vid comfy-vid2 2>/dev/null || true
if [ "${1:-}" = "--all" ]; then
  docker stop ads-llm 2>/dev/null && echo "  stopped ads-llm (:8010)"
fi
echo "  stopped model services"
