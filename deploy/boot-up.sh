#!/bin/bash
# After a power cut: Docker starts before tailscaled, and containers that publish on the tailnet IP fail
# to bind and are not retried. This waits for the IP, then brings the host's compose project up
# (idempotent). Run by the user unit inf-stack.service:  deploy/boot-up.sh <host-dir>
set -uo pipefail
dir=$(cd "$(dirname "$0")/../hosts/${1:?host dir, e.g. dgx-spark}" && pwd)
ip=$(grep -E '^TAILNET_IP=' "$dir/.env" | cut -d= -f2)
for i in $(seq 120); do ip -4 addr | grep -q " ${ip}/" && break; sleep 5; done
for i in $(seq 60); do docker info >/dev/null 2>&1 && break; sleep 5; done
cd "$dir" && docker compose up -d --no-build 2>&1 | tail -20
# A container Docker itself failed to start at boot (tailnet IP missing) comes up afterwards WITHOUT its
# published ports, and neither restart nor stop/start brings them back (Docker 29.2.1, seen 2026-10-10):
# recreate it. Data lives in named volumes, so this is safe.
for s in $(docker compose ps --services); do
  c=$(docker compose ps -q "$s"); [ -n "$c" ] || continue
  want=$(docker inspect "$c" --format '{{len .HostConfig.PortBindings}}')
  [ "$want" -gt 0 ] && [ -z "$(docker port "$c")" ] || continue
  echo "boot-up: $s has no published ports, recreating"
  docker compose up -d --no-build --no-deps --force-recreate "$s" 2>&1 | tail -3
done
