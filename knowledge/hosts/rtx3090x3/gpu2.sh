#!/usr/bin/env bash
# rtx3090x3 — what GPU2 does. The card cannot hold both at once (LTX 21.5 GB / vLLM ~20 GB).
#   gpu2.sh video    DEFAULT: a second LTX renderer (comfy-vid2 :8190 + gen-video2 :8114),
#                    same files and workflow as GPU1 — the Spark's worker rtx3090x3-v2 dials it.
#                    Use while the Spark's own LLM is up (normal mode).
#   gpu2.sh llm      the fallback LLM (docker ads-llm, vLLM :8010) — needed while the Spark
#                    is offloaded (make offload), because then nothing else answers prompts.
#   gpu2.sh status   which role is live.
set -uo pipefail
if [ -f "$HOME/.config/systemd/user/comfy-vid2.service" ]; then SC="systemctl --user"; else SC="sudo systemctl"; fi
up() { curl -s -m 5 -o /dev/null -w "%{http_code}" "http://localhost:$1$2" 2>/dev/null | grep -q '^200$' && echo up || echo down; }
status() {
  echo "  video #2 (:8114) $(up 8114 /health) · ComfyUI :8190 $(up 8190 /system_stats) · vLLM :8010 $(up 8010 /v1/models)"
  nvidia-smi -i 2 --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null | sed 's/^/  GPU2 /'
}
case "${1:-status}" in
  video)
    docker stop ads-llm >/dev/null 2>&1 && echo "  stopped ads-llm (:8010)"
    $SC start comfy-vid2 gen-video2 && echo "  started comfy-vid2 + gen-video2"
    sleep 5; status ;;
  llm)
    $SC stop gen-video2 comfy-vid2 2>/dev/null && echo "  stopped gen-video2 + comfy-vid2"
    docker start ads-llm >/dev/null && echo "  started ads-llm — vLLM needs ~2 min to load"
    status ;;
  status) status ;;
  *) echo "usage: gpu2.sh {video|llm|status}"; exit 2 ;;
esac
