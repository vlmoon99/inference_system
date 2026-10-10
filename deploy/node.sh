#!/bin/bash
# The node watchdog of the inference cluster. Same rules as boostcontent_backend/deploy/node.sh.
#
#   deploy/node.sh run       the loop (user unit inf-node.service; the only thing that starts the stack)
#   deploy/node.sh status    every node's role and engines, as this node sees them
#   deploy/node.sh promote   make THIS node's database the master now (the old master yields on its next tick)
#
# Every node runs everything (hosts/core.yaml): the core models, a balancer over all nodes' models, a gateway.
# The ONE thing that has a master is the gateway's database (project keys, usage):
#   1. A node's role is what its Postgres says: read-write = master, standby = follower.
#   2. Every gateway writes to the master's database; followers keep a live copy of it.
#   3. A follower that sees no master takes over when the master it followed and every node above it in NODES
#      have been unreachable on every path (database port, tailnet ping, LAN ping) for NODE_FAIL_SECS. A host
#      that still answers pings is alive, so nobody takes over from it.
#   4. Two masters: the one promoted LAST wins (Postgres timeline; a tie goes to the higher priority). The
#      loser dumps its database to .data/backups/before-reclone-*.dump and re-clones as a follower.
# NODES in the host's .env lists every node in priority order ("tailnetIP[/lanIP] ..."), the same on all nodes.
# No voting: all nodes sit in one room. Across sites this needs a quorum (Patroni).
set -uo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
dir=$(for d in "$root"/hosts/*/; do [ -f "$d.env" ] && echo "$d"; done | head -1)   # the host dir that has a .env
[ -n "$dir" ] || { echo "no hosts/<host>/.env on this machine"; exit 1; }
cd "$dir"
set -a; . ./.env; set +a
: "${NODES:?set NODES in .env}" "${REPL_PASSWORD:?set REPL_PASSWORD in .env}"
TICK=${NODE_TICK:-5}; FAIL_SECS=${NODE_FAIL_SECS:-60}
read -ra N <<<"$NODES"
ME=-1; for i in "${!N[@]}"; do [ "${N[$i]%%/*}" = "$TAILNET_IP" ] && ME=$i; done
[ "$ME" -ge 0 ] || { echo "TAILNET_IP $TAILNET_IP is not in NODES"; exit 1; }
DB=inf-litellm-db
Q="select case when pg_is_in_recovery() then 't 0' else 'f ' || ('x' || substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8))::bit(32)::int end"

log() { echo "$(date -u +%FT%TZ) node$ME($TAILNET_IP) $*"; }
# .master = whose database the local gateway writes to (127.0.0.1 on the master itself)
dc() { MASTER_IP=$(cat .master 2>/dev/null || echo 127.0.0.1) docker compose "$@"; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }

local_role() { running $DB && docker exec $DB psql -X -U litellm -d litellm -tAc "$Q" 2>/dev/null; }
probe() {      # "f <timeline>" = master, "t 0" = follower, "" = no answer
  local conn="host=$1 port=5440 user=replicator dbname=litellm connect_timeout=3"
  if running $DB; then docker exec -e PGPASSWORD="$REPL_PASSWORD" $DB psql -X "$conn" -tAc "$Q" 2>/dev/null
  else docker run --rm --network host -e PGPASSWORD="$REPL_PASSWORD" --entrypoint psql postgres:17 -X "$conn" -tAc "$Q" 2>/dev/null; fi
}
pings() {      # the host itself answers on any path (NODE_NO_PING=1: a test knob, the database port alone decides)
  [ "${NODE_NO_PING:-0}" = 1 ] && return 1
  local ip; for ip in ${1//\// }; do ping -c1 -W2 "$ip" >/dev/null 2>&1 && return 0; done; return 1
}

# A container Docker itself failed to start at boot (tailnet IP missing) comes up afterwards WITHOUT its
# published ports, and neither restart nor stop/start brings them back (Docker 29.2.1, seen 2026-10-10):
# recreate it. Data lives in named volumes, so this is safe.
heal_ports() {
  local s c want
  for s in $SERVICES; do
    c=$(dc ps -q "$s" 2>/dev/null); [ -n "$c" ] || continue
    want=$(docker inspect "$c" --format '{{len .HostConfig.PortBindings}}')
    [ "$want" -gt 0 ] && [ -z "$(docker port "$c")" ] || continue
    log "$s has no published ports, recreating"
    dc up -d --no-build --no-deps --force-recreate "$s" 2>&1 | tail -2
  done
}

ensure_stack() {          # everything in the compose project is running, and the gateway uses the right database
  local want=$1 up s
  if [ "$(cat .master 2>/dev/null)" != "$want" ]; then
    echo "$want" > .master; log "gateway database: $want"
    running inf-litellm && dc up -d --no-build --no-deps litellm 2>&1 | tail -1
  fi
  up=$(dc ps --services --status running 2>/dev/null)
  for s in $SERVICES; do
    grep -qx "$s" <<<"$up" && continue
    log "bringing the stack up ($s is not running)"
    dc up -d --no-build 2>&1 | grep -vE 'Running|Healthy|Waiting' | tail -8
    heal_ports; return
  done
}

ensure_master() {
  if [ "${ROLE_OK:-}" != 1 ]; then          # the replication login: followers stream with it, nodes probe with it
    docker exec -i $DB psql -X -q -U litellm -d litellm -v ON_ERROR_STOP=1 -v pw="$REPL_PASSWORD" >/dev/null <<'SQL' && ROLE_OK=1
select format('%s role replicator login replication password %L',
              case when exists (select 1 from pg_roles where rolname = 'replicator') then 'alter' else 'create' end, :'pw') \gexec
SQL
  fi
  ensure_stack 127.0.0.1
}

clone_from() {   # throw the local copy away and become a follower of node $1
  local mip=${N[$1]%%/*} ts; ts=$(date -u +%Y%m%dT%H%M%S)
  log "re-cloning the gateway database from node$1 ($mip)"
  if [ -n "$(local_role)" ]; then
    running inf-backup || { dc up -d --no-build --no-deps backup >/dev/null 2>&1; sleep 2; }
    docker exec inf-backup sh -c "pg_dump -h litellm-db -U litellm -Fc litellm > /backups/before-reclone-$ts.dump" \
      && log "local database saved first: .data/backups/before-reclone-$ts.dump" || log "could not dump the local database first"
  fi
  dc stop litellm-db >/dev/null 2>&1
  dc run --rm --no-deps -T -e PGPASSWORD="$REPL_PASSWORD" --entrypoint bash litellm-db -c '
    set -e
    bb() { gosu postgres pg_basebackup -h '"$mip"' -p 5440 -U replicator -D "$PGDATA" -R -X stream -S node'"$ME"' "$@"; }
    find "$PGDATA" -mindepth 1 -delete; chown postgres:postgres "$PGDATA"; chmod 700 "$PGDATA"
    bb -C 2>/dev/null || { find "$PGDATA" -mindepth 1 -delete; bb; }' 2>&1 | tail -3
  dc up -d --no-build litellm-db 2>&1 | tail -1
}

ensure_follower() {   # of node $1
  local mip=${N[$1]%%/*}
  if ! docker exec $DB psql -X -U litellm -d litellm -tAc "show primary_conninfo" 2>/dev/null | grep -q "host=$mip "; then
    clone_from "$1"; return
  fi
  ensure_stack "$mip"
}

promote() {
  log "PROMOTING: this node's gateway database becomes the master"
  docker exec -u postgres $DB pg_ctl promote -w -t 60 2>&1 | tail -1
  ROLE_OK=; ensure_master
}

tick() {
  local mine i r tl best_i=-1 best_tl=-1 mytl was
  mine=$(local_role)
  for i in "${!N[@]}"; do
    [ "$i" = "$ME" ] && continue
    r=$(probe "${N[$i]%%/*}"); ROLE[$i]=$r
    if [[ $r == f\ * ]]; then tl=${r#f }; if (( tl > best_tl )); then best_tl=$tl; best_i=$i; fi; fi
  done
  case "$mine" in
    f\ *)
      mytl=${mine#f }
      if (( best_i >= 0 )) && { (( best_tl > mytl )) || { (( best_tl == mytl )) && (( best_i < ME )); }; }; then
        log "two masters: node$best_i (timeline $best_tl) beats this node (timeline $mytl); yielding"
        clone_from "$best_i"
      else
        ensure_master
      fi ;;
    t\ *)
      if (( best_i >= 0 )); then DOWN_SINCE=0; WAITING=; ensure_follower "$best_i"; return; fi
      was=$(docker exec $DB psql -X -U litellm -d litellm -tAc "show primary_conninfo" 2>/dev/null | grep -oE 'host=[^ ]+' | cut -d= -f2)
      for i in "${!N[@]}"; do                   # is the master's host, or anyone above me, still there?
        [ "$i" = "$ME" ] && continue
        (( i < ME )) || [ "${N[$i]%%/*}" = "$was" ] || continue
        if [ -n "${ROLE[$i]:-}" ] || pings "${N[$i]}"; then
          [ "${WAITING:-}" = "$i" ] || { log "no master, but node$i is alive: not taking over"; WAITING=$i; }
          DOWN_SINCE=0; return
        fi
      done
      WAITING=
      if (( DOWN_SINCE == 0 )); then
        DOWN_SINCE=$(date +%s); log "no master and nobody above this node answers; taking over in ${FAIL_SECS}s unless one returns"
      fi
      if (( $(date +%s) - DOWN_SINCE >= FAIL_SECS )); then DOWN_SINCE=0; promote; fi ;;
    *)
      running $DB && return                       # starting up: ask again next tick
      if dc run --rm --no-deps -T --entrypoint sh litellm-db -c 'test -s "$PGDATA/PG_VERSION"' 2>/dev/null; then
        log "database container is down, starting it"; dc up -d --no-build litellm-db 2>&1 | tail -1
      elif (( best_i >= 0 )); then clone_from "$best_i"
      elif (( ME == 0 )); then log "empty node, no master anywhere: starting as the first master"; dc up -d --no-build litellm-db 2>&1 | tail -1
      fi ;;
  esac
}

case "${1:-status}" in
  run)
    for i in $(seq 120); do ip -4 addr | grep -q " ${TAILNET_IP}/" && break; sleep 5; done   # Docker starts before tailscaled
    for i in $(seq 60); do docker info >/dev/null 2>&1 && break; sleep 5; done
    SERVICES=$(dc config --services)
    log "watchdog started (priority $ME of ${#N[@]}, takeover after ${FAIL_SECS}s, $(wc -w <<<"$SERVICES") services)"
    declare -a ROLE; DOWN_SINCE=0; WAITING=; ROLE_OK=
    while true; do tick; sleep "$TICK"; done ;;
  promote)
    [[ "$(local_role)" == t\ * ]] || { echo "this node is not a healthy follower: $(local_role)"; exit 1; }
    promote ;;
  status)
    for i in "${!N[@]}"; do
      ip=${N[$i]%%/*}
      if [ "$i" = "$ME" ]; then r=$(local_role); else r=$(probe "$ip"); fi
      case "$r" in f\ *) d="database MASTER (timeline ${r#f })";; t\ *) d="database follower";; *) pings "${N[$i]}" && d="database not answering (host alive)" || d="DOWN";; esac
      e=""; for p in 8000:gateway 8010:llm 8013:embed 8102:image; do
        c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://$ip:${p%%:*}/health$([ ${p%%:*} = 8000 ] && echo /liveliness)")
        e+=" ${p##*:}=$([ "$c" = 200 ] && echo ok || echo -)"
      done
      echo "node$i $ip  $d |$e$([ "$i" = "$ME" ] && echo '   <- this node')"
    done
    echo "this node's gateway writes to: $(cat .master 2>/dev/null || echo '?')" ;;
  *) echo "usage: $0 run|status|promote"; exit 1 ;;
esac
