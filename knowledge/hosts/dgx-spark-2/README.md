# dgx-spark-2 — second DGX Spark (GB10, ~122 GB unified): the image + video executor

**Status: built, warm (2026-10-07).** This file is the setup runbook *and*, once done, the place the
measured numbers live (CLAUDE.md: numbers live next to the hardware). Whoever sets the
box up — the owner or an agent running on it — follows the steps in order and replaces
every `TBD` with a measurement.

Tailnet: `dgx-spark-2` = **100.64.0.12**. The primary Spark (`dgx-spark`) = **100.64.0.1**.

## Why this box exists

Every quality/latency compromise on the primary Spark is a memory compromise, not a
model compromise (numbers: `../dgx-spark/README.md`):

| Today on dgx-spark | Cost |
|---|---|
| image, edit, video, avatar share one ComfyUI and evict each other | Qwen-Image 9 s warm → ~160 s cold; LTX 105 s warm → ~200 s cold |
| LTX text encoder (Gemma-4-12B int8) pinned to the **CPU** | 55–65 s per new video prompt (GPU placement hit 118 GB → earlyoom) |
| only Qwen-Image-Edit-2511 loaded since 2026-10-03 | text-to-image does not get the 2512 quality rung |
| avatar (InfiniteTalk) peaks ~104 GB | nothing else generates during a ~22 min clip |

**The split:** this box takes **image + video** and keeps them **resident**. The primary
Spark keeps the LLM, voices, embeddings, translation gate, avatar, control plane, web
apps and Postgres. Nothing here is a "shared tier" — other tailnet projects do not
depend on this box, so it is ours to restart at will.

## Memory budget (estimate — step 3 replaces it with measurements)

| Component | ~GB | Source |
|---|---|---|
| OS + docker + adapters (no API/web/Postgres/vLLM here) | 12 | estimate |
| Qwen-Image encoder (Qwen2.5-VL-7B fp8) + VAE | 9 | shared by 2512 and Edit-2511 |
| Qwen-Image-2512 fp8 Lightning transformer | 20 | docs/MODELS.md |
| Qwen-Image-Edit-2511 fp8 Lightning transformer | 20 | docs/MODELS.md |
| LTX-2.5 22B int8 transformer + VAEs + upsampler | 25 | weights.lock |
| Gemma-4-12B int8 LTX encoder **on GPU** (phase 2 only) | 13 | weights.lock |
| sampling activations, one render at a time | 15–20 | dgx-spark peaks |
| **Phase 1 (encoder on CPU)** | **~100–105** | |
| **Phase 2 (encoder on GPU)** | **~113–118** | at the earlyoom line — must be measured |

Phase 1 is the safe target. Phase 2 only ships if step 3 shows ≥ 8 GB headroom at the
peak of a 704×1280×121 LTX render with both image transformers resident; otherwise
drop the 2512/Edit co-residency (one image transformer at a time) to buy it.

Keep in mind: resident ≠ parallel. Diffusion is compute-bound on one GB10; an image
and a video sampling at the same moment each run slower. Residency removes load time,
it does not add GPUs.

## What this box serves

| Kind | Port | Engine | Models | Default |
|---|---|---|---|---|
| comfyui (image) | 8188 | ComfyUI v0.33.3 in docker `ads-comfyui` | — | — |
| comfyui (video) | 8189 | ComfyUI v0.33.3 in docker `ads-comfyui-vid` (same image) | — | — |
| image | 8102 | `inference/adapters/gen-image` → :8188 | `qwen-image-2512`, kept warm (Edit-2511 lives on dgx-spark since 2026-10-07) | `qwen-image-2512` |
| video | 8104 | `inference/adapters/gen-video` → :8189 | `ltx2`, **kept warm** | `ltx2` |

**Everything stays warm (owner's rule, 2026-10-06):** one ComfyUI per role (a ComfyUI only
evicts its own models), `KEEP_WARM=vram` + `KEEP_WARM_GAUGE=process` (re-warm within
2 min if a process's own model memory drops), and the `ads-dropcache` root timer (page
cache must not pose as used GPU memory).

Not here, on purpose: LLM, TTS, embeddings, gate, avatar (stay on dgx-spark), FLUX.1-dev
(**NC licence** — prototype only, docs/MODELS.md), Wan 2.2 (no weights in the lock; a
later phase if the owner wants a second video model).

The image router sends each model to the host whose `default` it is, so with this
box defaulting to 2512 and dgx-spark defaulting to Edit-2511, text-to-image lands here
and product-photo edits keep working on either.

## How it is driven (no worker on this box)

Same pattern as `rtx3090x3` with `MODELS_ONLY=1`: this box runs **model services
only**. A second `ads-worker` process **on dgx-spark** (`workers/dgx-spark-2.env`)
claims jobs and dials this box's image/video, while LLM and voices stay local to it.
Consequences:

* no queue-DB password or worker code on this box; pipeline changes never need a redeploy here;
* the executor is registered on dgx-spark's Admin → Executors as `dgx-spark-2`;
* `up.sh` on dgx-spark starts the worker automatically (it starts one per `workers/*.env`).

---

## Status log

**2026-10-06 — installed, conformance PASS, but image ↔ video evicted each other.**
From dgx-spark: `python -m inference.conformance --host dgx-spark-2 --at 100.64.0.12` → PASS
(image 2512 + Edit-2511 advertised, video ltx2). Timings showed the eviction:

| Render (256² image / 9-frame ltx2) | Wall |
|---|---|
| image, first | 206–216 s |
| image, same model again | **1.0 s** |
| video right after an image | 152–154 s |
| image right after a video | 175 s |

Cause: ComfyUI `/system_stats` showed `vram_free` 20 GB while `ram_free` (MemAvailable)
was 85 GB — ~65 GB of page cache from the weight copy counted as used, so one ComfyUI
unloaded Qwen to load LTX and back. Fix (this commit): two ComfyUIs, process-gauge
keep-warm for every model, `ads-dropcache` timer.

**Apply the fix on this box** (one time, after `git pull`):

```bash
cd ~/Documents/dev/advertisment_system && git pull
# the image ComfyUI must be named ads-comfyui on :8188 — if yours has another name:
#   docker rename <name> ads-comfyui
bash inference/hosts/dgx-spark-2/install.sh     # adds ads-comfyui-vid :8189, the dropcache timer, re-installs units
docker restart ads-comfyui                      # drops the LTX copy the image ComfyUI still holds
systemctl --user restart gen-image gen-video    # warm-up: 2512 + Edit on :8188, LTX on :8189
```

Then check (from here or from dgx-spark):

```bash
for p in 8188 8189; do curl -s localhost:$p/system_stats | python3 -c \
  "import json,sys; d=json.load(sys.stdin)['devices'][0]; print($p, 'torch GB', round(d['torch_vram_total']/2**30,1), 'free GB', round(d['vram_free']/2**30,1))"; done
free -g; systemctl list-timers ads-dropcache.timer
```

Expected: :8188 torch ≈ 45–50 GB (both image rungs + encoder), :8189 ≈ 22–28 GB, `free`
"available" ≥ 25 GB. If :8188 sits below 40 GB after the warm-up, set `KEEP_WARM_MIN_GB`
in `units/gen-image.service` to what it really holds minus 5, or the keep-warm loop will
re-render every 2 minutes. Same for :8189 and `18`. Re-run conformance from dgx-spark
three times — every image and video render after the first must be warm (seconds, not
minutes) — and record the numbers in the step 3 table.

**2026-10-07 — warm side by side; conformance PASS ×3.** Two more causes found on the box:
the root dropcache timer was never installed (no passwordless sudo → now a user timer,
`dd iflag=nocache` on the weight files), and ComfyUI's default RAM-pressure cache evicts
every *inactive* node result (threshold = 100% of RAM), so 2512 and Edit dropped each other
and keep-warm re-rendered ~350 s every 2 min. Fix: `--cache-lru 64` on both ComfyUIs.

| Measure | Value |
|---|---|
| :8188 torch (2512 + Edit + encoder) | 47.1 GB |
| :8189 torch (LTX-2.5) | 23–24 GB |
| `free` available, both warm | 40–42 GB |
| cold warm-up image / video | 359 s / 195 s |
| conformance image 256², rounds 1–3 | 1.0 / 1.0 / 1.0 s |
| conformance ltx2 9 frames, rounds 1–3 | 60.2 (new prompt, encoder on CPU) / 6.0 / 6.0 s |

**2026-10-07 — phase 2 (LTX encoder on GPU) measured: does NOT fit next to both image rungs.**
`LTX_TEXT_ENCODER_DEVICE=default` (gen-video knob, a24ec1e) tried via a unit drop-in, then
reverted. 704×1280×121, new prompt each run, 2512 + Edit resident on :8188:

| Encoder | Wall | Peak used (MemTotal − MemAvailable) | Idle after |
|---|---|---|---|
| CPU (shipped) | 127 s | 87 GB | ~70 GB used |
| GPU, first run (loads encoder) | 199 s | 115 GB | — |
| GPU, steady | **78 s** | **117 GB** (1 GB under earlyoom) | 111 GB used, 10 GB available |

The GPU copy holds ~14.6 GB fp16 in :8189 (23 → 38 GB) and `/free` did not release it
(container restart did). Faster (−49 s per new-prompt video, 1.6×) but fails the ≥ 8 GB
rule; an image render during a video would get ComfyUI killed. To ship it, drop the
2512/Edit co-residency (one image rung warm, ~20 GB back) — owner's call.

**2026-10-07 (later) — SHIPPED: LTX encoder on the GPU; Edit-2511 moved to dgx-spark.**
dgx-spark keeps Edit-2511 warm (SPARK_KEEPS=edit, avatar adapter off in render mode); this box
keeps 2512 + LTX + Gemma encoder. Measured, both warm, 704×1280×121 new prompt:

| Measure | Value |
|---|---|
| :8188 torch (2512 + Qwen encoder) | 27.6 GB |
| :8189 torch (LTX + Gemma encoder on GPU) | 37.7 GB |
| idle used / available | 89 / 31 GB |
| LTX wall, new prompt | **72 s** (was 127 s on CPU encoder) |
| peak used during the render | 96 GB — **22 GB** under earlyoom |
| dgx-spark: Edit-2511 resident in pd-comfyui | 27.7 GB, 42 GB available |

## Setup runbook

Run as the box's normal linux user. Every step is idempotent — re-run it if interrupted.

Files in this folder: `host.yaml` (what runs where) · `install.sh` (steps 1–5 of the
install) · `up.sh` / `down.sh` · `comfyui.Dockerfile` · `torchaudio_stub.py` ·
`units/` (adapter user units + the `ads-dropcache` root timer) · `dropcache.sh`. The worker side lives on dgx-spark:
`workers/dgx-spark-2.env.example`.

### 0. Record the box (fill in before touching anything)

```bash
hostname; uname -m; free -g; df -h /; nvidia-smi; docker --version
docker info 2>/dev/null | grep -i runtime; tailscale ip -4; systemctl is-active earlyoom
```

| Fact | Value |
|---|---|
| user / home | TBD |
| DGX OS / driver / CUDA | TBD |
| MemTotal | TBD |
| free disk on / | TBD (need ≥ 150 GB: ~90 GB weights + 20 GB image + outputs) |
| nvidia container runtime | TBD |

Needed: docker with the nvidia runtime (DGX OS ships it), git, python3.12 + venv,
ffmpeg, curl, `hf` CLI. Install `earlyoom` if absent (`sudo apt install earlyoom`) —
the same safety net dgx-spark has; it SIGTERMs ComfyUI instead of letting the box freeze.

**Do not apply `files/sysctl-spark3.conf` or any other sysctl tuning from other repos**
on this box (it cost dgx-spark ~14 GiB of visible memory once).

### 1. Clone, log in to Hugging Face

```bash
git clone git@github.com:vlmoon99/advertisment_system.git ~/Documents/dev/advertisment_system
hf auth login                         # same account as dgx-spark
loginctl enable-linger "$USER"        # user units survive logout/reboot (sudo if refused)
```

Accept the **Lightricks/LTX-2.5** licence on the HF website for that account first
(LTX-2.5 Community — free below $10M revenue, docs/MODELS.md). The other repos are open.

### 2. Install (one command, idempotent)

```bash
bash ~/Documents/dev/advertisment_system/inference/hosts/dgx-spark-2/install.sh
```

What it does, and the reason behind each pin:

1. **Repo venv** — adapters + conformance.
2. **ComfyUI v0.33.3** at `~/ComfyUI` + ComfyUI-GGUF `6ea2651` + KJNodes `d3cfe21` — dgx-spark's
   exact pins. Copies `torchaudio_stub.py` to `~/ComfyUI/torchaudio/__init__.py`: NGC 25.09 has
   no ABI-matched torchaudio and ComfyUI imports it at startup (`pip install torchaudio` →
   `undefined symbol`).
3. **Weights** — the qwen / ltx-2.5 / gemma4 rows of `inference/workflows/weights.lock`, sha256
   checked (~90 GB). FLUX rows skipped (NC licence), avatar rows skipped (avatar stays on dgx-spark).
   Faster on the tailnet: rsync the same paths from `100.64.0.1:~/ComfyUI/models/` first; the
   script then only verifies. Never resume a big download with `curl -C -` (it once appended past
   the end of a safetensors) — on a sha failure delete the file and re-run.
4. **Two ComfyUI containers** `ads-comfyui` (:8188, image) and `ads-comfyui-vid` (:8189, video) from `comfyui.Dockerfile` (NGC `pytorch:25.09`, the only
   sane sm_121 aarch64 torch; pip is kept off torch/torchvision). Runs with
   `--disable-async-offload --disable-dynamic-vram` — **required** on GB10, the sampler crashes
   with `'NoneType' has no attribute 'wait_stream'` without them.
5. **Adapters** `gen-image` :8102 and `gen-video` :8104 as `systemctl --user` units from `units/`.
   `COMFY_OUTPUT`/`COMFY_INPUT` are set explicitly (the adapters default to `/home/server/...`).
   No `VLLM_SLEEP_URL` (no vLLM here). `KEEP_WARM=vram` with `KEEP_WARM_GAUGE=process`: it reads
   each ComfyUI's own torch memory (the device reading is meaningless on unified memory) and
   re-warms within 2 min when a model drops; `KEEP_WARM_MODELS` warms both image rungs.
6. **`ads-dropcache` user timer** (no sudo; `dd iflag=nocache` on the weight files): drops their clean page cache when MemAvailable − MemFree
   exceeds 6 GB, so cached weight files never make ComfyUI evict a resident model.

Check:

```bash
curl -s localhost:8188/system_stats | head -c 300      # the GB10
curl -s localhost:8102/health | python3 -m json.tool   # kind image, default qwen-image-2512
curl -s localhost:8104/health | python3 -m json.tool   # kind video, models [ltx2]
```

`up.sh` / `down.sh [--all]` start and stop the services afterwards.

### 3. Conformance + measurements

From dgx-spark (it has the repo venv):

```bash
cd ~/Documents/dev/advertisment_system
.venv/bin/python -m inference.conformance --host dgx-spark-2 --at 100.64.0.12
make host-validate
```

Then measure on this box with `free -g` sampled every second (`MemTotal − MemAvailable`;
`nvidia-smi` memory is "Not Supported" on GB10) and fill the table:

| Scenario | Peak GB | Wall |
|---|---|---|
| idle, adapters up, nothing loaded | TBD | — |
| Qwen-Image-2512 1024², cold / warm | TBD | TBD / TBD |
| Qwen-Image-Edit-2511 ~1184×880 right after a 2512 render (both resident?) | TBD | TBD |
| LTX-2.5 704×1280×121 cold / warm, Gemma on CPU | TBD | TBD / TBD |
| LTX warm **while** 2512 + Edit are resident (the phase-1 target) | TBD | TBD |
| image render during an LTX render (compute contention) | TBD | TBD |
| phase 2: Gemma encoder on GPU, same LTX render | TBD | TBD |

**Phase 2 (encoder on GPU):** the LTX workflows pin `CLIPLoader device=cpu`
(`inference/workflows/ltx25_*_api.json`). Do not edit the JSON — dgx-spark uses the
same files and must keep CPU. Add an env knob to `gen-video` (e.g.
`LTX_TEXT_ENCODER_DEVICE`, default `cpu`) that rewrites that one input at request time,
set it to `default` only in this box's unit, and ship it only if the peak row above
leaves ≥ 8 GB headroom.

### 4. Register as an executor (on dgx-spark) — prepared 2026-10-06

Done on dgx-spark: executor **`dgx-spark-2`** registered (token prefix `adx_YyGlw9nC`); its
worker env is parked as the gitignored `workers/dgx-spark-2.env.off` until cutover (so a
reboot or the nightly tune can't start it early). It dials **only** this box for
image/video, and says `SPARK_KEEPS=avatar`.

Cutover runs on dgx-spark through its render mode: `../dgx-spark/README.md` § Two-Spark
layout. The handoff for this box is `CONNECT.md`.

### 5. Close out

* Fill the measurement table and step 0, update `host.yaml` (`mem_gb` per service, notes) and flip
  `status: built`.
* Commit in this repo, normal message format (what changed and why), straight to `main`.

---

## Phase 3 — what dgx-spark does with the freed memory (owner decision)

Once shorts and photos run here reliably, dgx-spark can stop holding the LTX transformer
and the 2512 rung (~45–50 GB back). Candidates, pick one, measure, then record it in
`../dgx-spark/README.md` and `docs/MODELS.md`:

1. **Avatar always warm** on dgx-spark (next to the voice it lip-syncs; no longer blocks image/video).
2. **A stronger ads LLM** (better copy, critics, uk long-form) — touches `spark-llm :8010`,
   which the students also use, so it is the owner's call.
3. **vLLM KV back to 0.35** for more parallel campaign LLM traffic.

Until the owner picks, dgx-spark keeps its current services as the fallback: if this box
is down, its worker still renders everything exactly as today.

## Quirks (fill in as found)

* `earlyoom` SIGTERMs ComfyUI above ~118 GB used — the same ceiling as dgx-spark.
* `cudaMemGetInfo` tracks MemFree, not MemAvailable: page cache after a big safetensors
  read looks like used memory to ComfyUI. `sync; echo 1 | sudo tee /proc/sys/vm/drop_caches`
  before measuring a cold peak.
