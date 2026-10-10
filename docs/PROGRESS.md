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
| 1 | stop + clean old system (owner approves deletion list) | done: deleted 2026-10-10 on both Sparks |
| 2 | inference v1 on dgx-spark, `smoke.sh`, tag `inf-v1` | done: smoke 11/11 |
| 3 | dgx-spark-2 replica + node-agent, tag `inf-v1.1` | done: smoke 14/14 |
| 4 | BoostContent backend, `smoke.sh`, tag `bc-v1` | done: smoke 16/16 local + via Cloudflare, pgTAP 38 |
| 5 | BoostContent admin, tag `bc-admin-v1` | done (verified over HTTP, password left unset) |

## Next action

The migration is closed. Both systems are 2-node clusters with automatic takeover (since the power-off of
2026-10-10 14:44 UTC **dgx-spark-2 is the master of both databases** and dgx-spark the follower; README "The cluster" here, "More than one machine" in boostcontent_backend).
Public: https://boostcontent.io and https://api.vramhouse.com/v1. What remains:
1. **First real power-off: 2026-10-10 14:44 UTC (power key on dgx-spark), see the log.** The takeover worked.
   Still open from it: the five checks in README "Never tried on a real power loss" were not all walked through,
   and nobody failed back: BoostContent migrations and pgTAP now have to run on dgx-spark-2 (the master).
2. Flutter side applies boostcontent_backend/docs/FLUTTER.md section 0 (the answers to its ten requests). Obliq (the owner's own
   development only): its key and base URL are in `~/.config/inference_system/obliq.env` on dgx-spark (mode 600).
3. Off until the owner sets them in `.env` (both repos, README "Alerts and the off-site copy"): `ALERT_URL`,
   `OFFSITE_REMOTE` + `OFFSITE_PASSPHRASE` (probably the owner's Mac, later).
4. Non-core models: the mechanism is in place (README "Add a non-core model") but none is defined yet. Candidates
   on spark-2: Qwen-Image-2512, LTX-2.5 (weights already in ~/ComfyUI). Owner: later.
5. Not done: real sign-in providers; the other projects.

Done 2026-10-10 in the hardening round (owner's decisions from the grilling session):
* Leftovers deleted on both Sparks: the first replica attempt, Garage key `bc-replica`, `ai-brain/backup.pass`,
  30 bucket objects with no row in `private.media` (13 left = 13 rows), `.env.before-cluster`, `dart:stable`,
  `before-*.dump`, two 0-byte `.dump.tmp`.
* SearXNG is a core service (`hosts/core.yaml`, settings in `services/searxng`, mounted read-only): 38 results on each node.
* Per-key limits: new keys get 30 requests/min and 2 in parallel (admin → Projects → limits). `boostcontent` =
  120/min, 12 parallel (its own engine runs up to 4 LLM + 4 image calls at once, so 8 would be the exact edge).
  `obliq` = 30/2. Verified through api.vramhouse.com: 6 parallel chats → 2×200, 4×429.
* `deploy/node.sh` in both repos: `alert` (POST `{"text"}` to `ALERT_URL`) on takeover, two masters, a node gone /
  back, engine or gateway down 10 min (inference), public URL down 2 min (BoostContent), no dump for 26 h, no
  off-site copy for 3 days. Verified with a local listener: follower databases stopped 100 s → "does not answer"
  from both watchdogs after 60 s, "fine again" after the restart.
* Off-site copy (compose service `offsite`, rclone crypt; BoostContent also sends the pictures): run by the
  master's watchdog daily, hourly retry. Verified to a test folder: files encrypted, restored dumps byte-identical,
  a failing remote is logged as FAILED. Not verified against a real remote (none exists yet).
* After it: inference smoke 14/14 on both nodes, BoostContent smoke 16/16, both sites 200.

## 2026-10-10 evening: a Mac as a stand-in host (Sparks off, power cut)

`hosts/mac/` (new, used by nothing else): MTPLX with Qwen 3.5 4B 4-bit + Qwen3-Embedding 0.6B, and FLUX.2 klein 4B
4-bit on mflux, behind one small gateway on `:8000` with the same three public ids. BoostContent runs against it
with `boostcontent_backend/deploy/mac/` (16/16 smoke, local and through a quick tunnel). Numbers and limits:
`hosts/mac/README.md`. Nothing on the Sparks changes: no existing code file was edited in either repo.

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
* Power cut: user units `inf-stack.service` (both Sparks) and `bc-stack.service` wait for the tailnet IP, then
  `docker compose up -d`. Docker starts before tailscaled, and tailnet-bound ports otherwise stay down.
  Verified by stopping those containers and starting the units. A real reboot happened 2026-10-10 08:16 UTC
  (both Sparks) and found one gap, now fixed: see the log.
* Backups: bc-backup + inf-backup pg_dump daily to `<repo>/.data/backups` (14 days). Garage objects are NOT
  backed up yet (photos/renders); for S1 move them to R2 (docs/SCALING_AND_CLOUD.md).
* Test data from tonight was deleted from the BoostContent DB and bucket; smoke.sh now cleans up after itself.
* Admin passwords: both consoles were tested over HTTP with a temporary password, then reset to first-visit.
  BoostContent admin tokens now die when the password is reset (found during that test).

## Deletion list: approved by the owner and DELETED 2026-10-10

Kept as the record of what was removed.

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
* 2026-10-09 extras: nightly pg_dump (both stacks, verified restorable listing), Garage CORS (preflight via
  Cloudflare OK), boot units on both Sparks, docs/SCALING_AND_CLOUD.md, admin usage keeps names of deleted keys,
  smoke cleanup. Final: inference smoke 14/14, BoostContent smoke 16/16 (local + public), pgTAP 38/38.
* 2026-10-10 08:16 UTC REAL REBOOT of both Sparks (power cut, not planned). The boot units brought both stacks up,
  but bc-caddy, bc-garage and inf-searxng ran **without their published ports**, so boostcontent.io answered 502
  for ~20 min. Cause: Docker tried to start them before the tailnet IP existed ("cannot assign requested address");
  the later `compose up` started them with no port mappings, and restart / stop+start didn't restore them
  (Docker 29.2.1). Fixed by recreating the three containers (data is in named volumes). Both `deploy/boot-up.sh`
  now recreate any service whose configured ports aren't published. Verified after the fix: inference smoke 14/14,
  BoostContent smoke 16/16, https://boostcontent.io 200. The self-heal branch ran as a no-op on the healthy
  system; it has NOT been exercised by another real reboot. dgx-spark-2 needed nothing (host-network services).
* 2026-10-10 09:15 UTC 1b STARTED: owner approved the deletion list ("approve it", and removed product_dream himself).
  Re-checked first: advertisment_system main == origin/main, the 3 unmerged ai/* branches are on origin, the other 4
  have nothing ahead of main; tunnel token in boostcontent_backend/.env is identical to ~/.config/ads/tunnel.env;
  each listed volume belongs only to a listed container. Deleting now on both Sparks.
* 2026-10-10 09:20 UTC 1b DONE: everything on the list is gone from both Sparks (plus the drop-in dir
  `gen-video.service.d` on spark-2, part of a listed unit). Verified: `docker ps -a` shows only inf-*/bc-*, the
  never-delete images are all present, boostcontent.io / gateway / SearXNG answer 200. Disk: 332 G used.
  Left alone, not on the list: `~/.ai-worker/work/*` (7 stale worktrees of the deleted repo).
* 2026-10-10 README for the app rewritten from the running API (every shape and status was called, not assumed).
  Found while doing it: a default Supabase client asks for schema `public` and gets 406 (needs `schema: 'api'`);
  `not_found` comes back as HTTP 500; pgTAP is 38, not 36.

* 2026-10-10 errors: migration 0009 gives client errors their HTTP status (not_found 404, quota_exceeded 402,
  limit_* 409, daily_cap 429, not_authenticated 401). Verified over HTTP (404, 402) + pgTAP 38/38.
* 2026-10-10 replica on dgx-spark-2 (`~/boostcontent_backend/replica`, compose `bc-replica`): hot standby via
  slot `replica1`, daily pg_dump of the standby, hourly media copy with read-only Garage key `bc-replica`.
  Verified: a row written on the primary was readable on the standby 2 s later; the standby refuses writes;
  the dump lists with pg_restore; 12/12 objects copied; smoke 16/16 afterwards. bc-db was restarted twice
  (seconds) for the new pg_hba + tailnet port. NOT verified: promotion/failover, behaviour across a reboot.
  Owner decisions: no coding LLM on the Sparks (coding stays on the Mac); `~/.config/ai-worker` deleted on
  request (`~/.config/ai-brain/backup.pass` left: it is a backup password, not asked about).
* 2026-10-10 cluster: `replica/` and `boot-up.sh` replaced by `deploy/node.sh` + `bc-node.service` on both
  Sparks (owner's design: fixed priority, pings, no quorum, all nodes in one room). Verified live:
  spark-2 took over (public API back 38 s after the decision; smoke 16/16 through Cloudflare on spark-2);
  dgx-spark returned, yielded (timeline 1 < 2) and re-cloned; fail-back the same way (timeline 3); a stopped
  stack on a live host is NOT taken over; final state dgx-spark master, smoke 16/16, pgTAP 38/38.
  Takeover was simulated (`NODE_NO_PING=1`), not a power-off. boostcontent.io was down ~2 min + ~40 s during the tests.
  Also: a failed nightly dump now retries in 5 min (the 08:16 one after the power cut had failed silently).

* 2026-10-10 inference cluster (owner: "same logic, core models on all nodes + non-core reachable from any node").
  `hosts/core.yaml` on both Sparks: LLM + embeddings + Qwen-Edit + balancer + gateway + admin everywhere; engines
  moved from 127.0.0.1 to the tailnet IP behind `ENGINE_KEY`; `deploy/node.sh` + `inf-node.service` replace
  boot-up.sh. vllm-node / svc-embed images and the two model folders were copied to spark-2 (60 GB over the LAN).
  Decisions: (1) balancing is Caddy's job, not LiteLLM's: LiteLLM with two deployments hung >100 s on a dead host
  (measured in a scratch gateway), Caddy health-checks every 5 s; (2) the gateway and the admin run on every node,
  only the gateway DATABASE has a master, and every gateway writes to it; (3) BoostContent on each node uses its
  own node's gateway, and the public name tries every gateway.
  Verified: 12 parallel chats split 6/6; LLM killed under load → 10/10 in-flight requests answered (after
  adding `request_buffers`: without it the retry failed and the good node was marked down for 20 s); gateway +
  database of the master stopped → public chat back in 46 s from spark-2, a key created there works everywhere;
  return + fail-back (timeline 3, dgx-spark master again); smoke 14/14 here, 13/13 on spark-2 (no SearXNG there);
  BoostContent smoke 16/16. The LLM here was reloaded 4 times during the tests (~4 min each, chat served by spark-2).

* 2026-10-10 14:41–14:47 UTC FIRST REAL OUTAGE of dgx-spark. 14:41:41 spark-2 logged "no node is the master"
  (dgx-spark's host still answered pings, so by design no takeover); 14:44:47 dgx-spark logged "Power key
  pressed short" and powered off; 14:46:08 spark-2 took over both databases (timeline 4); 14:46:29 dgx-spark
  was back, saw the newer master, saved `.data/backups/before-reclone-20261010T144700.dump` and re-cloned as
  a follower. Public API down about 5 min. Why dgx-spark stopped serving at 14:41 was NOT investigated.
* 2026-10-10 app requests (the Flutter side's ten items; owner's decisions: guest only, one plan `free` with
  limits from the console, no Pro / trial / payments, no automatic posts, options + learning instead, separate
  post language incl. Russian). boostcontent_backend migrations 0010 + 0011: plan `standard` → `free`,
  `me` has plan / onboarded_at / left, `complete_onboarding`, optional business name (the profile analysis
  names it), `utc_offset`, fixed profile shape (`private.profile_shape`), `download_url` on every picture,
  `choose_post` (the rest of the batch is passed and given back: a round costs 1 post), job kind `learn`
  (prompt k1) → `profiles.learned`, ideas prompt i3 reads the memory + picks + passes, `delete_account`
  (rows, sign-in, pictures on EVERY node via `s3_node_endpoints`). Prompts now p2 / i3 / l1 / k1.
  Verified: pgTAP 79/79 on a throwaway database built from the migrations (`DB_CONTAINER=… scripts/test.sh`);
  client/dart 37/37 against https://boostcontent.io (a real round of 3 options in 132 s, choice, refund
  3 posts/9 pictures → 1/3, learned memory, attachment download, account deletion with 20 × 204 and nothing
  left on the follower's storage). NOT verified: pgTAP on the live master, the admin console screens, anything
  in Flutter itself. Applied on the master by hand: `docker compose --profile master run --rm --no-deps
  migrate` (+ `configure`), then `notify pgrst, 'reload schema'`. Dump taken first:
  `~/before-0010-20261010.dump` on dgx-spark-2.
