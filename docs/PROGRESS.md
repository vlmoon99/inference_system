# Migration progress log

The single source of truth for "where are we" in the platform migration
([PLATFORM_PLAN.md](PLATFORM_PLAN.md)). Any session, human or Claude, starts by reading
**Current state** and **Next action**, then continues from there.

Rules for whoever works on it:
* Update this file and commit + push it **after every finished unit**, and **before** any destructive
  or long operation (write "STARTED: …" first, so an interrupted step is visible).
* Never mark something done that wasn't verified. Say what's verified and how.
* An interrupted step is re-checked against the machine (`docker ps`, `systemctl --user`, `git status`)
  before it is resumed. Most steps are written so that re-running them is safe.

## Current state

| Step | What | Status |
|---|---|---|
| 0 | push old git, move TTS weights, port knowledge | done |
| 1 | stop + clean old system (owner approves deletion list) | STOPPED (both Sparks); deletion waits for owner |
| 2 | inference v1 on dgx-spark, `smoke.sh`, tag `inf-v1` | done: smoke 11/11 |
| 3 | dgx-spark-2 replica + node-agent, tag `inf-v1.1` | done: smoke 14/14 |
| 4 | BoostContent backend, `smoke.sh`, tag `bc-v1` | done: smoke 16/16 local + via Cloudflare, pgTAP 38 |
| 5 | BoostContent admin, tag `bc-admin-v1` | done (verified over HTTP, password left unset) |

## Next action

Plan steps 0–5 are done. Waiting on the owner for: (1) approve the deletion list below, (2) set both admin
passwords on first visit. Overnight extras in progress: nightly DB backups, Garage CORS (browser uploads),
scaling/cloud doc.

## Decisions made overnight (owner asleep 2026-10-09 night → review in the morning)

* Owner asked for a non-stop loop overnight with no input. Deletion is the only thing held back.
* **The admin password is NOT set**: the owner sets it on the first visit to http://100.64.0.1:8091.
* Ports: gateway :8000, admin :8091, node-agent :8090, SearXNG :8888, all on the tailnet IP only (checked:
  127.0.0.1 and the LAN IP refuse). :4000/:8899 left free for a coding LLM (`~/work` doesn't exist; nothing to keep).
* vLLM `--gpu-memory-utilization 0.26` (0.20 fails; 0.35 starved ComfyUI), ComfyUI `--highvram`. Numbers in
  hosts/dgx-spark/README.md.
* ComfyUI got its own image (services/comfyui): the old `pd-comfyui` container had pip-upgraded packages in
  its writable layer only; a fresh container from the base image crashed (comfy_kitchen 0.2.16 vs 0.2.31).
* Public model ids: `qwen3.6-35b`, `qwen3-embedding-0.6b`, `qwen-image-edit` (short, provider-neutral).
* LiteLLM key cache TTL 10 s, so a revoked key dies everywhere within ~10 s (2 workers each cache keys).
* LiteLLM pinned to 1.104.2 by digest (`main-stable` moves).
* LiteLLM runs **1 worker**: with 2, least-busy routing and the key cache are per-process (all renders went
  to one box; revoked keys lived up to 60 s). One async worker is plenty for this load.
* inf-image requires `Authorization: Bearer $IMAGE_API_KEY` (the gateway sends it): spark-2's image service
  is on the tailnet and must not bypass project keys.
* ComfyUI image takes `BASE` per box: dgx-spark `pd-comfyui:base`, dgx-spark-2 `ads-comfyui:v0.33.3`.
* BoostContent routing: the existing CF tunnel already points boostcontent.io → localhost:3100, so **bc-caddy
  listens on :3100** and splits by path (/auth/v1 GoTrue, /rest/v1 PostgREST, /bc-media Garage). No Cloudflare
  dashboard change needed. Media URLs are https://boostcontent.io/bc-media/… (path-style S3).
* BoostContent got its own gateway key (LiteLLM key alias `boostcontent`), like any other project.
* Image format for posts: 1024×1280 (4:5, Instagram feed). Setting `image_size` in private.settings.
* Engine concurrency: 4 LLM + 4 image jobs in flight (settings `llm_concurrency`, `image_concurrency`).
* Garage CORS is not configured yet: needed only for a browser (Flutter web) uploading directly.
* **boostcontent.io is live again** with the new backend (cloudflared in the bc compose). The root path answers
  "BoostContent"; *.boostcontent.io (old tenant subdomains) reach the same Caddy and get the same answer.
* Admin passwords: both consoles were tested over HTTP with a temporary password, then reset to first-visit.
  BoostContent admin tokens now die when the password is reset (found during that test).

## Deletion list, awaiting owner approval

Everything here is **stopped and disabled** overnight, but **not deleted**. Owner: reply "approve deletion" (or strike items).

**dgx-spark (this box)**
| Item | Size | Note |
|---|---|---|
| `~/Documents/dev/advertisment_system/` | 8.7 GB | code is in git (origin == local); `backups/` 606 MB of old DB dumps = old client data, deleted by decision |
| `~/Documents/dev/product_dream/` | 12 KB | root-owned: owner runs `sudo rm -rf ~/Documents/dev/product_dream` |
| `~/.config/systemd/user/ads-{stack,tunnel,analytics,backup,offload-watch,reflect}.{service,timer}` | | 10 unit files |
| `~/.config/ads/` | | `tunnel.env` (the CF tunnel token is **copied** into boostcontent_backend's gitignored `.env` first), `searxng/` (settings ported first) |
| containers `ads-postgres`, `ads-searxng`, `spark-llm`, `spark-embed`, `spark-embed-qwen3`, `pd-comfyui` | | replaced by the `inf-*` compose services |
| volumes `advertisment_system_ads_pgdata`, `advertisment_system_ads_pgdata_pgvector`, `ef75b0a5…` (anonymous) | | old DB data |
| image `pgvector/pgvector:pg16` | 0.5 GB | |

**dgx-spark-2 (100.64.0.12)**
| Item | Note |
|---|---|
| `~/ads/` (39 MB) | old checkout |
| user units `gen-image.service`, `gen-video.service`, `ads-dropcache.{service,timer}` | |
| containers `ads-comfyui`, `ads-comfyui-vid` | replaced by `inf-comfyui` |

**Never deleted** (no Dockerfile exists, or slow to re-download): images `vllm-node`, `product_dream-svc-embed`,
`pd-comfyui:base`, `ads-comfyui:v0.33.3` (spark-2), `searxng/searxng`; `~/ComfyUI` on both boxes (85 GB models);
`~/.cache/huggingface` (38 GB); `inference_system/weights/`; `~/work` (coding LLM).

## Log (newest last)

* 2026-10-09: plan settled with the owner in a grilling session (PLATFORM_PLAN.md v1). Progress log created.
  Owner said "go", so steps 0→5 run straight through; only step 1's deletion waits for approval.
* 2026-10-09 0a DONE: advertisment_system main (331775c) + ai/0a8090de, ai/9668cb32, ai/c5034952 pushed;
  verified with `git fetch` (main == origin/main, the 3 branches exist on origin). The other 4 ai/* were already merged.
* 2026-10-09 0b DONE: `advertisment_system/data/models` (7.7 GB, 119 files) moved to `weights/tts/` (gitignored).
  Sources in `weights/weights.lock`, per-file hashes in `weights/tts.sha256` (not committed).
* 2026-10-09 0c DONE: `knowledge/` (119 files, 1.4 MB): tts-uk, workflows, adapters, hosts, training, docs, and
  `containers/*.inspect.json` (secrets redacted). Found: vllm-node, product_dream-svc-embed and pd-comfyui:base
  have no Dockerfile anywhere. The images are the only copy, so they must never be pruned.
* 2026-10-09 1a STARTED: deletion list written; now stopping + disabling old services on both Sparks.
* 2026-10-09 1 STOPPED: dgx-spark: ads-{stack,tunnel} + 4 timers disabled+stopped; containers ads-postgres, ads-searxng,
  spark-llm, spark-embed, spark-embed-qwen3, pd-comfyui stopped with restart=no. dgx-spark-2: gen-image, gen-video,
  ads-dropcache.timer disabled; ads-comfyui(-vid) stopped, restart=no. Verified: no ads processes, `docker ps` empty on both.
  **boostcontent.io is offline from now on** (tunnel stopped). Tunnel token copied to boostcontent_backend/.env (600, gitignored);
  SearXNG settings ported to hosts/dgx-spark/searxng (secret moved to hosts/dgx-spark/.env).
* 2026-10-09 2 DONE: dgx-spark stack up (9 containers). smoke.sh 11/11: chat + stream, embeddings 1024-d,
  text→image and photo edit through pre-signed PUT/GET (19–20 s warm), SearXNG, node-agent (+401 without token),
  usage rows per key, revocation → 401. Run on the Spark itself (no other tailnet machine was online).
* 2026-10-09 3 DONE: dgx-spark-2 runs inf-comfyui + inf-image (100.64.0.12:8102, key) + node-agent from
  ~/inference_system (git clone). Gateway has 2 `qwen-image-edit` deployments. smoke.sh 14/14 incl. 4 concurrent
  renders split across both boxes. Fixed: keep-warm stayed cold 15 min after a failed first try.
* 2026-10-09 4 (part): boostcontent_backend up: db (pg17 + pg_net 0.20.5, pg_cron, pgvector), dbmate migrations
  0001–0006, GoTrue anonymous, PostgREST ×2, Garage, Caddy. Verified: SigV4 in SQL = AWS test vector; signed
  PUT/GET internal + public; tampered URL 403; guest → business → photo → generate 2 posts → profile (vision) →
  ideas → uk captions → 6 Qwen-Edit images, quota 2/6 used, gen_trace rows. Fixed: pg_net worker watched the
  wrong database (`pg_net.database_name`).
* 2026-10-09 4 DONE (tag bc-v1): smoke.sh 16/16 on http://127.0.0.1:3100 AND on https://boostcontent.io through
  the tunnel (pre-signed media URLs verify through Cloudflare). ~60 s from generate_posts to a ready post with
  3 images. pgTAP 38/38. Fixed: GoTrue 500 after a DB restart (pooled dead connection).
* 2026-10-09 5 DONE (tag bc-admin-v1): admin console baked into the bc-caddy image (one compose = whole deploy),
  verified: stats, users, quota override, posts with images, training export, wrong password 403, stale token 403.
  Inference admin verified too: 2 nodes live, models, projects, usage, chat, remote logs.
