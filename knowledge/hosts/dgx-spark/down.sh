#!/usr/bin/env bash
# dgx-spark — FULL LOCAL STOP. Tears the ad app + owned adapters down and ALWAYS
# leaves the SHARED tier running (spark-llm :8010, spark-embed :8011, pd-comfyui
# :8188) unless --all is given: other tailnet projects use those.
#   down.sh          app + owned adapters
#   down.sh --all    everything, including the shared tier and postgres (frees the box)
set -uo pipefail
HOST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AD_ROOT="$(cd "$HOST_DIR/../../.." && pwd)"
cd "$AD_ROOT"

echo "■ stopping web apps + API + worker…"
for p in 3100 3200 8070 8180; do
  pid=$(ss -tlnHp 2>/dev/null | grep ":$p " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -n "${pid:-}" ]; then
    # `npm exec next start` holds the port through a child next-server; kill the
    # children too, or the old build keeps serving under a rebuilt .next (chunk 400s).
    pkill -TERM -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null
    for _ in 1 2 3 4 5; do ss -tlnH | grep -q ":$p " || break; sleep 1; done
    ss -tlnH | grep -q ":$p " && fuser -k "$p/tcp" >/dev/null 2>&1
    echo "  stopped :$p"
  fi
done
pgrep -f "(^|/)python[0-9.]* -m ads[.]worker$" | xargs -r kill -TERM && echo "  stopped worker(s) (re-queued their in-flight work)"

if [ "${1:-}" = "--all" ]; then
  bash "$HOST_DIR/services.sh" down hard
  docker stop ads-postgres >/dev/null 2>&1 && echo "  stopped ads-postgres"
else
  bash "$HOST_DIR/services.sh" down
  echo
  echo "✔ shared tier still up for other projects (stop it too with: down.sh --all):"
  for entry in "8010:LLM" "8011:embeddings" "8012:gate" "8188:ComfyUI"; do
    p="${entry%%:*}"; n="${entry##*:}"
    echo -n "  :$p $n  "; ss -tlnH | grep -q ":$p " && echo UP || echo "DOWN"
  done
fi
echo; free -h | head -2
