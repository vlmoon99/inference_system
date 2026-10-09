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
| 2 | inference v1 on dgx-spark, `smoke.sh`, tag `inf-v1` | in progress |
| 3 | dgx-spark-2 replica + node-agent, tag `inf-v1.1` | not started |
| 4 | BoostContent backend, `smoke.sh`, tag `bc-v1` | not started |
| 5 | BoostContent admin, tag `bc-admin-v1` | not started |

## Next action

Step 2: build `hosts/dgx-spark/compose.yaml` (vLLM, qwen3-embed, ComfyUI, inf-image adapter, LiteLLM + its Postgres,
SearXNG, node-agent, admin), bring it up, then write `smoke.sh`.

## Decisions made overnight (owner asleep 2026-10-09 night → review in the morning)

* Owner asked for a non-stop loop overnight with no input. Deletion is the only thing held back.

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
