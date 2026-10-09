# Platform plan v1 — settled 2026-10-09 (grilling session)

Two new repos replace `advertisment_system` and `product_dream`, which live on only in git:
* `~/Documents/dev/inference_system` → github.com/vlmoon99/inference_system — one local inference cloud for N projects
* `~/Documents/dev/boostcontent_backend` → github.com/vlmoon99/boostcontent_backend — codeless backend for BoostContent

The Flutter client and web hosting are out of scope (the owner brings the client later).
The Spark serves only this project for now; other projects get re-set-up later, in another session.

---

## 1. inference_system

**Language: Python, no reinvented wheels.** Engines are black boxes; Python glue is kept thin.

| Piece | Choice |
|---|---|
| Gateway | **LiteLLM Proxy**: OpenAI-compatible chat/embeddings/images, one key per project, usage per project, load-balancing across nodes. Its own Postgres. LiteLLM's built-in UI is off |
| LLM | vLLM, `nvidia/Qwen3.6-35B-A3B-NVFP4` (reuse the locally built `vllm-node` image) |
| Embeddings | **qwen3-embed only** (bge-m3 dropped) |
| Image | ComfyUI + `qwen_image_edit_2511_api.json` (fp8, Lightning 4-step, ~20 s warm) behind a slim adapter that speaks OpenAI `/v1/images/generations` + `/v1/images/edits` |
| Search | SearXNG as a shared service (more MCP servers will follow) |
| node-agent | Python, a port of sysinfo.py: `/system` (GPU/RAM/temps) + `/containers` (list/up/down/restart), shared secret, tailnet-only, **in the same compose file** as each host's services |
| Admin | React + Vite + Tailwind (plain JS) + small FastAPI. Bound to the tailnet IP only. **The first visit from any headscale device sets the password** (bcrypt/argon2 hash), and a CLI on the Spark resets it. v1 scope: nodes + live GPU, containers, models loaded where, projects + keys, usage per project/day, log tail, playground. Start small |
| Exposure | **tailnet-only for now**; a public inference domain comes later. Obliq is down until then |
| Layout | `hosts/<host>/compose.yaml` + host.yaml + README (measured numbers) per machine, started by a user systemd unit (no sudo). Containers are named `inf-*` |
| Multi-node | dgx-spark-2 runs a second Qwen-Edit replica + node-agent from v1.1. Add a PC = copy a host folder, start compose, add it to LiteLLM |
| Dormant knowledge | `knowledge/`: Ukrainian TTS (RadTTS/mykyta наголос chain, verbalizer), LTX/Wan/FLUX/Qwen-2512 workflows, weights.lock, host READMEs, all as code + docs, not running. TTS weights (7.7 GB, gitignored) move to a gitignored `weights/` dir with a lock (source + sha256) |

**Image URL contract.** A caller may pass a pre-signed **GET** URL for the input image and a pre-signed **PUT**
URL for the output. The adapter downloads the input, renders, uploads the result, and returns `{key, w, h, seed}`.
Without those fields it returns plain OpenAI base64. Inference stays stateless and storage-agnostic.

## 2. boostcontent_backend (one compose file, cloud-redeployable)

| Piece | Choice |
|---|---|
| DB | custom image `FROM postgres:17` + pg_net, pg_cron, pgvector; pgcrypto for signing. Own database, never shared |
| API | public **PostgREST** over an `api` schema (views + functions); RLS on everything, scoped by `account_id` |
| Auth | **GoTrue, anonymous sign-ins only** (guest mode); real sign-in providers are added later on the same GoTrue |
| Storage | **Garage** (S3). Postgres signs pre-signed PUT/GET URLs in SQL (SigV4 via pgcrypto); no bytes pass through Postgres |
| Generation | client calls `rpc/generate_posts(business_id, n)`; a pg_cron tick + pg_net state machine calls LiteLLM (tailnet URL + project key from config). Only on request in v1, no weekly cron yet |
| Content | many businesses per account; profile = description + photos → vision LLM → editable profile (customers, tone, best days, brand colors, products). Posts = caption + up to 3 images, each a Qwen-Edit of the customer's own photo. Customers copy and download, then post by hand |
| Language | the LLM works in **structured English JSON** (profile, ideas: angle, product_ref, audience, caption_en, image_prompts[≤3], hashtags), then the caption is localized into the user's language |
| Fine-tune data | `gen_trace` (input, raw output, schema version, model, latency) + signals: copied, downloaded, edited (with the edited text), deleted, regenerated. JSONL export function |
| Quota | per account per month, defaults in `plans` (3000 images, 1000 posts), per-account override set in the admin, resets on the 1st UTC. Reserved when a job starts, refunded if it fails. **LLM calls never count.** Plus a global daily safety cap. No payments |
| Admin | a second PostgREST on `admin_api` with its own JWT secret, bound to the tailnet IP only. First visit sets the password (bcrypt in DB). React + Vite + Tailwind (JS) served by a static container on the tailnet IP. v1 = users, businesses, content, quotas, at a basic level |
| Edge | `cloudflared` in the compose file, reusing the existing named tunnel: `api.boostcontent.io` (`/auth/v1` → GoTrue, `/rest/v1` → PostgREST) and `media.boostcontent.io` → Garage. The bare domain shows nothing until the client ships |
| Tooling | dbmate (plain-SQL migrations), pgTAP (RLS, quotas, signing, state machine) |
| Cloud path | the same compose file on a VPS, or managed PG + R2. Inference is reached via its future public domain (config row) |

## 3. Execution order (one commit per unit to main, tests green, proven against the server + logs before UI)

0. Push old git (main + `ai/0a8090de`, `ai/9668cb32`, `ai/c5034952`), move TTS weights, port the knowledge
1. Stop and clean. **Deletion list shown, then approved once by the owner.** Must survive: model weights (HF cache,
   ComfyUI models, the vLLM model), the `vllm-node` image, TTS weights, the coding LLM (`~/work`, :8899/:4000), tailscale,
   cloudflared. `product_dream/` = the owner's `sudo rm -rf`
2. inference v1 on dgx-spark → `smoke.sh` (chat + stream, embeddings, image edit via pre-signed in/out ~20 s,
   admin shows nodes + GPU, revoked key → 401, usage per project) → tag `inf-v1`
3. dgx-spark-2 replica + node-agent → tag `inf-v1.1`
4. BoostContent backend → `smoke.sh` (guest sign-in → business → upload photo → 1 post with 3 images →
   quota decrements → trace row) → tag `bc-v1`
5. BoostContent admin → tag `bc-admin-v1`

Steps 0 → 5 run straight through and report at each tag. Only step 1's deletion waits for approval.
