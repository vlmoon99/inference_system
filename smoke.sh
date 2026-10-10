#!/usr/bin/env bash
# Inference definition of done. Run on any node (it tests that node's own gateway):   ./smoke.sh
# Creates a throwaway project key, exercises every model through the gateway exactly like a
# project would, checks revocation, usage logging and the node-agents, then deletes the key.
# Exit 0 = PASS. Needs: curl, python3 (stdlib only).
set -uo pipefail
cd "$(dirname "$0")"
set -a; . "$(ls hosts/*/.env | head -1)"; set +a      # the host dir that has a .env is this machine
GW="http://${TAILNET_IP}:8000"
BLOB_PORT=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
TMP=$(mktemp -d)
PASS=0; FAIL=0
ok()   { echo "  PASS  $*"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $*"; FAIL=$((FAIL+1)); }
mk()   { curl -s "$GW$1" -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'content-type: application/json' -d "$2"; }
call() { curl -s -w '\n%{http_code}' "$GW$1" -H "Authorization: Bearer $KEY" -H 'content-type: application/json' -d "$2"; }
code() { tail -n1; }
body() { sed '$d'; }
jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1" 2>/dev/null; }

# a pre-signed-URL stand-in: PUT stores, GET serves (what Garage/R2 do for a signed URL)
python3 - "$TMP" "$BLOB_PORT" <<'EOF' &
import os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
root, port = sys.argv[1], int(sys.argv[2])
class H(BaseHTTPRequestHandler):
    def p(self): return os.path.join(root, self.path.split('?')[0].strip('/').replace('/', '_'))
    def do_PUT(self):
        open(self.p(), 'wb').write(self.rfile.read(int(self.headers['content-length']))); self.send_response(200); self.end_headers()
    def do_GET(self):
        if not os.path.exists(self.p()): self.send_response(404); self.end_headers(); return
        d = open(self.p(), 'rb').read(); self.send_response(200); self.send_header('content-length', str(len(d))); self.end_headers(); self.wfile.write(d)
    def log_message(self, *a): pass
ThreadingHTTPServer((os.environ['TAILNET_IP'], port), H).serve_forever()
EOF
BLOB_PID=$!
cleanup() { kill $BLOB_PID 2>/dev/null; [ -n "${TOKEN:-}" ] && mk /key/delete "{\"keys\":[\"$TOKEN\"]}" >/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT
png_size() { python3 -c "import struct,sys; d=open(sys.argv[1],'rb').read(24); assert d[:8]==b'\x89PNG\r\n\x1a\n'; print('%dx%d'%struct.unpack('>II',d[16:24]))" "$1" 2>/dev/null; }

echo "== gateway $GW"
NAME="smoke-$(date +%s)"
GEN=$(mk /key/generate "{\"key_alias\":\"$NAME\",\"metadata\":{\"project\":\"$NAME\"}}")
KEY=$(echo "$GEN" | jget 'd["key"]'); TOKEN=$(echo "$GEN" | jget 'd["token"]')
[ -n "$KEY" ] && ok "project key created ($NAME)" || { bad "key/generate: $GEN"; exit 1; }

echo "== chat"
R=$(call /v1/chat/completions '{"model":"qwen3.6-35b","messages":[{"role":"user","content":"Reply with exactly: pong"}],"max_tokens":400,"chat_template_kwargs":{"enable_thinking":false}}')
[ "$(echo "$R" | code)" = 200 ] && echo "$R" | body | jget 'd["choices"][0]["message"]["content"]' | grep -qi pong \
  && ok "chat completion" || bad "chat: $(echo "$R" | body | head -c 300)"
S=$(curl -sN "$GW/v1/chat/completions" -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"model":"qwen3.6-35b","stream":true,"messages":[{"role":"user","content":"Count 1 to 5"}],"max_tokens":200,"chat_template_kwargs":{"enable_thinking":false}}')
[ "$(echo "$S" | grep -c '^data: {')" -gt 3 ] && echo "$S" | grep -q 'data: \[DONE\]' && ok "chat streaming ($(echo "$S" | grep -c '^data: {') chunks)" || bad "stream: $(echo "$S" | head -c 300)"

echo "== embeddings"
R=$(call /v1/embeddings '{"model":"qwen3-embedding-0.6b","input":["hello world","привіт світ"]}')
DIM=$(echo "$R" | body | jget 'len(d["data"][0]["embedding"])')
[ "$(echo "$R" | code)" = 200 ] && [ "$DIM" = 1024 ] && ok "embeddings (2 × $DIM)" || bad "embeddings: $(echo "$R" | body | head -c 300)"

echo "== image (pre-signed URL contract)"
T0=$(date +%s)
R=$(call /v1/images/generations "{\"model\":\"qwen-image-edit\",\"prompt\":\"a cup of coffee on a wooden table, morning light\",\"size\":\"768x1024\",\"output_put_url\":\"http://${TAILNET_IP}:$BLOB_PORT/smoke/text.png?X-Amz-Signature=x\"}")
URL=$(echo "$R" | body | jget 'd["data"][0]["url"]')
[ "$(echo "$R" | code)" = 200 ] && [ "$URL" = "http://${TAILNET_IP}:$BLOB_PORT/smoke/text.png" ] && [ "$(png_size "$TMP/smoke_text.png")" = 768x1024 ] \
  && ok "text→image uploaded to the PUT URL, 768x1024 ($(( $(date +%s) - T0 )) s)" || bad "text image: $(echo "$R" | body | head -c 300)"
T0=$(date +%s)
R=$(call /v1/images/generations "{\"model\":\"qwen-image-edit\",\"prompt\":\"Same cup, now on a snowy windowsill\",\"size\":\"1024x1024\",\"image_url\":\"http://${TAILNET_IP}:$BLOB_PORT/smoke/text.png?sig=1\",\"output_put_url\":\"http://${TAILNET_IP}:$BLOB_PORT/smoke/edit.png?sig=2\"}")
[ "$(echo "$R" | code)" = 200 ] && [ "$(png_size "$TMP/smoke_edit.png")" = 1024x1024 ] \
  && ok "photo edit via image_url → output_put_url ($(( $(date +%s) - T0 )) s)" || bad "edit: $(echo "$R" | body | head -c 300)"

if [ "$(echo "${IMAGE_HEALTH:-}" | tr ',' '\n' | grep -c .)" -gt 1 ]; then
  echo "== image load-balancing across $(echo "$IMAGE_HEALTH" | tr ',' ' ')"
  before=$(for u in $(echo "$IMAGE_HEALTH" | tr ',' ' '); do curl -s "$u/health" | jget 'd["renders"]'; done | tr '\n' ' ')
  pids=""
  for i in 1 2 3 4; do
    call /v1/images/generations "{\"model\":\"qwen-image-edit\",\"prompt\":\"a red apple, studio light\",\"size\":\"512x512\",\"output_put_url\":\"http://${TAILNET_IP}:$BLOB_PORT/smoke/lb$i.png\"}" > "$TMP/lb$i.out" &
    pids="$pids $!"
  done; wait $pids        # not a bare wait: the blob server is a background job too
  after=$(for u in $(echo "$IMAGE_HEALTH" | tr ',' ' '); do curl -s "$u/health" | jget 'd["renders"]'; done | tr '\n' ' ')
  served=$(python3 -c "import sys; b,a=sys.argv[1].split(),sys.argv[2].split(); print(sum(1 for x,y in zip(b,a) if int(y)>int(x)))" "$before" "$after")
  okn=$(grep -l '"url"' "$TMP"/lb*.out | wc -l)
  [ "$okn" = 4 ] && [ "$served" = "$(echo "$IMAGE_HEALTH" | tr ',' '\n' | grep -c .)" ] \
    && ok "4 concurrent renders, every image node served some (renders before: $before → after: $after)" \
    || bad "load-balancing: $okn/4 ok, nodes that served: $served (before $before, after $after)"
fi

echo "== search"
curl -sf "http://${TAILNET_IP}:8888/search?q=kyiv+bakery&format=json" | jget 'len(d["results"])' | grep -qE '^[1-9]' \
  && ok "SearXNG json results" || bad "SearXNG returned no results"

echo "== node-agents"
for n in $(echo "$ADMIN_NODES" | tr ',' ' '); do
  U=${n#*=}; N=${n%%=*}
  C=$(curl -sf "$U/containers" -H "X-Agent-Token: $AGENT_TOKEN" | jget 'sum(1 for c in d if c["state"]=="running")')
  G=$(curl -sf "$U/system" -H "X-Agent-Token: $AGENT_TOKEN" | jget 'len(d["gpus"])')
  [ -n "$C" ] && [ "${G:-0}" -ge 1 ] && ok "$N: $C containers running, $G GPU(s) reported" || bad "$N node-agent unreachable"
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$U/containers")" = 401 ] && ok "$N: agent refuses requests without the token" || bad "$N: agent open without token"
done

echo "== usage + revocation"
sleep 5
U=$(docker exec inf-litellm-db psql -U litellm -tAc "select count(*) from \"LiteLLM_SpendLogs\" where api_key='$TOKEN'")
[ "${U:-0}" -ge 4 ] && ok "usage logged for the project ($U requests)" || bad "usage rows for the key: ${U:-none}"
mk /key/delete "{\"keys\":[\"$TOKEN\"]}" >/dev/null; TOKEN=""
REV=""
for i in $(seq 15); do   # every worker caches a key ≤ 10 s (user_api_key_cache_ttl)
  c=""; for j in 1 2 3 4; do c="$c$(curl -s -o /dev/null -w '%{http_code}' "$GW/v1/models" -H "Authorization: Bearer $KEY")"; done
  [ "$c" = 401401401401 ] && { REV=$((i*2)); break; }; sleep 2
done
[ -n "$REV" ] && ok "revoked key → 401 on every worker within ${REV} s" || bad "revoked key still accepted after 30 s"

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
