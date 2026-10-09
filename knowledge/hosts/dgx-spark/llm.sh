#!/usr/bin/env bash
# spark-llm — the shared LLM container (Qwen3.6-35B-A3B NVFP4 on vLLM :8010), as it
# actually runs on the Spark (captured from `docker inspect` 2026-09-27; the compose
# file's flags were a subset). `recreate` is how a flag change reaches production:
#   inference/hosts/dgx-spark/llm.sh recreate     # ~2-3 min until /v1/models answers
#   inference/hosts/dgx-spark/llm.sh status
# LLM_GPU_UTIL: 0.20 since 2026-09-27 (docs/MODELS.md): the KV cache at 0.35 was ~10x the
# workload; the ~18 GB freed keeps Qwen-Image-2512 resident beside LTX-2.5.
set -uo pipefail
NAME=spark-llm
IMAGE="${LLM_IMAGE:-vllm-node}"
MODEL="${LLM_MODEL:-nvidia/Qwen3.6-35B-A3B-NVFP4}"
UTIL="${LLM_GPU_UTIL:-0.20}"
HF_CACHE="${HF_CACHE:-/home/server/.cache/huggingface}"

status() {
  echo "  $NAME: $(docker ps -a --format '{{.Names}} {{.Status}}' | grep "^$NAME " || echo 'absent')"
  curl -s -m 5 http://localhost:8010/v1/models | python3 -c 'import sys,json;print("  serving:", [m["id"] for m in json.load(sys.stdin)["data"]])' 2>/dev/null || echo "  :8010 not answering"
  docker inspect "$NAME" --format '  util: {{join .Args " "}}' 2>/dev/null | grep -o 'gpu-memory-utilization [0-9.]*'
}

recreate() {
  docker rm -f "$NAME" >/dev/null 2>&1 && echo "  removed old $NAME"
  docker run -d --name "$NAME" --restart unless-stopped --gpus all --network host --ipc host \
    --shm-size 32g \
    -e HF_HUB_OFFLINE=1 -e HF_HOME=/cache/huggingface -e VLLM_MARLIN_USE_ATOMIC_ADD=1 \
    -v "$HF_CACHE:/cache/huggingface" \
    "$IMAGE" vllm serve "$MODEL" --served-model-name "$MODEL" \
    --host 0.0.0.0 --port 8010 --tensor-parallel-size 1 --trust-remote-code \
    --kv-cache-dtype fp8 --attention-backend flashinfer --moe-backend marlin \
    --gpu-memory-utilization "$UTIL" --max-model-len 24576 --max-num-seqs 8 \
    --max-num-batched-tokens 8192 --enable-chunked-prefill --enable-prefix-caching \
    --load-format fastsafetensors --reasoning-parser qwen3 --tool-call-parser qwen3_xml \
    --enable-auto-tool-choice >/dev/null && echo "  started $NAME (util $UTIL) — waiting for /v1/models"
  for _ in $(seq 1 90); do curl -s -m 3 http://localhost:8010/v1/models >/dev/null 2>&1 && { echo "  ready"; status; return 0; }; sleep 4; done
  echo "  ! not ready after 6 min — docker logs --tail 50 $NAME"; return 1
}

case "${1:-status}" in
  recreate) recreate ;;
  status) status ;;
  *) echo "usage: llm.sh {recreate|status}"; exit 2 ;;
esac
