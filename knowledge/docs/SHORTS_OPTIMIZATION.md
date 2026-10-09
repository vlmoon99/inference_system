# Shorts optimization — quality ladder, judges, ratings loop, client adaptation

What landed on `feat/shorts-optimization` (2026-08-17) — §1–7 — plus the phase-2
**client-adaptation product** built on top of it (2026-08-18) — §8–12: onboarding,
the optimization center, the per-client adapter registry, and the fine-tuning
flow. Design rule
throughout: **every new capability is env-gated and degrades to the previous behavior**
(missing model/env/lib ⇒ one log line, pipeline never fails because an *optimization*
failed), and there are **no new schedulers or control planes** — everything is either a
step inside an existing request or an operator-run CLI script.

## 1. Quality ladder (draft / final)

`POST /v1/generate/short` gains `quality: "draft" | "final"` (default **final** —
API-compatible). `POST /v1/campaigns` gains the same field, default **draft** (campaigns
are volume). The ladder (`backend/ads/pipelines/short.py`):

| quality | video model | resolution | frames | use |
|---|---|---|---|---|
| `draft` | Wan2.2-TI2V-**5B** | 480×832 | 81 | fast previews, campaign volume, golden runs |
| `final` | Wan2.2-A14B (**14b**) | 720×1280 | 81 | ship quality |

The keyframe is rendered at the same W×H as the video. The video service request now
carries `model: "5b" | "14b" | null` (null ⇒ the service's `WAN_MODEL` env default,
which `inference/hosts/dgx-spark/services.sh` sets to `14b`); invalid values are a 422. The response's
`video.model` reports what **actually ran** (`wan2.2-5b` / `wan2.2-14b`).

## 2. Video service (:8104)

- **Per-request model selection** — see above; the pre-render ComfyUI `/free` and the
  vLLM sleep/wake wrap key off the *per-request* resolution, not the env.
- **Tiled VAE decode** — the two a14b workflow templates now use `VAEDecodeTiled`
  (tile 256, overlap 32, temporal 64/8; core ComfyUI node) instead of `VAEDecode`,
  cutting the decode-stage memory spike at 720×1280. The 5B templates are untouched
  (5B fits fine).
- **vLLM sleep mode** — new env `VLLM_SLEEP_URL` (e.g. `http://localhost:8010`).
  When set, a 14B render is wrapped with `POST /sleep?level=1` before submit and
  `POST /wake_up` in a `finally` block (runs even on 502/504/errors). Both calls have a
  10 s timeout and are logged-but-non-fatal on failure. Unset (the default) or a 5B
  render ⇒ complete no-op. **The LLM is briefly unavailable during 14B renders** —
  acceptable by explicit operator decision; enable it only after recreating the
  container as below.

### ⚠️ Live-tested 2026-08-17: wake is broken on the current vLLM build — keep this OFF

The full cycle was tested against the real container (vLLM
`0.23.1rc1.dev727+gec0ffaacc`, recreated with `--enable-sleep-mode` +
`VLLM_SERVER_DEV_MODE=1`): `POST /sleep?level=1` worked (6.5 s, `is_sleeping:true`),
but `POST /wake_up` failed with `'list' object has no attribute 'zero_'` — a
sleep-mode incompatibility with this NVFP4 + marlin MoE quantization path — leaving
the server stuck asleep until a container restart. The container was rolled back to
its exact original config (no sleep flag; the CuMem allocator that
`--enable-sleep-mode` switches on is not worth carrying while unusable).

**Leave `VLLM_SLEEP_URL` unset until the `vllm-node` image is upgraded past this
bug.** The video-service code path is implemented, env-gated, and ready; re-test the
sleep→wake→completion cycle after any vLLM upgrade before exporting the env. Note
that 14B renders already fit alongside the resident LLM today (that is current
production behavior) — sleep mode is extra headroom, not a prerequisite.

### Enabling sleep mode (container recreation — operator, by hand)

The vLLM flags are baked into the `spark-llm` container's Cmd; no script owns its
creation (house rule: no custom control planes — the operator runs the Spark by hand).
To add `--enable-sleep-mode` the container must be recreated with the **live** flags
plus the new one, **and `-e VLLM_SERVER_DEV_MODE=1`, which exposes the
`/sleep`/`/wake_up`/`/is_sleeping` endpoints** (cold start 3–4 min):

```bash
docker stop spark-llm && docker rm spark-llm
docker run -d --name spark-llm --gpus all --network host --ipc host --shm-size 32g \
  --restart unless-stopped \
  -v /home/server/.cache/huggingface:/cache/huggingface \
  -e HF_HOME=/cache/huggingface -e HF_HUB_OFFLINE=1 -e VLLM_MARLIN_USE_ATOMIC_ADD=1 \
  -e VLLM_SERVER_DEV_MODE=1 \
  vllm-node \
  vllm serve nvidia/Qwen3.6-35B-A3B-NVFP4 --served-model-name nvidia/Qwen3.6-35B-A3B-NVFP4 \
  --host 0.0.0.0 --port 8010 --tensor-parallel-size 1 --trust-remote-code \
  --kv-cache-dtype fp8 --attention-backend flashinfer --moe-backend marlin \
  --gpu-memory-utilization 0.35 --max-model-len 24576 --max-num-seqs 8 \
  --max-num-batched-tokens 8192 --enable-chunked-prefill --enable-prefix-caching \
  --load-format fastsafetensors --reasoning-parser qwen3 --tool-call-parser qwen3_xml \
  --enable-auto-tool-choice \
  --enable-sleep-mode
```

Then export `VLLM_SLEEP_URL=http://localhost:8010` in the environment that launches the
video service (`inference/hosts/dgx-spark/services.sh up video` inherits your shell env). Leave it unset to
keep the feature off — everything else works exactly as before.

## 3. VLM judges (keyframe best-of-N + output QA)

`providers/llm.py` gains `chat_json_vision(system, user_text, images, ...)` — the same
guided-decoding JSON call with base64 `data:` image parts (works on the multimodal
Qwen3.6 behind vLLM). Two uses in the short pipeline:

- **Keyframe best-of-N** — env `KEYFRAME_CANDIDATES` (default **3**, `<=1` = off).
  One FLUX call renders N candidates; the VLM scores each (artifacts / brand_fit /
  photoreal / overall, 0–10) and picks a winner. Any judge failure ⇒ candidate 0 with
  one log line. Skipped entirely when a library photo is the reference.
- **Output QA (reject sampling)** — env `VIDEO_QA` (default **"1"**),
  `VIDEO_QA_RETRIES` (default **1**). Four frame-center PNGs are sampled from the raw
  (pre-compose) clip via the bundled imageio-ffmpeg binary and judged for serious
  defects only (morphing, extra limbs, garbled text, major artifacts). A judged fail
  with retries left ⇒ re-render with the same everything and a fresh seed. ffmpeg or
  judge unavailable ⇒ `passed=true` with issues `["qa-unavailable"]` — QA never blocks
  shipping. QA runs concurrently with the (CPU) voiceover.

## 4. TTS (:8105) — engine registry, normalization, pronunciations

### Engine registry (uk/ru only; every other language keeps Kokoro unchanged)

Env `TTS_ENGINE_UK` / `TTS_ENGINE_RU`, values `piper` (code default) | `f5` |
`styletts2`. Engines load lazily; a configured engine whose libs/models are absent
falls back to **Piper — the never-fails baseline** — with exactly one warning per
(lang, engine). `GET /health` now reports per-language status
(`engines: {kokoro: {loadable}, uk: {configured, active, loadable}, ru: {...}}`) so a
fresh start with missing model files is visible before the first `/generate` fails.

Current reality (be aware, this is what's true — not the aspiration):

- **ru ⇒ F5-TTS** (`inference/hosts/dgx-spark/services.sh` launches with `TTS_ENGINE_RU=f5`): checkpoint
  [`TVI/f5-tts-ru-accent`](https://huggingface.co/TVI/f5-tts-ru-accent), **CC-BY-4.0
  (commercial OK, attribution required)**. Caveat: it is a finetune of `SWivid/F5-TTS`
  whose base weights are CC-BY-NC-4.0; the author republished under CC-BY-4.0, but the
  derivative-of-NC provenance carries residual legal risk — flagged to whoever owns
  compliance. (`Misha24-10/F5-TTS_RUSSIAN` is likely better but CC-BY-NC — internal
  eval only, not downloaded.) Any runtime F5 failure (OOM, missing vocoder, …)
  degrades to Piper per request.
- **uk ⇒ StyleTTS2-Ukrainian** (`inference/hosts/dgx-spark/services.sh` launches with
  `TTS_ENGINE_UK=styletts2`): checkpoint
  [`patriotyk/styletts2_ukrainian_single`](https://huggingface.co/patriotyk/styletts2_ukrainian_single),
  **MIT (model, inference code, and the `styletts2-inference` package — commercial OK)**,
  the single-speaker "filatov" male voice — the best open Ukrainian TTS today (no F5
  Ukrainian checkpoint exists; verified). Model dir `data/models/styletts2/uk/`
  (`config.yml` + `pytorch_model.bin` ~700MB + `style.pt` speaker embedding); env
  override `STYLETTS2_UK_MODEL_DIR`. The engine reruns the author's Space pipeline on
  already-normalized text: `ukrainian-word-stress` → `ipa_uk` G2P → IPA tokens → model
  (24 kHz out; `+` after a syllable forces stress, as in the Space). Runs on GPU when
  free (~1.3 GB VRAM peak, ~15× real-time), CPU otherwise (`STYLETTS2_DEVICE`
  override); any runtime failure degrades to Piper per request. `ref_audio_path` is
  ignored by this single-speaker checkpoint (honored automatically if an operator
  drops in a `multispeaker: true` checkpoint — the multispeaker variants are
  `patriotyk/styletts2_ukrainian_multispeaker_{hifigan,istftnet}`, also MIT, but
  need per-speaker style vectors and were judged a worse default than the tuned
  single voice).
- **F5 voice cloning** — request field `ref_audio_path` (relative to `ASSETS_DIR`,
  traversal-guarded) clones a per-brand voice; `voice=<name>` picks a bundled reference
  clip from `F5_REFS_DIR`; otherwise the per-lang default `<lang>.wav` is used.
  **The short pipeline now sets it automatically** (phase 2): when the app has a
  `voice_ref` library asset (see §8) *and* the language's engine can clone (f5 —
  i.e. `ru` with today's defaults), the clip's path is passed as `ref_audio_path`.
  Absent voice_ref or a non-cloning engine (uk styletts2, en kokoro) ⇒ the
  provider call is byte-identical to before.

Engine env: `F5_UK_MODEL_DIR`, `F5_RU_MODEL_DIR` (one `*.safetensors`/`*.pt` +
`vocab.txt`), `F5_REFS_DIR` (`<name>.wav` + `<name>.txt` transcript pairs),
`F5_NFE_STEP` (default 32), `STYLETTS2_UK_MODEL_DIR` / `STYLETTS2_RU_MODEL_DIR`
(dir with `config.yml` + `pytorch_model.bin` + `style.pt`; only uk assets exist),
`STYLETTS2_DEVICE` (default cuda-if-available), `STYLETTS2_SPEED` (default 1.0).

### Text normalization (`inference/adapters/gen-tts/normalize.py`)

Applied to every uk/ru request, pure functions, unit-tested:

- numbers → words via num2words (`50%` → «п'ятдесят відсотків» / «пятьдесят процентов»;
  `199 грн`, `$199`, `€ 5`, `199 USD`, `руб`/`₽` — prefix and suffix forms; decimals
  and years read sanely as cardinals),
- brand `pronunciations` replacements (case-insensitive, word-boundary),
- **optional** stress marking: uk via `ukrainian-word-stress`, ru via `ruaccent` —
  lazy imports from `services/requirements-tts-extras.txt`, which is **NOT installed
  by `make install`** (heavy deps; installing them is an operator choice). Missing lib
  ⇒ stress skipped silently after one log line. (`ukrainian-word-stress` IS installed
  in the Spark venv now — the styletts2 uk engine requires it — so uk text reaches
  the engine pre-stressed; the engine's own stress pass is idempotent.)

### Brand pronunciations (backend)

`apps.pronunciations` — nullable JSONB, brand-name → Cyrillic replacement (e.g.
`{"NovaTech": "НоваТек"}`). The short pipeline passes it to the TTS provider on every
voiceover; it affects the TTS *input* only, never captions or on-screen text. Older TTS
services simply ignore the extra fields.

### Model assets are repo-owned now

`product_dream/data/models` was wiped 2026-08-16 (the live :8105 survives on deleted
fds only — **any restart without re-provisioned assets breaks TTS**). All defaults and
`inference/hosts/dgx-spark/services.sh` now point at repo-owned paths; there are **no product_dream paths
left** in the TTS stack:

| path | contents | source |
|---|---|---|
| `data/models/kokoro/` | `kokoro-v1.0.onnx`, `voices-v1.0.bin` | kokoro-onnx GitHub releases |
| `data/models/piper/` | `uk_UA-ukrainian_tts-medium`, `ru_RU-ruslan-medium` (.onnx + .json) | HF `rhasspy/piper-voices` |
| `data/models/f5/ru/` | `model_last.safetensors` + `vocab.txt` | HF `TVI/f5-tts-ru-accent` |
| `data/models/f5/refs/` | bundled `uk|ru.wav` + `.txt` reference clips (committed) | piper-synthesized |
| `data/models/f5/uk/` | *empty* — no uk F5 checkpoint exists | — |
| `data/models/styletts2/uk/` | `config.yml`, `pytorch_model.bin`, `style.pt` | HF `patriotyk/styletts2_ukrainian_single` (MIT) |

Binaries are gitignored (see `data/models/.gitignore`, which doubles as the
re-provisioning recipe); only the small F5 reference clips are committed. All assets
above are on disk on the Spark today.

## 5. Client asset library (product grounding)

Real product photos beat any generated keyframe. New table `library_assets` + Bearer-
scoped API (`POST`/`GET /v1/library`, `DELETE /v1/library/{id}`; JPEG/PNG/WebP,
≤ 15 MB; files under `assets_dir/library/{app_id}/` with server-side UUID names, plus a
durable Postgres blob copy; foreign rows are always 404 — existence never leaked).

`ShortRequest.library_image_id` selects a library photo as the I2V reference: the
pipeline **skips FLUX** (and the keyframe judge), PIL cover-crops the photo to the
target W×H, and records `ref_image_source: "library"`. Ownership is checked at submit
time (404) and re-checked inside the pipeline; an unusable photo falls back to the
FLUX keyframe path with a log line. The client gets a Library page (upload/list/delete)
and a photo picker on the short form.

## 6. The ratings → adaptation loop

The feedback loop is: **rate → (optionally) rewrite the style → verify on golden
briefs**. Originally operator-run CLI only; since phase 2 the same loop also runs
as jobs through the **existing DB queue** (`style_optimize` / `golden_run` — §9),
still human-triggered, never on a schedule of its own.

1. **Reproducibility record** — every short's Asset stores `meta.gen_config`
   (schema 1): quality, video model, W×H×frames, the exact seed sent to the video
   service, the video prompt and the exact LLM script prompts, voice/text lang,
   keyframe judge scores, QA verdict, `ref_image_source`, workflow template stem.
   Missing pieces are `null`, never omitted. (Known nulls today: `tts_engine`/
   `tts_voice` — the TTS *service* reports them but the provider only returns the
   audio dict; the pipeline reads them tolerantly, so they populate when the provider
   forwards them.)
2. **Ratings** — `POST /v1/assets/{asset_id}/rating` `{stars: 1..5, tags?, comment?}`
   (tags from the fixed vocabulary `off-brand`, `artifacts`, `wrong-voice`,
   `wrong-message`, `bad-motion`, `other`; unknown tags 422). Append-only history; job
   serialization surfaces the **latest** rating per asset as `assets[].rating`. The
   client shows a star/tag/comment widget on shorts, job assets, and campaign pieces.
3. **`backend/scripts/style_optimizer.py`** — reads an app's rated assets (stars +
   tags + comments + each asset's `gen_config`), asks the LLM for a rewritten
   `brand_style`, prints an old/new diff; `--apply` stores a `style_versions` row and
   copies the body into the live `App.brand_style` (the hand-written original is
   snapshotted as `source='manual'` on the first apply). Since phase 2 it is a
   **thin CLI wrapper** over `backend/ads/pipelines/optimize.py` — the exact
   functions the queue's `style_optimize` job runs (logic lives there only).
   Unlike the queue job, the CLI accepts any number of ratings — the operator
   judges the evidence.
4. **`backend/scripts/golden_run.py`** — runs the app's golden briefs
   through the real short pipeline (draft quality, real DB + services, serial), scores
   each clip with the VLM rubric, writes `data/golden/runs/<app-id>-<ts>.jsonl` + a
   markdown summary, and diffs per-brief scores against the previous run. Run it
   before/after a style or pipeline change to see the delta. Also a thin wrapper
   over `pipelines/optimize.py` now; briefs come from the **`golden_briefs` DB
   table first** (onboarding §8), falling back to `data/golden/<app_id>.json`.
   (The queue job additionally stores `golden_runs`/`golden_results` DB rows;
   the CLI keeps its file-based reports.)

Usage, flags, and caveats for both scripts: [`../backend/scripts/README.md`](../backend/scripts/README.md).

## 7. Env knob reference

| Knob | Where | Default | Effect |
|---|---|---|---|
| `WAN_MODEL` | video service | `5b` in code, **`14b` via models.sh** | service-default checkpoint; per-request `model` overrides |
| `VLLM_SLEEP_URL` | video service | unset (off) | sleep/wake the LLM around 14B renders (needs `--enable-sleep-mode`) |
| `VIDEO_EVICT_BEFORE_LTX` | video service | `0` | `1` = clear ComfyUI before every `ltx2` render (old behaviour); default keeps LTX-2.5 resident — consecutive renders ~105 s instead of ~200 s |
| `CAMPAIGN_VIDEO_MODEL` | backend settings | `ltx2` | video model for campaign shorts when the request sets none (`5b` / `14b` / `ltx2` / `""` = quality ladder) |
| `CAMPAIGN_KEYFRAME_CANDIDATES` | backend settings | `2` | keyframe best-of-N for campaign shorts (standalone shorts keep `KEYFRAME_CANDIDATES`) |
| `TTS_ENGINE_UK` / `TTS_ENGINE_RU` | TTS service | `piper` in code; models.sh sets ru=`f5`, uk=`styletts2` | uk/ru engine; unavailable ⇒ Piper fallback |
| `F5_UK_MODEL_DIR` / `F5_RU_MODEL_DIR` / `F5_REFS_DIR` / `F5_NFE_STEP` | TTS service | `data/models/f5/...` / 32 | F5 checkpoint, reference clips, inference steps |
| `STYLETTS2_UK_MODEL_DIR` / `STYLETTS2_RU_MODEL_DIR` / `STYLETTS2_DEVICE` / `STYLETTS2_SPEED` | TTS service | `data/models/styletts2/<lang>` / cuda-if-available / 1.0 | StyleTTS2-Ukrainian model dir + runtime knobs |
| `KOKORO_ONNX` / `KOKORO_VOICES` / `PIPER_VOICES_DIR` | TTS service | repo `data/models/...` | model asset paths (product_dream paths removed) |
| `KEYFRAME_CANDIDATES` | backend pipeline | `3` (`<=1` off) | keyframe best-of-N with VLM judge |
| `VIDEO_QA` / `VIDEO_QA_RETRIES` | backend pipeline | `1` / `1` | output QA + seed-retry reject sampling |
| `MODEL_HOST` / `DRAIN_TIMEOUT_S` | tune executor (`tune.sh`) | `localhost` / `2700` | remote-models mode (skip local stop/restart) · generation-drain timeout |
| `SD_SCRIPTS_DIR` / `TRAINER_PYTHON` / `FLUX_TRAIN_DIR` / `TRAINER_EXTRA_ARGS` | flux_lora trainer | `.trainer/sd-scripts` / `.venv-train/bin/python` / `data/models/flux-train` / — | kohya toolchain locations + extra CLI args |
| `COMFY_LORAS_DIR` | install_adapter | `/home/server/ComfyUI/models/loras` | where client LoRAs are mirrored for serving |

API-level knobs (not env): `quality` on short/campaign requests, `library_image_id` on
shorts, `pronunciations` / `brand_examples_*` / `default_language` on the app, `kind`
on library uploads, `steps`/`rank` on training runs, `model`/`pronunciations`/
`ref_audio_path`/`lora_name`/`lora_strength` on the raw service contracts.

## 8. Client onboarding (phase 2 — P1/P2)

Everything the adaptation ladder needs from a client, collected once, checkable at
any time, and — crucially — **actually steering generation**. The client gets a
wizard at `/onboarding/[appId]` (each step saves independently, re-enterable);
the API surface underneath:

- **Brand examples** — `apps.brand_examples_loved` / `brand_examples_hated`
  (JSONB, `[{text, note?}]`, ≤5 each, text ≤600 chars), settable via
  `PATCH /v1/apps/{id}`. Injected into the **ad-post and short script prompts**:
  up to **3 loved** few-shots ("match this voice") + **2 hated** anti-examples
  ("NEVER write like this"), total injected example text hard-capped at
  **1200 chars** (truncate per item, keep order, loved first). No examples ⇒ the
  prompt is **byte-identical** to the pre-onboarding prompt (tested —
  `test_prompt_injection.py`).
- **`default_language`** — `String(8)`, default `"en"`, on the app.
- **Voice reference** — `POST /v1/library` gained an optional `kind` form field:
  `product_photo` (default) or `voice_ref` (wav/mp3/m4a, ≤10 MB; invalid kind
  ⇒ 422). At most **one active voice_ref per app** — a new upload replaces the
  previous row + file. Used for F5 voice cloning on shorts (§4).
- **Golden briefs** — new `golden_briefs` table + tenant-scoped CRUD at
  `GET/POST /v1/golden-briefs`, `PATCH/DELETE /v1/golden-briefs/{id}`, and
  `POST /v1/golden-briefs/suggest` — the LLM proposes **8** briefs from the
  brand profile; **nothing is stored** until the client POSTs the ones they
  keep. Golden runs (CLI and queue job) read the DB table first, file fallback.
- **Checklist** — `GET /v1/apps/{id}/onboarding` computes (never stores):
  `profile` (tone+audience+style set and non-default), `examples` (≥2 loved,
  ≥1 hated), `product_photos` (≥1), `golden_briefs` (≥5) — **required**;
  `voice_ref`, `pronunciations` — **recommended**. `complete` = all required.
  The client shows a banner on the studio pages while incomplete.

## 9. Optimization center (phase 2 — P3)

The §6 loop, automated **through the existing DB queue** — two new job kinds in
`backend/ads/pipelines/optimize.py`, dispatched by `queue.py` like any job:

- **`style_optimize`** — loads the app's rated assets (latest rating per asset,
  stars + tags + comments + `gen_config`), LLM-proposes a rewritten
  `brand_style`. Result: `{proposed_style, current_style, rationale,
  based_on_ratings}`. Requires **≥5 rated assets**, else the job completes with
  `{insufficient_data: true, needed: 5, have: n}`. It **never auto-applies**.
- **`golden_run`** — runs every golden brief (DB first, file fallback) through
  the short pipeline **draft-quality, in-process, sequentially** (holds the
  video gate like a short), judges each clip with the §3 VLM rubric on sampled
  frames, stores `golden_runs` (summary: `mean_overall`, `per_brief`,
  `delta_vs_previous`) + `golden_results` rows.

The client has an `/optimize` page (ratings overview, style diff + Apply/
Rollback, golden-run table, enqueue buttons).

**API surface** (`backend/ads/routers/optimize.py`, Bearer app auth, strictly
tenant-scoped): `POST /v1/optimize/style` and `POST /v1/optimize/golden-run`
**only enqueue** a job of that kind through the existing queue path (202 with
the job; 409 while one of that kind is already queued/running for the app).
`GET /v1/optimize/overview` returns one page of state: ratings stats (latest
rating per asset, overall + per-ISO-week), recent style versions and golden
runs, the adapter registry, and recent training runs. Applying a style is
always explicit: `POST /v1/style-versions` stores the version AND makes it the
live `App.brand_style` (the hand-written original is snapshotted on the very
first apply); `GET /v1/style-versions` lists history; `POST
/v1/style-versions/{id}/rollback` re-applies an older body as a **new**
`source='rollback'` row. The same loop also runs via the CLI wrappers
(`style_optimizer.py --apply`, `golden_run.py`) and via the automatic
post-training `golden_run` enqueue (§11).

## 10. Adapter registry + per-client image LoRA (phase 2 — P4)

Per-client fine-tuning must never touch another client — enforced **by
construction**: adapters, never base weights.

- **Registry** — `model_adapters` table (app-scoped; `modality='image'`,
  `base_model='flux1-dev-fp8'`, `path` under `data/models/loras/{app_id}/`,
  `comfy_name` relative to ComfyUI's loras dir e.g.
  `clients/{app_id}/{file}.safetensors`, `status`
  ready|training|failed|disabled, `active`, `strength` default 0.8, `metrics`,
  `training_run_id`). Endpoints: `GET /v1/adapters`,
  `POST /v1/adapters/{id}/activate` (deactivates same-modality siblings),
  `POST /v1/adapters/{id}/deactivate`. All tenant-scoped.
- **Serving** — when the requesting app has an **active, `ready`** image
  adapter, the backend image provider adds `{lora_name, lora_strength}` to the
  :8102 `/generate` request; the image service injects a `LoraLoaderModelOnly`
  node between the UNET loader and its consumers (patch-by-class_type, graph
  integrity validated, stacks with the template's Hyper-FLUX speed LoRA).
- **Isolation guarantees** (tested — `test_image_adapter_provider.py`,
  `test_image_service_lora.py`): the adapter is looked up **by the requesting
  app's id** with `active=True AND status='ready'` — app B can never carry app
  A's adapter; deactivated/failed adapters are never sent; with no adapter the
  workflow is untouched. Lookup is per-request, so activate/deactivate applies
  immediately, and any lookup failure degrades to the base model.
- **Degradation** — a 502 with a LoRA attached (unknown/broken file in ComfyUI)
  ⇒ the provider logs and retries once **without** the LoRA. A broken adapter
  never fails a client's job.
- **Install convention** — `backend/scripts/install_adapter.py` (standalone or
  called by the tune executor): copies the file to
  `data/models/loras/{app_id}/`, mirrors it to
  `$COMFY_LORAS_DIR/clients/{app_id}/` (default
  `/home/server/ComfyUI/models/loras`; missing dir ⇒ registered anyway with a
  loud warning — serving degrades to base model), registers the row,
  `--activate` optional. Portability across boxes: [`SCALING.md`](SCALING.md) §4.

## 11. Fine-tuning (phase 2 — P5): explicit, whole-box, UI-driven

Fine-tuning remains the **last step of the ladder** and may take the whole box —
but it is now automated behind an explicit, operator-armed batch flow instead of
folklore. Nothing in the API process ever stops a model; `scripts/tune.sh` is
the only actor that does.

**Requesting a run (client/API):** `POST /v1/training`
`{kind: "flux_lora", image_ids: [≥8 of the app's library product photos],
steps? (default 1000), rank? (default 16)}` → a queued `training_runs` row
(409 if the app already has a queued/active run). `GET /v1/training` (+`/{id}`,
which includes the last **50 log lines** when the log exists);
`DELETE /v1/training/{id}` cancels **queued** runs. **Stranded-run recovery:**
a run left in an executor-owned status (`preparing`/`training`/`installing`)
while the `training_active` gate is **down** means the executor died mid-run —
such a run does not count toward the one-active-run 409, and DELETE fails it
out (`status='failed'`, `error='stranded — executor died'`) instead of leaving
it stuck forever. The client's `/training`
page wraps this: dataset picker, run list with status/progress/log tail,
adapter cards with activate/deactivate.

**The executor — `make tune` (= `scripts/tune.sh`):**

1. Exit 0 quietly when nothing is queued (idle nights cost nothing).
2. Set the `training_active` gate (`backend/scripts/training_gate.py` — the
   flag's **only** writer). While set: `POST /v1/generate/*` and
   `POST /v1/campaigns` return **503**
   `{"detail": "training in progress", "training_run_id": ...}`, the queue
   worker **claims nothing** (the LLM is down too, so even CPU+LLM jobs would
   fail), and `GET /v1/status` reports the flag — the client shows a
   "generation paused" banner.
3. Wait for in-flight generation to drain (poll, default 45 min timeout —
   on timeout: abort, clear the gate, stop nothing).
4. Stop the GPU model services (`models.sh down hard`) but **keep**
   ads-postgres + backend :8070 + client :3100 — the UI shows live progress.
5. For each queued run (`inference/training/run_tune.py`): status transitions
   `queued → preparing → training → installing → evaluating → done|failed`,
   dataset built from the run's library images (captions
   `"<trigger_word>, <label>"`), trainer invoked, adapter installed + activated
   via `install_adapter.py`, everything logged to `logs/tune-<run_id>.log`
   (path stored on the run; progress `{step, total, loss}` written to the DB
   ~every 5 s).
6. Restart the model stack (`spark-run.sh`); a trap guarantees restart + gate
   clear even on unexpected exit (stale gate: `make tune-gate-clear`).
7. Clear the gate and enqueue a **`golden_run` job per app that got a new
   adapter** — the optimization center then shows the before/after comparison.

**Nightly automation (one-time enable, automatic thereafter):**

```bash
sudo cp deploy/ads-tune.service deploy/ads-tune.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ads-tune.timer   # nightly 03:00; no-op when queue empty
```

**Scale-out seam:** `MODEL_HOST=<host>` makes tune.sh skip the local
stop/restart (models live elsewhere — dedicated training node / GB300). See
[`SCALING.md`](SCALING.md) §3/§5.

### The flux_lora trainer — honest current state (2026-08-18)

`inference/training/flux_lora.py` drives **kohya sd-scripts** (`sd3` branch,
`flux_train_network.py`) as a **subprocess in a separate venv**
(`.venv-train`) — the app venv and CI never import torch or any trainer dep;
when any toolchain piece is missing, `prepare()` fails the run fast with the
exact install recipe in the run's `error` field (the same recipe is in the
module docstring).

What is verifiably on this box today:

- `.venv-train` with torch 2.13.0+cu130 (CUDA available on the aarch64 Spark),
  bitsandbytes 0.50.1 (adamw8bit), accelerate/diffusers — built by symlinking
  the app venv's torch stack, per the recipe.
- kohya checkout at `.trainer/sd-scripts` (both gitignored — operator-owned).
- FLUX components split from the **local** ComfyUI combined checkpoint by
  `inference/training/extract_flux_components.py` into `data/models/flux-train/`
  (fp8 unet 11.9 GB, t5xxl-fp8 4.8 GB, clip_l, ae) — **no multi-GB downloads
  during a training run, ever**.
- Training recipe: unet-only LoRA (`--network_train_unet_only` — serving uses
  `LoraLoaderModelOnly`, so text-encoder deltas would be dead weight),
  rank=alpha (default 16), bf16 + `--fp8_base`, adamw8bit, res-512 buckets,
  cached latents + TE outputs, flow-shift 3.1582. `TRAINER_EXTRA_ARGS` appends
  operator overrides.
- The executor path (dataset build → status transitions → progress parsing →
  install → golden enqueue) is covered by tests with a fake trainer
  (`test_tune_executor.py`) — green in CI without any of the above installed.

What is **not** yet proven: no end-to-end trained adapter exists on disk at the
time of writing — `data/models/loras/` and ComfyUI's `loras/clients/` are still
empty and no `logs/tune-*.log` from a real run remains. Treat the first real
`make tune` (or a manual smoke run: queue a run with `steps ≤ 20`, 4–8 images,
then `make tune`) as the proving run for the kohya command on this exact
box, and check `logs/tune-<run_id>.log` if it fails.

## 12. Ladder status after phase 2

The full client-adaptation ladder is now: **onboard (§8) → generate → rate (§6)
→ optimize style / verify on golden briefs (§9) → fine-tune a per-client LoRA
(§11) → golden-run comparison (§9)** — with fine-tuning still last, still
explicit, still whole-box on the Spark, and per-client by construction (§10).
Wan (motion) LoRA and DPO-style preference tuning on the collected ratings
remain future work; the `training_runs.kind` column and the pluggable
`inference/training/` registry are the seams they will land in.

## 13. Ukrainian voice: what is tunable on RAD-TTS++ (2026-08-23)

uk standardised on the `radtts` engine and the UI offers no other (backend
`providers/voices.py` `UI_ENGINES`). **Voice cloning is hidden** with it
(`packages/ui/lib/features.ts` `VOICE_CLONING_ENABLED = false`): RAD-TTS has fixed
speaker embeddings and cannot clone, and a flow that accepts a client's voice
sample, renders an "approve your clone" gate, then ships the stock speaker
anyway is worse than no flow. The pipeline, the queue job and the consent gate
in `short.py` are untouched — a project that needs cloning pins
`styletts2_multi` (or `f5`) and the flag comes back on.

Cloning is not the only way to shape a voice, and RAD-TTS++ is unusually
controllable — the "++" is attribute prediction. `RADTTS.infer()` already
accepts every knob below; `RadTTSEngine.synthesize()` currently pins them to
the reference defaults, so exposing one is a matter of threading a value, not
new modelling.

**Levers, cheapest first:**

1. **наголос marks** — shipped. `+` before the stressed vowel beats the
   dictionary, per word, and the lab shows a stress map of where it landed.
   The single biggest lever on perceived correctness.
2. **Pace** — `token_dur_scaling` (already wired to the lab's `speed`).
   Per-project pacing is one saved float away.
3. **Pitch** — `f0_mean` / `f0_std`: shift the voice's centre pitch and how
   far it ranges. A deeper, flatter read for a premium brand; a higher, wider
   one for something energetic. This is the control styletts2 never had.
4. **Emphasis** — `energy_mean` / `energy_std`: loudness centre and spread,
   i.e. how punchy the delivery is.
5. **Variability** — `sigma` (decoder), `sigma_dur`, `sigma_f0`,
   `sigma_energy`: how much each attribute is sampled rather than predicted.
   Lower = flatter and more repeatable, higher = more expressive and less
   predictable.
6. **Best-of-N takes** — the HF space's `n_takes`. Render N and auto-pick with
   the ASR round-trip we already run (`/transcribe` -> WER), exactly the
   reject-sampling pattern §6 uses for keyframes. Buys quality for GPU time
   with no new UI.
7. **Per-token override** — `dur`, `f0`, `energy_avg` accept full contours, so
   a specific word can be lengthened or lifted. The escape hatch for a brand
   name that never sounds right.
8. **Fine-tune** — the real "make it sound like THIS person" answer now that
   zero-shot cloning is off the table: train RAD-TTS (or styletts2/f5) on
   ~30-60 min of one speaker. Slots into the §11 ladder as a new
   `training_runs.kind`; open uk data exists (egorsmkv/ukrainian-tts-datasets).

Suggested first step when this comes up: a per-project **voice profile**
(pace, pitch, emphasis, variability) saved next to `tts_voice`, tuned in the
TTS lab against the existing thumbs/WER feedback — items 2-5 share one form
and one migration.
