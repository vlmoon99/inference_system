# rtx3090x3 — 3× RTX 3090 (Ampere, 24 GB each), the weekend worker box

A friend's machine used as a **model-only inference host**: ComfyUI ×2 + vLLM +
the three contract adapters, nothing else. A worker **on the Spark**
(`workers/rtx3090x3.env`) dials these services over the tailnet, so pipelines,
voices and every future change live on the Spark and nothing is ever re-uploaded
here. (The box can also run its own `ads-worker` — `install.sh` without
`MODELS_ONLY=1` — but then it needs the code each time it changes.)
Status: **planned** — on the Spark's exact weights since 2026-09-25 (commit 56e2212):
lock hashes verified, conformance PASS over the tailnet; `parity.py` from the Spark still to run.

The box as it is set up (2026-09-25):

| | |
|---|---|
| Repo | `/home/server/Documents/dev/advertisment_system` — a git clone with a read-only fine-grained token (see LICENSE) |
| `ADS_HOME` | `/home/server/ads` (ComfyUI in `/home/server/ads/ComfyUI`) |
| Services | systemd **--user** units, linger on (no passwordless sudo): `comfy-img comfy-vid gen-image gen-video` (+ `comfy-vid2 gen-video2` on GPU2, see *Using the whole box*) |
| Not running here | vLLM :8010 (only in offload mode, `gpu2.sh llm`), TTS :8105, a local `ads-worker` — the Spark serves the LLM and voices |
| SSH | from the Spark: `ssh rtx3090x3` (key `~/.ssh/rtx3090x3`, alias in the Spark's `~/.ssh/config`) |

Fresh box:
```
git clone https://github.com/vlmoon99/advertisment_system.git ~/Documents/dev/advertisment_system   # token as password
cd ~/Documents/dev/advertisment_system
ADS_HOME=~/ads ADS_USER_UNITS=1 MODELS_ONLY=1 bash inference/hosts/rtx3090x3/install.sh   # engines, weights, adapter units
# then on the SPARK: cp workers/rtx3090x3.env.example workers/rtx3090x3.env, paste a token, make run
```
`ADS_USER_UNITS=1` installs the units under `~/.config/systemd/user` (no sudo; an admin runs
`loginctl enable-linger <user>` once). Without it they go to `/etc/systemd/system` via sudo.
`up.sh`/`down.sh` detect which kind is installed.

Files: `host.yaml`, `install.sh` (idempotent, 7 steps), `units/*.service`
(templated, `@USER@ @ADS@ @REPO@ @CF@` substituted by install.sh),
`parity.py` (same seed on the Spark and here → same picture?), `bench.py`,
`gpu2.sh` (GPU2 = second video renderer | fallback LLM), `worker.env.example`.

## Translator bake-off on this box

`bakeoff.sh up hymt2|hymt2-7b|lapa` runs one candidate as a throwaway vLLM
container on :8015 (borrowing GPU0+GPU2; renderers stopped, `bakeoff.sh down`
restores them). The harness, the MetricX gate and the judge stay on the Spark:
`backend/scripts/translate_bakeoff.py --candidate <name>=http://100.64.0.10:8015/v1|<model>`.
Results land in `backend/data/bakeoff/<run>/report.md`; the decision goes to
docs/AGENTIC_PLAN.md §2.5. The Spark's worker never dials :8015.

## Updating the adapters on this box

Only `inference/adapters/*` runs here, so a code change reaches this box exactly
when the contract changes (a new request field, a new capability). The Spark pushes
to `main`; on the box:

```
cd ~/Documents/dev/advertisment_system
git status                                            # clean? (stash anything local first)
git pull --ff-only origin main
git log --oneline -1                                  # the commit you now run
systemctl --user restart gen-video gen-video2 gen-image   # the adapter units (comfy-* only if ComfyUI/workflows changed)
curl -s localhost:8104/health                         # capabilities back? video: ["ltx2"]
```
A pull that touches `install.sh`, `units/` or `inference/workflows/weights.lock` also needs
`ADS_HOME=~/ads ADS_USER_UNITS=1 MODELS_ONLY=1 bash inference/hosts/rtx3090x3/install.sh`
(idempotent), then `systemctl --user daemon-reload` and a restart of `comfy-vid` too.
No git on a box? `scripts/pack_release.sh` on the Spark makes a zip of HEAD; unzip it over the repo.

Pipelines, voices and prompts never need this: they run on the Spark's worker.
Last contract change that required a pull: `ref_image_b64` on video `/generate`
(2026-09-21) — without it a short built on a library or campaign photo fails
here with 404 "ref image not found", because that photo is on the Spark's disk.

## Same models as the Spark (since 2026-09-25)

Rule: **a request renders the same way here as on the Spark.** Same weight files
(`inference/workflows/weights.lock`: sha256 + Hugging Face source, hash-checked by
`install.sh` step 3), same workflows (`inference/workflows/*.json`, used unpatched),
same ComfyUI (v0.33.3 + comfy-kitchen), same adapters, same advertised models.
Only *where* things sit in memory differs:

| | Spark (GB10, 122 GB unified) | This box (3× 24 GB, discrete) |
|---|---|---|
| Image | FLUX.1-dev fp8 + Hyper 8-step LoRA | **same files**; fp8 dequantises per matmul on Ampere (slower, same weights) |
| Video | LTX-2.5 22B distilled int8-convrot, Gemma-4 12B int8 encoder on CPU | **same files**; comfy-kitchen's int8 kernels run on sm80+; the 21.5 GB transformer does not fit next to activations in 24 GB, so ComfyUI streams part of it from RAM each step (slower, same weights); Gemma on **CPU**, as on the Spark |
| Video models advertised | `ltx2` (no Wan weights on the Spark) | `ltx2` — pinned with `VIDEO_MODELS=ltx2` so a model choice never depends on which box claimed the job |
| LLM (prompts, scripts, judges) | Qwen3.6-35B-A3B **NVFP4** | the Spark's worker for this box pools the **Spark's LLM first** (`workers/rtx3090x3.env`); the local AWQ int4 answers only while the Spark's is stopped (offload mode). `LLM_WEIGHTS=nvfp4` runs the Spark's own checkpoint here via Marlin — 23.4 GB on a 24 GB card, untested |
| TTS | kokoro en, RadTTS uk, F5 ru | the Spark's (`GEN_TTS_URL=localhost:8105` in the Spark-side worker); local kokoro/piper unused |
| Processes | one ComfyUI | one ComfyUI **per GPU** (image :8188 GPU0, video :8189 GPU1) |

Until 2026-09-25 this box ran a GGUF **Q4_K_M** LTX transformer and a Q4 Gemma
encoder (both 4-bit, vs the Spark's 8-bit) and the worker used the local AWQ LLM for
every prompt — visibly worse video and different images. `install.sh` deletes those
GGUF files now.

### Moving an existing box to the Spark's weights

```
cd ~/Documents/dev/advertisment_system && git pull --ff-only origin main
ADS_HOME=~/ads ADS_USER_UNITS=1 MODELS_ONLY=1 bash inference/hosts/rtx3090x3/install.sh   # ~37 GB (int8 LTX + Gemma), checks hashes
systemctl --user daemon-reload && systemctl --user restart comfy-vid gen-video gen-image
# on the Spark:
.venv/bin/python inference/hosts/rtx3090x3/parity.py --at 100.64.0.10 --video
```
Done on 2026-09-25 by hand instead of install.sh (same result): all 7 lock entries pass
`sha256sum -c`, GGUF files and `wf-3090` removed, ComfyUI v0.33.3 + `comfy_kitchen` imports,
`VIDEO_MODELS=ltx2` in the gen-video unit.
`parity.py` must print `static check: PASS`; the image/video pairs it saves under
`logs/parity/` should show the same picture (pixel-exact is impossible across GPU
architectures, the composition/subject/colours are not).

### GPU layout

| GPU | Process | Port | VRAM plan |
|---|---|---|---|
| 0 | ComfyUI image (FLUX fp8 + Hyper LoRA) | 8188 | UNet ~12 GB resident; T5-XXL fp8 offloaded to RAM between prompts |
| 1 | ComfyUI video (LTX int8 + VAEs + upscaler) | 8189 | 21.5 GB transformer, partly streamed from RAM + VAE/upscaler ~2.5 GB + activations (tiled decode) |
| 2 | **role `video`** (default): ComfyUI video #2, same files as GPU1 → gen-video2 | 8190 → **8114** | as GPU1 |
| 2 | role `llm` (offload mode only): vLLM AWQ fallback LLM (docker `ads-llm`) | 8010 | ~20 GB weights + KV; `--gpu-memory-utilization 0.92 --max-model-len 16384` |
| CPU | Gemma text encode for LTX (one per video ComfyUI) | — | ~16 GB RAM for the int8 encoder + the streamed part of the transformer, per renderer |

### Using the whole box (2026-09-25)

Quality first: every change below keeps the same weights, the same workflows and the
Spark's LLM. It only removes idle time.

* **Why the box idled.** A worker holds its video gate for a short's whole pipeline
  (script, voice, keyframe, render, mux), but GPU1 renders for only about half of it
  (19 Sep run: short median 218 s against ~72–116 s of render). Its 3 slots also filled
  with posts that were waiting on the LLM, and GPU2 did nothing (vLLM stopped).
* **GPU2 renders video.** `gpu2.sh video` (the default `GPU2_ROLE`) runs a second LTX
  ComfyUI (:8190) behind `gen-video2` (:8114). The Spark's second worker
  `workers/rtx3090x3-gpu2.env` (its own executor token) sends its shorts there. Check it
  first: `parity.py --ref-video 100.64.0.10:8104 --video-port 8114 --video` compares
  GPU2 against GPU1 (same hardware, same kernels).
* **Two shorts per worker.** `VIDEO_GEN_CONCURRENCY=2` in both worker envs lets one short
  prepare while the other renders. ComfyUI queues the renders, so output is unchanged.
  `QUEUE_CONCURRENCY` is 5 on the main worker, so posts keep GPU0 busy in between.
* **Offload mode needs the LLM back on GPU2.** On the box run `gpu2.sh llm` before
  `make offload` on the Spark (preflight checks the 3090's :8010 and says so), and
  `gpu2.sh video` after `make onload`.
* **Watch RAM.** Each LTX renderer streams part of its 21.5 GB transformer from RAM and
  keeps a Gemma encoder on the CPU. One render peaked at 62 of 125 GB, so two at once is
  tight. If `free -g` shows swap climbing during `bench.py --video-ports 8104,8114`,
  run `gpu2.sh llm` (or stop comfy-vid2) and record it here.
  Measured 2026-09-25: two renders at once peaked at 91 GB RAM with 0.7 GB swap (the box
  has 19 GB swap, below the 32 GB in Prerequisites; add more before adding load).
* **GPU2 = GPU1, measured.** `parity.py --ref-video 100.64.0.10:8104 --video-port 8114 --video`
  gave 45.8–46.1 dB, the same clip. So on identical hardware LTX is reproducible; the
  ~10 dB Spark-vs-box video gap comes from GB10-vs-Ampere kernels under the ancestral
  sampler, not from the setup (the ComfyUI graphs on both are identical).
* **One database per ComfyUI.** Each video ComfyUI gets `--database-url …/comfyui-vid{,2}.db`;
  sharing comfy-img's `comfyui.db` fails with "Failed to initialize database".

## Prerequisites

* NVIDIA driver ≥ 570 (CUDA 12.8 wheels), docker + nvidia-container-toolkit,
  python3.12, git, ffmpeg, curl, `hf` logged in with the **LTX-2.5 licence accepted**.
* System RAM 64 GB minimum (128 comfortable); add ≥ 32 GB swap.
* ~80 GB of weights (lock + LLM) → 200 GB free disk.
* Tailscale joined to the Headscale tailnet; `tailscale ip -4` is what other
  machines use in `--at` for conformance and what you would put in a pool.

## Contract identity

Every adapter here starts with `HOST_NAME=rtx3090x3 DEVICE=cuda` (from
`model-host.env`) and advertises what it serves: image `flux1-dev-fp8`, video
`ltx2` (+`5b` when installed), tts `en uk ru` via piper. The worker therefore
claims everything including shorts and campaigns; a `14b` request never reaches
this box because the short pipeline reads the advertised list first.

## Ampere gotchas

* `--kv-cache-dtype fp8` on vLLM is storage-only on Ampere and works; drop it if
  the attention backend complains. Do not copy the Spark's
  `--attention-backend flashinfer --moe-backend marlin --load-format fastsafetensors`.
* Do **not** pass ComfyUI `--disable-async-offload --disable-dynamic-vram` (Spark
  unified-memory workarounds); on discrete cards default offloading is what makes
  things fit.
* If comfy-vid runs out of VRAM on the int8 transformer, add `--reserve-vram 1.5`
  (or `--lowvram`) to its ExecStart — never go back to a quantised substitute; that
  is exactly what made this box render worse. The log line `loaded partially` is
  expected here.
* `import comfy_kitchen` failing (install.sh step 2 stops) means the int8 files cannot
  load: fix the wheel against the installed torch, do not swap the weights.
* AWQ checkpoint failing to load → text-only fallback
  `cyankiwi/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit`; the VLM judges then need
  `KEYFRAME_CANDIDATES=1 CAMPAIGN_KEYFRAME_CANDIDATES=1 VIDEO_QA=0` in `worker.env`.

## Measured (2026-09-19, from the Spark over the tailnet, `status: built`)

Conformance: `python -m inference.conformance --host rtx3090x3 --at 100.64.0.10` → PASS, 0 warnings.
Wall-clock as seen by the worker (HTTP round-trip, files excluded). Repeating a request with the same
seed and prompt is a ComfyUI cache hit (~1 s), so warm numbers below use fresh seeds.

Rows marked *GGUF* were measured on the retired Q4 setup; re-measure with `bench.py`.
2026-09-25, Spark weights (int8 LTX, commit 56e2212): conformance PASS over the tailnet; `/health`
image `["flux1-dev-fp8"]`, video `["ltx2"]` + audio, i2v, max_frames 121; `ref_image_b64` i2v works.

| Unit | Command | Measured | Community expectation |
|---|---|---|---|
| FLUX 1024² 8 steps warm | `curl :8102/generate` | **11.1 s** (2 runs, identical) | ~4 s — not reached: fp8 weights dequantise per matmul on Ampere; same file as the Spark by rule — speed is not a reason to swap it |
| LTX **int8** i2v 704×1280×121 cold (2026-09-25) | `:8104/generate model=ltx2` + ref image | **205 s** (first render after restart; transformer partly streamed from RAM) | — |
| LTX **int8** t2v 704×1280×81, GPU1 :8104 and GPU2 :8114 (2026-09-25) | `bench.py --video-ports 8104,8114` | **74.2 s** each, warm | — |
| **both renderers at once** (2026-09-25) | same, last row | **74.2 s wall for 2 clips**; peak RAM 91 / 125 GB, swap 0.7 GB, VRAM 21.1 GB per card | — |
| LTX 704×1280×81 first render *(GGUF)* | `curl :8104/generate model=ltx2` | **114 s** (model resident from conformance, first at this size) | ~30 s render + CPU encode |
| LTX 704×1280×81 warm *(GGUF)* | same, new seed | **72 s** (2 runs, identical) | |
| vLLM Qwen3.6-35B-A3B (AWQ) | 8 concurrent × 512 completion tokens | **809 tok/s aggregate**, 5.0 s wall | — |
| Peak VRAM per card | `nvidia-smi` during a campaign | **not measured** (no SSH from the Spark yet) | must stay < 23 GB |

Reproduce: `.venv/bin/python inference/hosts/rtx3090x3/bench.py --at 100.64.0.10` (prints the same rows;
the vLLM row is skipped while :8010 is not running).
`docs/` never holds these numbers.
