#!/usr/bin/env bash
# rtx3090x3 — translator bake-off candidates (docs/AGENTIC_PLAN.md §2.5), one at a time,
# as a throwaway vLLM container `ads-bakeoff` on :8015. The renderers on the cards it
# borrows are stopped for the run and restored by `down`. Never runs in production:
# the Spark's worker dials :8010/:8104/:8114 here, never :8015.
#
#   bakeoff.sh up hymt2       tencent/Hy-MT2-30B-A3B-FP8   GPU0+GPU2, TP=2 (~31 GB weights; FP8 via Marlin on Ampere)
#   bakeoff.sh up hymt2-bf16  tencent/Hy-MT2-30B-A3B       GPU0+1+2, TP=3 (~60 GB) — if FP8 MoE refuses to load on sm86
#   bakeoff.sh up hymt2-7b    tencent/Hy-MT2-7B            GPU2 only (dense 7B, bf16 ~14 GB)
#   bakeoff.sh up lapa        lapa-llm/lapa-v0.1.3-instruct GPU0+GPU2, TP=2 (Gemma-3 12B, bf16 ~24 GB)
#   bakeoff.sh status | logs | down
#
# Then from the SPARK (the harness, the gate and the judge live there):
#   cd backend && ../.venv/bin/python -m scripts.translate_bakeoff --judge \
#     --candidate hymt2=http://100.64.0.10:8015/v1|tencent/Hy-MT2-30B-A3B-FP8 --resume data/bakeoff/<dir>
set -uo pipefail
if [ -f "$HOME/.config/systemd/user/comfy-vid2.service" ]; then SC="systemctl --user"; else SC="sudo systemctl"; fi
PORT=8015
NAME=ads-bakeoff

candidate() {
  case "$1" in
    hymt2)      MODEL=tencent/Hy-MT2-30B-A3B-FP8; GPUS='"device=0,2"'; TP=2; EXTRA=(--max-model-len 8192 --gpu-memory-utilization 0.92) ;;
    hymt2-bf16) MODEL=tencent/Hy-MT2-30B-A3B;     GPUS='"device=0,1,2"'; TP=3; EXTRA=(--max-model-len 8192 --gpu-memory-utilization 0.92) ;;
    hymt2-7b)   MODEL=tencent/Hy-MT2-7B;          GPUS='"device=2"'; TP=1; EXTRA=(--max-model-len 8192 --gpu-memory-utilization 0.90) ;;
    lapa)       MODEL=lapa-llm/lapa-v0.1.3-instruct; GPUS='"device=0,2"'; TP=2; EXTRA=(--max-model-len 8192 --gpu-memory-utilization 0.92) ;;
    *) echo "unknown candidate: $1 (hymt2 | hymt2-bf16 | hymt2-7b | lapa)"; exit 2 ;;
  esac
}

stop_renderers() {
  # GPU2 first (LLM fallback or second video), then GPU0 (image) when borrowed, GPU1 only for TP=3.
  docker stop ads-llm >/dev/null 2>&1 || true
  $SC stop gen-video2 comfy-vid2 2>/dev/null || true
  [ "$TP" -ge 2 ] && { $SC stop gen-image comfy-img 2>/dev/null || true; }
  [ "$TP" -ge 3 ] && { $SC stop gen-video comfy-vid 2>/dev/null || true; }
  sleep 3
}
restore_renderers() {
  $SC start comfy-img gen-image comfy-vid gen-video comfy-vid2 gen-video2 2>/dev/null || true
  echo "  renderers restarted (GPU2 back to video; gpu2.sh llm if the Spark is offloaded)"
}

status() {
  echo "  $NAME: $(docker ps --format '{{.Names}} {{.Status}}' | grep "^$NAME " || echo 'not running')"
  curl -s -m 5 "http://localhost:$PORT/v1/models" | python3 -c 'import sys,json;print("  serving:", [m["id"] for m in json.load(sys.stdin)["data"]])' 2>/dev/null || echo "  :$PORT not answering"
  nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/  GPU /'
}

case "${1:-status}" in
  up)
    candidate "${2:?candidate}"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    stop_renderers
    docker run -d --name "$NAME" --gpus "$GPUS" --ipc=host -p "$PORT:8000" \
      -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
      vllm/vllm-openai:latest "$MODEL" --served-model-name "$MODEL" \
      --tensor-parallel-size "$TP" --trust-remote-code --max-num-seqs 8 \
      --enable-chunked-prefill --enable-prefix-caching "${EXTRA[@]}" >/dev/null \
      && echo "  $NAME started: $MODEL on GPUs $GPUS (:$PORT) — first run downloads the weights; \`bakeoff.sh logs\`"
    ;;
  down)
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "  $NAME removed"
    TP=3; restore_renderers ;;
  logs) docker logs --tail 40 "$NAME" ;;
  status) status ;;
  *) echo "usage: bakeoff.sh {up <candidate>|down|status|logs}"; exit 2 ;;
esac
