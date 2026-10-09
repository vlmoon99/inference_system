# dgx-spark — DGX Spark (GB10, 122 GB unified memory)

The primary host. Runs the shared model tier, the three contract adapters, the
control plane (`ads-api`), this box's `ads-worker`, and both web apps.

```
make run          # = up.sh: shared tier (start if down) + adapters + api + worker + web
make stop         # = down.sh: app + adapters; shared tier stays for other projects
make host-down HOST=dgx-spark ARGS=--all     # everything, frees the box
make models-status
make host-check HOST=dgx-spark               # conformance against localhost ports
```

Files: `host.yaml` (what runs where), `services.sh` (model services), `up.sh` /
`down.sh`, `compose.shared.yaml` (reinstall recipe for the shared containers),
`embed.sh` (the bge-m3 service on its own).

Conformance 2026-09-18 (full, with renders): **PASS** — llm `nvidia/Qwen3.6-35B-A3B-NVFP4`,
image `flux1-dev-fp8` (256² 1-step swatch 1.0 s), video `ltx2` only (no Wan weights on
disk; 9 frames 256×448 in 151 s warm-ish), tts `en uk ru` (0.6–1.6 s each), `/files`
round-trips and traversal refusal OK.

Real customer run the same day through `apps/client-web` against this host (embedded
worker): Ukrainian ad post 66 s (FLUX 1024², caption + 20 hashtags), video short 344 s
(the pipeline swapped the `14b` default for `ltx2` because that is all this host
advertises), 1-post + 1-photo campaign 70 s.

## Memory facts that govern everything (measured 2026-09-05)

122.5 GB unified, no separate VRAM. Both ComfyUI and vLLM size themselves from
`cudaMemGetInfo`, which on this box tracks **MemFree, not MemAvailable** — page
cache after a 35 GB safetensors read looks like used memory to them. That single
fact explains most surprises.

| Scenario | Peak | Wall |
|---|---|---|
| Stack idle (vLLM + embeddings + ComfyUI + adapters + API) | 53.5 GB | — |
| FLUX-dev, 1 image 1024², warm | — | 14 s |
| FLUX-dev, 3 keyframes 720×1280 with LLM busy | 86 GB | 38–44 s each |
| LTX-2.5 short 704×1280×121 + RadTTS + LLM, transformer resident | 96 GB | ~105 s |
| LTX-2.5 same, cold after `/free` | 108 GB | ~200 s |
| Wan 2.2 14B short 720×1280×81 + RIFE | 103.5 GB | 615 s |

* LTX text encoder (Gemma-4-12B int8) is pinned to **CPU** in the workflows; the
  55–65 s per new prompt is CPU inference of a 1024-token padded sequence, not a
  load. Keep `CLIPLoader device=cpu` — GPU placement peaked at 118 GB and got
  ComfyUI SIGTERMed by earlyoom.
* `VIDEO_EVICT_BEFORE_LTX=0` (default): consecutive LTX renders keep the
  transformer resident (105 s vs 200 s). Wan 14B always evicts.
* vLLM at `--gpu-memory-utilization 0.35` ≈ 42 GB is what lets image + video +
  embeddings stay resident beside it.
* Campaign cost model and $/campaign scenarios: `docs/PRICING_PLANS.md`.

## Image ladder and avatar (measured 2026-09-27)

| Render | Warm | Cold (after another model) | Notes |
|---|---|---|---|
| Qwen-Image-2512, 1024², 4 steps | 9 s | ~160 s | default rung; ~28 GB with the encoder |
| FLUX.1-dev fp8, 1024², 8 steps | 16 s | ~35 s | prototype rung (non-commercial) |
| Qwen-Image-Edit-2511, ~1184×880, 4 steps | 20 s | ~130 s | shares the Qwen encoder |
| InfiniteTalk (Wan2.1-14B fp8), 480×832, 6 steps, 10.7 s clip | 1340 s | — | 4 windows of 81 frames, ~5 min each, GPU 96% busy (compute-bound); ~2.1 min of render per second of video |

Avatar memory: idle stack 57 GB used → ~104 GB during sampling (17–19 GB
available). Merging the Lightx2v LoRA into the 14B model spiked to 117 GB and
earlyoom killed ComfyUI (2026-09-27); the graph now applies the LoRA at run
time (`merge_loras: false`, `low_mem_load: true`). Speed levers not yet
measured: 4 steps instead of 6, sage attention (not in the container).

Container quirks the avatar graph works around: torchaudio's native library
does not load (aarch64 ABI) — gen-avatar feeds the audio encoder a 16 kHz copy
so no resample runs; ComfyUI-VideoHelperSuite needs libxcb (absent) — core
CreateVideo/SaveVideo mux the audio instead.

## Training window

`scripts/tune.sh` (nightly `ads-tune.timer`, 03:00) must stop every model service
on this box — one GPU. It sets the training gate (workers claim nothing, API
returns 503 on generation), drains, trains the FLUX LoRA in `.venv-train`, installs
the adapter, restarts the stack via `up.sh`. With a dedicated training box set
`MODEL_HOST=<inference-host>` and the stop/restart is skipped.

## Offload mode (generation on rtx3090x3, the Spark's GPU free)

`make offload` (= `offload.sh on`) hands every job kind to the 3090 box and frees
~90 GB here for something else (a big coding LLM, an experiment):

* preflight: the 3090's llm/image/video answer and the `rtx3090x3` worker
  (started by `up.sh` from `workers/rtx3090x3.env`) is running — else it refuses
  (`FORCE=1` overrides);
* ads-api is restarted as a pure control plane (`QUEUE_EMBEDDED=0`) so it never
  claims a job against stopped services;
* stops spark-llm, pd-comfyui, the image/video adapters and spark-embed
  (`KEEP_EMBED=1` keeps embeddings for the other tailnet projects);
* **tts :8105 stays up** — the Ukrainian brand voice (RadTTS mykyta, ~1 GB) lives
  only on the Spark and `workers/rtx3090x3.env` dials it for every short.

What the freed memory is for today: the owner's coding LLM, Qwen3.8-Flash-Next EXL3
(`~/work/qwen38-serve.sh start|stop|status`, OpenAI API :8899; `~/work/qwen38-gateway.sh`
adds an Anthropic-format gateway on :4000 for Claude Code). It needs ~100 GB, so
stop it before `make onload`. Setup notes live next to those scripts, outside the repo.

`make onload` (= `offload.sh off`) is `up.sh` plus putting the embedded worker
back; `make offload-status` shows who serves what. Anything that runs `up.sh`
(a reboot via `ads-stack`, `make start`, the nightly `tune.sh`) also ends offload
mode — that is intended: normal is the default. Admin-only tools that call the
LLM from the API (TTS lab, golden briefs, benchmark) error while offloaded;
customers are unaffected.

## Two-Spark layout (planned 2026-10-06, render host dgx-spark-2)

Goal: every model warm, all the time, on one of the two boxes. Nothing a customer waits
on loads from disk. The existing render mode (`offload.sh render`) is the mechanism; its
render host is now selectable (`logs/.render-host`, default rtx3090x3).

| Box | Keeps warm | Notes |
|---|---|---|
| **dgx-spark** (this box) | vLLM Qwen3.6-35B-A3B :8010 · bge-m3 :8011 (CPU) · MetricX gate :8012 (CPU) · TTS :8105 (kokoro, RadTTS uk, F5 ru, StyleTTS2) · **avatar** :8106 (InfiniteTalk) | plus API, web, Postgres, SearXNG and every ads-worker. Image/video adapters stop in render mode; the watchdog brings them back if dgx-spark-2 fails 3 checks |
| **dgx-spark-2** | Qwen-Image-2512 + Edit-2511 (ComfyUI :8188) · LTX-2.5 (ComfyUI :8189) | `../dgx-spark-2/README.md` |

`workers/dgx-spark-2.env` says `SPARK_KEEPS=avatar`: in render mode this box keeps the
avatar instead of the edit rung (dgx-spark-2 already holds both image rungs warm and has
no room for the avatar). For rtx3090x3 the default `edit` still applies.

Memory on this box in that mode (estimates from the tables above; measure after cutover):

| | vLLM at 0.35 (live today) | vLLM at 0.20 (llm.sh default) |
|---|---|---|
| OS + apps + embed + gate + TTS | ~28 GB | ~28 GB |
| vLLM | 42 GB | 24 GB |
| avatar resident | ~25 GB | ~25 GB |
| **idle** | **~95 GB** | **~77 GB** |
| avatar sampling peak (vLLM sleeps for it, KV freed) | ~115 GB — at the earlyoom line | ~97 GB |

So the layout wants vLLM at 0.20 — the decision already recorded in docs/MODELS.md
(2026-09-27); the running container is still at 0.35. Recreating it is a 2–3 min LLM
outage for the ads system **and the students**, so it is an owner call.

### Cutover (when dgx-spark-2 reports warm in its README status log)

```bash
mv workers/dgx-spark-2.env.off workers/dgx-spark-2.env    # parked so a reboot/tune can't start it early
make offload-render HOST=dgx-spark-2    # worker up, preflight, API → control plane, image/video
                                        # adapters stop, ComfyUI unloads, avatar stays
make offload-status
# owner's OK: LLM_GPU_UTIL=0.20 inference/hosts/dgx-spark/llm.sh recreate
# once, with sudo: the page-cache guard (../dgx-spark-2/dropcache.sh, same units)
```

Then one ad post, one photo edit and one short from `apps/client-web`; the job detail must
name `dgx-spark-2` for image and video. Rollback: `make onload` (this box renders
everything again, as before). Unattended: the watchdog (`ads-offload-watch.timer`) falls
back after 3 failed checks of dgx-spark-2 and returns after 5 healthy ones.

## Quirks

* `earlyoom` is installed; it will SIGTERM ComfyUI above ~118 GB.
* `nvidia-smi` memory is "Not Supported" here; measure with `free -g`
  (MemTotal − MemAvailable).
* The shared containers were created by hand; `compose.shared.yaml` mirrors them
  but `docker inspect` is the source of truth for flags marked unverified.
