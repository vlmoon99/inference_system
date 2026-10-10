# inference_system

One local inference cloud for many projects: **LiteLLM** in front of self-hosted engines on tailnet
machines. Public at `https://api.vramhouse.com/v1` (only `/v1`, through BoostContent's entry and tunnel);
everything else is tailnet-only. Design: `docs/PLATFORM_PLAN.md`; scaling + cloud: `docs/SCALING_AND_CLOUD.md`;
progress log: `docs/PROGRESS.md`.

## The cluster

Every node runs the same thing (`hosts/core.yaml`), so any node can serve any request and any node can die:

```
            project ──► gateway (LiteLLM, :8000 on every node)
                           │   checks the project key, logs usage
                           ▼
                        balancer (Caddy, 127.0.0.1:18xxx on every node)
                           │   asks each node's engine /health every 5 s, sends to the least busy live one
              ┌────────────┴────────────┐
           node A engines            node B engines         ← CORE models: on every node
           LLM · embeddings · image  LLM · embeddings · image
                                     + a non-core engine    ← NON-CORE: only where a host file adds it,
                                                              but served by every node's gateway
```

| | Where | If a node dies |
|---|---|---|
| **Core models** (`qwen3.6-35b`, `qwen3-embedding-0.6b`, `qwen-image-edit`) | every node | the balancers drop it within 5 s; requests in flight are sent again to a live node |
| **Non-core models** | the nodes that have the engine | served while one of its nodes lives |
| **Gateway + balancer + admin + search** | every node | use another node's `:8000`; the public name does that by itself |
| **Gateway database** (project keys, usage) | one master, the others live standbys | the next node in `NODES` takes over (`deploy/node.sh`), about 45 s; every gateway then writes to the new master |

`deploy/node.sh` (user unit `inf-node.service`) is the only thing that starts the stack. Don't `docker compose up`
by hand.

```
deploy/node.sh status     # every node: database role, gateway, each core engine
deploy/node.sh promote    # make this node's database the master now
journalctl --user -u inf-node -f
```

**Add a core node**: copy the two images that have no Dockerfile (`docker save vllm-node:latest | ssh <node>
docker load`, same for `product_dream-svc-embed:latest`) and the two model folders from `~/.cache/huggingface/hub`,
clone this repo, write `hosts/<host>/compose.yaml` (a copy of dgx-spark-2's) and its `.env` (dgx-spark's with
`TAILNET_IP`, `HOST_NAME`, `COMFY_DIR`, `COMFY_BASE` changed), add the node to `NODES` and `ADMIN_NODES` on every
node, `docker compose build`, `deploy/install-unit.sh`. It clones the database and joins the balancers.

**Add a non-core model** (lives on some nodes, reachable through all):
1. the engine: a service in those hosts' `hosts/<host>/compose.yaml`, listening on the tailnet IP with `ENGINE_KEY`;
2. the balancer: a block in `gateway/Caddyfile` with a free `127.0.0.1` port and exactly those nodes;
3. the name: an entry in `gateway/litellm.yaml` pointing at that port.
Push, pull on every node, `docker compose up -d lb litellm` (through `node.sh` it happens at the next restart).

Proven on the two Sparks (2026-10-10): 12 parallel chats split 6/6; the LLM killed under load, 10 of 10 requests
in flight still answered; the gateway + database of the master stopped, the public API answered again after
46 s from the other node, a key made there worked everywhere after the first node returned; smoke 14/14.
Limits: no voting (all nodes in one room, see `deploy/node.sh`); a request that is already streaming when its
node dies is cut (only requests that have not started answering are sent again); the admin password file is per
node (copied once: change it on both).

**Never tried on a real power loss.** Every takeover above was simulated by stopping containers
(`NODE_NO_PING=1`). The first real outage is the test. After one, check:
1. `deploy/node.sh status` here and in boostcontent_backend, on the node that stayed up: it is the master of both.
2. `journalctl --user -u inf-node -u bc-node --since -1h | grep -E 'ALERT|TAKEOVER'`: when it decided, and why.
3. `./smoke.sh` in both repos on the surviving node; https://boostcontent.io and https://api.vramhouse.com/v1/models from outside.
4. When the dead node is back: it shows as follower in both `status` outputs within ~2 minutes, and
   `.data/backups/before-reclone-*.dump` on it holds what it had before it re-cloned.
5. If a node came back as a second master and did not yield, stop its unit (`systemctl --user stop inf-node bc-node`)
   and read its journal before anything else.

### Alerts and the off-site copy (both off until set)

`ALERT_URL` in the host's `.env`: the watchdog POSTs `{"text": "..."}` there when it takes over, when two masters
meet, when a node stops answering (and when it is back), when an engine or the gateway has not answered for
10 minutes, when no dump is newer than 26 hours, when no off-site copy succeeded for 3 days. A Telegram bot URL
(`https://api.telegram.org/bot<token>/sendMessage?chat_id=<id>`) or a Slack webhook works as it is.
`deploy/node.sh alert` sends a test message. The same lines are always in the journal as `ALERT:`.

`OFFSITE_REMOTE` + `OFFSITE_PASSPHRASE`: the master copies the nightly dumps to that
[rclone remote](https://rclone.org/docs/#connection-strings) once a day, encrypted before they leave, and tries
again every hour while it fails (a sleeping Mac is fine; for `:sftp,…,pass=` the password must go through `rclone obscure` first). **Keep the passphrase somewhere that is not a Spark**:
without it the copy is noise. Restore: `rclone copy` from a `crypt` remote with the same two values.
Use a different folder for boostcontent_backend, which has the same two settings.

## Using it from a project

```
base_url = https://api.vramhouse.com/v1     # OpenAI-compatible, from anywhere (project key)
base_url = http://100.64.0.1:8000/v1        # inside the tailnet: any node's gateway (…0.1, …0.12)
api_key  = <the project's key from the admin → Projects>
```

Every key has two limits, set in the admin (Projects → limits): requests per minute and requests in parallel.
A new key gets 30 and 2. Over the limit the gateway answers **HTTP 429**: wait and send again.

| model | endpoint |
|---|---|
| `qwen3.6-35b` | `/v1/chat/completions` (streaming, tools; thinking on by default, `chat_template_kwargs.enable_thinking=false` to skip) |
| `qwen3-embedding-0.6b` | `/v1/embeddings` (1024-dim) |
| `qwen-image-edit` | `/v1/images/generations` (JSON) and `/v1/images/edits` (multipart) |

**Images, URL contract.** Add `image_url` (pre-signed GET of a photo to edit) and/or `output_put_url`
(pre-signed PUT where the PNG goes). The reply's `data[0].url` is the object URL without the signature.
Without `output_put_url` you get OpenAI's `b64_json`. Without an input photo the model paints a blank
canvas of `size`. Warm render ≈ 20 s.

Search: `http://<any node>:8888/search?q=…&format=json` (SearXNG, on every node).

## Layout

```
gateway/litellm.yaml     models → backends (add a machine = another deployment under the same name)
hosts/<host>/            compose.yaml + .env(.example) + README with measured numbers, one per machine
services/image           OpenAI images API over ComfyUI (Qwen-Image-Edit-2511)
services/comfyui         ComfyUI runtime image (pinned packages on the NGC base)
services/embed           Qwen3-Embedding server
services/node-agent      per-machine /system + container control
services/admin           operator console (FastAPI + React/Vite/Tailwind)
smoke.sh                 definition of done: ./smoke.sh → PASS
knowledge/, weights/     dormant reference (Ukrainian TTS, video workflows, old hosts)
```
