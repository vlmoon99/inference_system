# Scaling and the way to the cloud

Written 2026-10-09 from measured numbers (host READMEs, smoke runs). It covers both repos:
`inference_system` (the shared GPU cloud) and `boostcontent_backend` (the first product on it).

## What one unit of work costs today

| Step | Where | Measured |
|---|---|---|
| style profile (vision LLM, 1 photo) | dgx-spark vLLM | ~10–90 s (depends on queue) |
| ideas for a batch | vLLM, JSON | ~7 s |
| localize one caption | vLLM, JSON | ~5–45 s (queues behind other LLM calls) |
| one image (Qwen-Edit 1024×1280, warm) | either Spark, ComfyUI | **~20 s, one at a time per box** |
| **one post, 3 images, uk** | end to end | **~60 s** (images on both boxes in parallel) |

**The bottleneck is images**: each box renders serially. Two Sparks ≈ 6 images/min ≈ 2 posts/min ≈ 2,800
posts/day at 100 % use. The LLM (54× concurrency headroom in the KV cache) and Postgres are far from limits.

## inference_system: adding capacity

1. **Another machine** (the 3090 box, a new Spark, a rented GPU): copy `hosts/dgx-spark-2/` to
   `hosts/<name>/`, adjust the compose (`BASE` image, `TAILNET_IP`), `docker compose up`, add one deployment
   under `qwen-image-edit` in `gateway/litellm.yaml`, add the node to `ADMIN_NODES`, run `smoke.sh`.
   LiteLLM's least-busy routing spreads the load immediately. Render parity: same `weights.lock` files, no GGUF swaps.
2. **More per box**: ComfyUI renders serially. A second ComfyUI + inf-image pair on the same GB10 (memory
   allows: ~30 GB per pair) doubles a box's image rate if the GPU isn't saturated. **Measure first** (GPU util in the admin).
3. **Cheaper images**: 1024×1280 → 896×1120, or 4 → 3 steps, is ~25 % faster. That's a quality call; A/B it on real posts.
4. **Cloud burst** (optional): any OpenAI-images-compatible endpoint becomes one more `qwen-image-edit`
   deployment with a lower priority / `order`, so LiteLLM only spills over to it when the local boxes are busy.
   The same inf-image container runs on RunPod/Modal next to a ComfyUI; the URL contract makes storage location irrelevant.
5. **Public domain** (when bought): a second tunnel → LiteLLM :8000 with the same project keys. Rate limits
   and budgets per key are LiteLLM settings (`rpm_limit`, `max_budget`) when external developers arrive.

## boostcontent_backend: stages

| Stage | Postgres | API tier | Storage | Inference | When |
|---|---|---|---|---|---|
| **S0 (now)** | bc-db on the Spark | PostgREST, GoTrue, Caddy in the same compose | Garage on the Spark | tailnet → LiteLLM | until the Spark's uptime or upload bandwidth is the problem |
| **S1 hybrid** | same compose on a cloud VM (Hetzner / DO / Fly), **or** managed Postgres that allows `pg_net` + `pg_cron` (Supabase; Neon has no pg_cron) | same containers, stateless | **Cloudflare R2** (S3 API: only the `S3_*` env values change; SigV4 is the same) | the VM joins the tailnet (tailscale container) **or** inference gets its public domain + key | when customers depend on 24/7 |
| **S2 scale** | bigger instance + read replica for the admin/analytics; partition `gen_trace` by month | N PostgREST replicas behind the tunnel/LB (stateless) | R2 + Cloudflare cache in front of GETs | several inference boxes + cloud burst | thousands of daily users |

What makes the move painless (already true):
* One compose file; config only through `.env`; no host paths in the database (only object keys).
* Storage via pre-signed S3 URLs computed in SQL: Garage today, R2 tomorrow, same code.
* The generation engine is inside Postgres: no worker processes to migrate.
* Nightly `pg_dump` (bc-backup). For S1, add WAL archiving (`wal-g` to R2) so a dead disk loses minutes, not a day.

The move itself (S0 → S1, ~1 hour, mostly waiting for copies):
1. `pg_dump` (bc-backup has one) → restore on the new Postgres; `garage` objects → R2 with `rclone sync`.
2. New `.env` (`S3_*` = R2, `INFERENCE_URL` = tailnet or public), `docker compose up -d --build`.
3. Point the Cloudflare tunnel's public hostname at the new machine (or run cloudflared there with the same token).
4. `BASE=https://boostcontent.io ./smoke.sh`.

## Known limits to watch

* **GB10 memory**: vLLM (0.26) + ComfyUI share 122 GB. Adding models on dgx-spark needs a new measurement
  (host README), especially after reboots when page cache is cold or full.
* **pg_net** is fire-and-forget HTTP with a response table (1-day TTL). The engine polls every 3 s, so very
  large fan-outs (>1000 concurrent jobs) should move the image step to a queue consumer. Not needed yet.
* **Guest accounts** are device-bound: a wiped phone loses the account. Real sign-in (GoTrue email/Apple/Google)
  is a config change plus `linkIdentity` on the client when needed.
* **Cloudflare free plan**: 100 MB request body limit (photo uploads are far below it).
