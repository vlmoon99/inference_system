#!/usr/bin/env bash
# rtx3090x3 — bring the model services up ON THIS BOX (models-only: the worker that
# drives them runs on the Spark, see workers/rtx3090x3.env). install.sh installed
# the units; this just starts them and shows what answers.
#   up.sh            comfy-img comfy-vid gen-image gen-video gen-tts
#   up.sh --worker   also the local ads-worker (only if you deliberately run one here)
set -uo pipefail
UNITS="comfy-img comfy-vid gen-image gen-video gen-tts"
# GPU2: the second video renderer, unless the LLM holds the card (gpu2.sh llm)
docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^ads-llm$' || UNITS="$UNITS comfy-vid2 gen-video2"
[ "${1:-}" = "--worker" ] && UNITS="$UNITS ads-worker"
# user units (install.sh with ADS_USER_UNITS=1) need no sudo; start only what is installed
if [ -f "$HOME/.config/systemd/user/comfy-img.service" ]; then
  SC="systemctl --user"; DIR="$HOME/.config/systemd/user"
else
  SC="sudo systemctl"; DIR=/etc/systemd/system
fi
UNITS=$(for u in $UNITS; do [ -f "$DIR/$u.service" ] && echo "$u"; done)
$SC start $UNITS
sleep 2
for p in 8010 8102 8104 8114 8105; do
  code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "http://localhost:$p/health" || true)
  echo "  :$p -> ${code:-down}"
done
