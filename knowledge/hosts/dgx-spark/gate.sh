#!/usr/bin/env bash
# mt-gate — translation quality gate (MetricX-24 large, Apache-2.0) on the CPU.
# A plain uvicorn process from the repo's venv; ~5 GB RAM, no GPU (docs/AGENTIC_PLAN.md §2.4).
#
# Usage: inference/hosts/dgx-spark/gate.sh {up|down|status|logs}
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
ROOT="$PWD"

GATE_PORT="${GATE_PORT:-8012}"
LOG="${GATE_LOG:-$ROOT/backend/data/mt-gate.log}"
PIDFILE="${GATE_PID:-$ROOT/backend/data/mt-gate.pid}"

up() {
  if status >/dev/null 2>&1; then echo "gate: already running (:${GATE_PORT})"; return; fi
  mkdir -p "$(dirname "$LOG")"
  ( cd inference/adapters/mt-gate && \
    PORT="$GATE_PORT" GATE_DEVICE="${GATE_DEVICE:-cpu}" \
    nohup "$ROOT/.venv/bin/python" app.py >> "$LOG" 2>&1 & echo $! > "$PIDFILE" )
  echo "gate: starting (:${GATE_PORT}); first /score loads the model (~5 GB, one-time download)"
}
down() {
  if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null && echo "gate: stopped"; rm -f "$PIDFILE"; else echo "gate: not running"; fi
}
status() { curl -s -m 3 "http://127.0.0.1:${GATE_PORT}/health" && echo; }
logs()   { tail -40 "$LOG"; }

case "${1:-}" in
  up) up ;; down) down ;; status) status || echo "gate: down" ;; logs) logs ;;
  *) echo "usage: $0 {up|down|status|logs}"; exit 1 ;;
esac
