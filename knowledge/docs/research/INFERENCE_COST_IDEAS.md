# Inference cost of one campaign — ideas, measurements, refutations (2026-09-05)

Scope: the INFERENCE cost of the reference campaign (3 posts + 2 photos @1024², 2 shorts
= 4 FLUX keyframe candidates @720×1280 + 2 LTX-2.5 renders @704×1280) on the standard
stack (LTX-2.5 22B distilled int8-convrot, FLUX.1-dev fp8 + Hyper 8-step LoRA, Qwen3.6-35B
NVFP4 vLLM, RadTTS/Kokoro, ffmpeg) — models, steps, resolutions, frames, quantisation,
encoders, judges, QA passes, reuse, hardware, business math. Scheduling (waves, affinity,
arbiter) is in `inference/hosts/dgx-spark/README.md` v2 (the "plan") and is not repeated.

Tags: **[M]** measured on this box (today unless dated), **[M-standalone]** measured in a
standalone process inside the pd-comfyui container, not the live service, **[E]**
extrapolated from measurements, **[C]** claimed from external sources, **[U]** unverified.
Baselines follow the plan's §7 formula:
`C = (P_h/3600) × [Σ n_i·p90_i·(1+r_i)·c_i + S_switch/k + T_boot/N] / U`, k=1, N=10,
U=0.85, P_h=$3/h → **$0.60 at 81 frames (610 s), $0.68 at the 121-frame campaign spec
(690.8 s)**. Note: every LTX number measured today is 81 f; `_MODEL_SPECS['ltx2']` renders
121 f (short.py:57). Both are quoted throughout.

Inputs: eleven research sweeps (LTX ladder, video alternatives, FLUX ladder, LLM cost,
text encoder, pipeline waste, systems, economics, quality gate, audio/CPU, reuse,
distillation), three GPU experiments (A, B, C), two ranked lists, a critic pass and eight
two-lens verifications. Where a verifier corrected a number, the corrected number is used
here and the original is listed in §6.

---

## 1. What was measured today

### 1.1 Experiment A — LTX-2.5 i2v ladder (704×1280, seed 12345, same keyframe/prompt, warm, encode served from node cache)

| Condition | Prompt wall | GPU-side stage split | Peak mem | Quality (Qwen VLM 4 frames + PSNR vs c0) | Tag |
|---|---|---|---|---|---|
| c0 shipped: 81 f, base 8 + upsampler + refine 3, audio on | **48.6 s** (replicate 48.7 s, frames bit-identical, 138 dB) | base 8.7 / upsample 0.5 / refine 15.6 (5.19 s/it) / tiled decode+audio+save **23.5** | 95.7 GB | abs 8; note: the plan's "40 s GPU: upsample 7 / decode 8" was wrong — the decode tail is the largest GPU-side stage | M |
| c1 refine 2 steps (sigmas 0.85, 0.4219, 0) | 43.4 s equiv. (−5.2) | refine 10.4 | 95.4 | same clip lightly re-refined: PSNR 33–43 dB; abs 8; pairwise tie in both orders | M |
| c2 base 6 steps | 46.6 s (−2.1) | base 6.6 | 95.4 | different draw (PSNR 12–18 dB = seed-level); abs 9; won both orders | M |
| c3 single stage at full res, 8 steps | **65.2 s (+16.5)** | 8 steps 41.4 s (5.18 s/it) | 95.0 | less motion; NOT a cut | M |
| c4 49 frames | 29.9 s (−18.7) | base 5.8 / refine 9.5 / decode ~13.7 | 94.5 | abs 9; cost ~constant per delivered second (14.4 vs 14.6 GPU-s/s); different video, not a truncation | M |
| c5 audio branch removed | 43.1 s (−5.5) | base 6.5 / refine 12.8 (4.28 s/it) / decode 23.8 | 94.7 | different draw (PSNR 18–22 dB); abs 8; tie / "marginally smoother flare" for c0 | M |
| c6 VAEDecode non-tiled | 43.2 s equiv. (−5.4) | decode+save 18.1 | **111.2 GB** (+15.5) | same picture (37–38.5 dB = tile-seam noise) | M |
| warm-up: transformer "loaded partially" 3.76/20.5 GB | 235.7 s | base 2.38 s/it, refine 7.4 s/it, GPU phase ~90 s | 97.7 | excluded from the ladder | M |

Notes: "prompt wall" is ComfyUI `got prompt → Prompt executed` with the Gemma encode
served from the node cache (identical prompt across conditions); a campaign short adds
the ~57 s CPU encode of a new prompt. c1/c6 walls were partial re-executions; their
full-render equivalents are c0 minus the measured stage delta. Latent tokens =
((F−1)/8+1)·(H/32)·(W/32); step time is near-linear in tokens (0.45–0.53 ms/token-step).
Fit over 49/81 f: prompt wall ≈ −2.8 + 4.675 s per latent frame → **121 f ≈ 72 s [E]**.

### 1.2 Experiment B — FLUX.1-dev fp8 + Hyper 8-step LoRA ladder (warm, one prompt, seed 12345)

| Condition | Wall | s/it | Quality | Tag |
|---|---|---|---|---|
| c0 1024², 8 steps, LoRA 0.125, guidance 3.5 | **15.48 s** | 1.72 | abs 8; pseudo-lettering on product base ("tek cid", "brahe") despite "no text" | M |
| c1 6 steps | 11.75 s (−24 %) | 1.67 | abs 9; wins full-image pairwise 2/2 vs c0 (c0's text + hand-through-glass), loses native 640-px crop pairwise 2/2 ("slightly softer", medium confidence) | M |
| c2b 4 steps @0.125 | 8.12 s (−48 %) | 1.57 | abs 9 at 672 px; loses crop 2/2 high confidence (soft/"waxy" hands); composition changes | M |
| c2 4 steps @LoRA 0.25 / c2c @0.5 | 10.2 / 10.1 s (incl. 2 s LoRA re-patch) | — | grey noise texture / pure RGB noise — the Hyper LoRA is calibrated for scale 0.125; over-scaling collapses it regardless of steps | M |
| c3 768² 8 steps + Lanczos → 1024² | ~9.4 s + 0.25 s | 1.05 | abs 7 upscaled; kept the lettering; loses crop 2/2 | M |
| c4 576×1024 (9:16 keyframe) | **9.41 s** | 1.05 | abs 9; different composition (aspect) | M |
| c5 720×1280 (shipped keyframe size) | 13.87 s | 1.54 | abs 9 | M |
| c6 FluxGuidance 2.0 | 15.56 s | 1.73 | no cost effect; guidance is an embedding, cfg stays 1.0 | M |
| warm-up FLUX after LTX residency | 51.25 s | — | ~25 s LTX eviction + 9.8 s Flux load + 13.6 s steps | M |

Cost model confirmed: ~1.7 s fixed + steps × 1.65 s/Mpx. Changing LoRA strength costs a
2.0 s re-patch. The VLM `has_text` check caught 4/4 real glyph cases and 0/6 false
positives. n = 1 seed; lettering presence per condition is not evidence that fewer steps
suppress text.

### 1.3 Experiment C — the Gemma-4-12B CPU encode (LTX resident, 704×1280×49)

| Condition | Encode span (node 364) | Notes | Tag |
|---|---|---|---|
| 22 / 120 / 466-token positive prompts | 54.7 / 54.9 / 58.0 s | flat: `lt.py:89-90` forces min_length 1024, pad_left | M |
| 1531-token prompt (> 1024) | 78.5 s | +0.044 s/token above the pad floor | M |
| negative = empty string | 55.8 s | BOS + 1023 pads costs the same as 15 tokens | M |
| cold after FLUX (pos + neg) | 111.5 s | transformer then loaded partially 10.6/20.5 GB | M |
| standalone glibc, 20 threads, padded 1024 | 58.5 s (53.5–59.4 over 5 runs) | profiler: aten::mm 74 %, int8-convrot dequant 19.7 s CPU-total, ~36 M minor page faults/encode, sys ~35 % | M-standalone |
| `--fp16/bf16/fp32-text-enc` | 57.8–59.1 s | dtype flag: no effect | M-standalone |
| `set_tokenizer_option('gemma4_min_length', 1)` | 59.4 s, seq_len still 1024 | `gemma4.py:1341` drops kwargs → the options route is dead | M-standalone |
| LD_PRELOAD tcmalloc_minimal | 42.8 s | page faults 44 M → 2.4 M; cosine vs ref 1.000039, per-token min 0.999999 | M-standalone, n=1 |
| OMP_NUM_THREADS=10 + taskset 5-9,15-19 (Cortex-X925) | 40.6 s | big.LITTLE stragglers removed; bf16 GEMM 3.7 vs 2.0 TFLOPS | M-standalone, n=1 |
| tcmalloc + pinning, padded 1024 | **24.5 s** | additive (15.7 + 17.9 ≈ 34.0) | M-standalone, n=1 |
| manual de-pad to 120 tokens, glibc/20 thr | 27.1 s | ~20 s token-independent floor (dequant + page faults) remains | M-standalone, n=1 |
| de-pad + tcmalloc + pinning | **9.1 s** | max abs diff 0.0007 on values ≤ 51.9 (fp16 noise) | M-standalone, n=1 |

Live service today: `OMP_NUM_THREADS` unset, no `LD_PRELOAD`, `Cpus_allowed 0-19`,
`torch.get_num_threads()=20` (docker inspect / `/proc/1/status`). Standalone runs were on an
idle box; no de-padded render has been produced end-to-end yet.

### 1.4 CPU-side finish stage (audio/CPU sweep, CPU-only runs today)

| Stage | Wall | Core-seconds | Tag |
|---|---|---|---|
| RadTTS uk (mykyta) 29.5 s of audio, CPU 4 threads | 2.8–4.2 s | ~12–14 | M |
| faster-whisper medium int8, 8 threads / 20 threads | 5.9–8.2 s / **26–28 s** | — | M |
| large-v3-turbo int8, 10 threads pinned X925 / A725 | 4.1–4.3 / 6.4–6.6 s | — | M |
| ffmpeg compose 704×1280 → 29.7 s short, libx264 medium / veryfast | 3.0 / 2.9 s | 36.6 / 14.7 | M (plan's "~30 s" is stale) |
| 40 caption PNG overlays vs one libass `.ass` | 3.74 / 2.68 s | 38 / 14 | M (bundled ffmpeg HAS libass, no NVENC) |
| loudnorm measure pass; polish | 0.30 s; 0.12–0.37 s | — | M |
| tts_qa (re-synth + re-transcribe per uk short) | 7–12 s | — | M (DB: 5/5 jobs WER 0) |

### 1.5 Other read-only facts used below

- DB (adsystem): 0 campaigns, 5 shorts (all app SmartDevice, all rendered 3 FLUX candidates
  + judge, none via `source_photo_path`), 2 ad_posts, 0 asset_ratings, 8 library_assets
  (0 with `analysis`).
- vLLM: reserves 41.9 GiB at util 0.35 (weights 20.4 + KV 16.3 for 44×24k tokens);
  prefix-cache hits 0.0 % (2096-token GDN block); uk tokenises at 2.36 tok/word vs 1.20 en;
  pretty JSON is +22 % tokens; MTP weights present in the checkpoint; FP4 runs as Marlin W4A16.
- ComfyUI 0.33.3: `get_free_memory` = `cudaMemGetInfo` = MemFree (live: vram_free 16.4 GB
  vs MemAvailable 32.7 GB); `--disable-dynamic-vram --disable-async-offload`; swap 16 GB
  fully used; earlyoom `-m 2 -s 80` fired twice today.
- comfy-kitchen reports native nvfp4 / fp8 / int8 kernels on GB10; `--fast` not set;
  flash_attn 2.7.4 with sm_120 SASS installed, flag not set.
- The on-disk LTX nvfp4 transformer lacks `.comfy_quant` markers (fails to load).

---

## 2. Cost anatomy of the reference campaign

Per-stage seconds, k=1, one family resident at a time. "Lane" = paid GPU-lane time
(GPU busy or GPU idle waiting on ComfyUI's single prompt worker); "CPU" = ARM-core work.

| Stage | 81 f (measured) | 121 f (spec) | GPU busy | CPU busy (GPU idle) | Plan §7 weighted | Tag |
|---|---|---|---|---|---|---|
| 3 posts + 2 photos, FLUX 1024² 8 steps | 5 × 15.5 = 78 s | 78 s | 78 | ~1 s each (decode/save) | 113.9 (p90 18 ×1.15 ×1.1) | M |
| 4 keyframe candidates, FLUX 720×1280 | 4 × 13.9–14.1 = 56 s | 56 s | 56 | — | 70.4 | M |
| LTX Gemma encode, positive, per short | 55–58 s | same | 0 | 55–58 | inside p90 | M |
| LTX GPU-side per short | 48.6 s (base 8.7 / up 0.5 / refine 15.6 / decode+audio+save 23.5) | ~72 s [E] | 48.6 / ~72 | ~1 (mux 0.44) | inside p90 | M / E |
| → LTX per short, warm | ~105 s | ~130–140 s [E] | | | 2 × 115 ×1.155 = 265.7 / 2 × 150 ×1.155 = 346.5 | M / E |
| Family switch FLUX→LTX (first render of the wave) | +87–93 s = ~45–55 s negative encode + 27–37 s transformer materialise + eviction | same | ~30 | ~50 | S_switch 130 (round trip) | M |
| Family switch LTX→FLUX | +20–36 s (49.9–51.3 s first image) | same | ~10 | ~25 eviction | | M |
| Partial-load penalty (MemFree-starved transformer load) | 0 to +42 s per render, +7..+16 s on following renders | larger | | | not in plan | M |
| Boot / N | 30 s | 30 s | | | 30 | E |
| **Plan total** | **610 s → 718 rented s → $0.60** | **691 s → 813 s → $0.68** | | | | |

Overlapped on backfill (do not enter the lane sum; their time-slicing is inside c_i):
vLLM text wave 13–16 s (6 calls), ~7 vision calls 25–35 s of prefill/decode (each
time-slices a running diffusion step, measured 2× FLUX step slowdown while vLLM decodes),
TTS 3 s, whisper 6–8 s ×2 (captions + tts_qa), compose 3 s, PIL 0.6 s per short.

Where the seconds go, 81 f, plan basis (610 s): LTX renders 44 % (of which the CPU
Gemma encode is ~19 % of the campaign and the VAE decode tail ~8 %), images 30 %,
family switch 21 %, boot 5 %. At 121 f: renders 50 %, images 27 %, switch 19 %, boot 4 %.
GPU is genuinely busy for only ~55–60 % of the lane; the rest is the CPU encode, model
materialisation and eviction.

---

## 3. KEEP-QUALITY ideas, ranked

Ranking = seconds saved per campaign ÷ effort, after verifier corrections; savings are
NOT additive across items that touch the same cost (marked).

| # | Idea | Mechanism | Saving (81 f / 121 f) | % of 610 / 691 s | Evidence | Effort | First step in this repo |
|---|---|---|---|---|---|---|---|
| 1 | **Stop encoding the LTX negative prompt** (replace node 373 with `ConditioningZeroOut(['364',0])` or feed 364 into `LTXVConditioning.negative`; never an empty string) | At video_cfg = audio_cfg = 1 `nodes_lt.py:1077-1080` delegates to `CFGGuider` with cfg 1 and `samplers.py:610` drops the uncond pass; `cfg_function` returns exactly cond_pred. The negative is still passed through `extra_conds` (ms) but never enters the denoiser. Node 373 costs a full 1024-token 12B CPU forward on every cold encode (empty string still 55.8 s). | −45…−56 s once per FLUX→LTX switch (first render of each wave: +93 → ~35–48 s; S_switch 115–130 → ~60–85 s) and on every interactive break into LTX; 0 on warm renders. Marginal value after #3: ~−9 s (the negative would then be short too), but #1 is cheaper. | ~7–9 % / 6–8 % | [M] plan §1.2/1.3 (110–121 vs 65.6 s), expC node 373 55.0–55.8 s; source read by 3 sweeps + 2 verifiers. Bit-identical output by construction. | 1 h | Copy `inference/adapters/gen-video/workflows/ltx25_i2v_api.json` to scratch, replace node 373, render the same keyframe/prompt/seed cold (FLUX image in between) with both graphs: expect one encode span (~57 s) before `Requested to load LTXAV`, identical frame hashes. Gate in `app.py _build_ltx` on `video_cfg == audio_cfg == 1` (unequal scales force the uncond pass, `nodes_lt.py:1093`). |
| 2 | **tcmalloc + 10 OpenMP threads pinned to the X925 cores** for pd-comfyui (`LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libtcmalloc_minimal.so.4`, `OMP_NUM_THREADS=10`, `OMP_PROC_BIND=close OMP_PLACES=cores`, `--cpuset-cpus 5-9,15-19`) | The encode is 74 % `aten::mm` + ~20 s of per-forward int8-convrot dequant + ~35 % kernel time servicing ~36–72 M minor page faults (glibc mmap churn of dequant temporaries); 20 static OpenMP threads wait on the 10 slower A725 cores. tcmalloc keeps spans mapped; pinning removes the stragglers. The A725 cores become the home for whisper/ffmpeg/TTS. | Encode 58.5 → 24.5 s [M-standalone, n=1, idle box] = −34 s per warm short; per campaign today −95…−100 s (cold first render 110 → ~49 s, warm 60 → 24.5 s), −68 s after #1. Non-additive with #3: after de-padding the lever is worth ~18 s/encode (27.1 → 9.1). | ~11–16 % / 10–14 % | expC te_bench2.py runs (transcript-verified by one verifier; a second verifier could not find the artefact — re-run and save `te_bench2.log` before relying on it). Conditioning cosine ≥ 0.999999 vs 20-thread reference. Live service: unmeasured; competes with vLLM host threads, TTS, whisper, ffmpeg for the same 10 cores. | 0.25 d operator at the next pd-comfyui recreate (bundle with plan 5.4) | Re-run `scratchpad/expC/te_bench2.py` with OMP_PROC_BIND/OMP_PLACES only (no taskset) to see if binding suffices; at the recreate add the env + cpuset, then read the node-364 span on the ComfyUI websocket for one cold and three warm renders with whisper/TTS/ffmpeg running; grep `loaded partially` == 0; check RSS/MemFree unchanged after encodes (ComfyUI budgets loads from MemFree). |
| 3 | **Drop the forced 1024-token left padding** (custom node setting `clip.tokenizer.gemma4.min_length`, or a 1-line forward of `**kwargs` at `gemma4.py:1341`, or edit `lt.py:90`) | `lt.py:84-92` overrides min_length 1 → 1024 with pad_left; pads are attention-masked (`sd1_clip.py:184-200`) and sliced off at `lt.py:184` before the projection; RoPE is relative, so real-token hidden states are unchanged to fp16 noise. Campaign prompts are 60–120 tokens. The `tokenizer_options` route is dead (`gemma4.py:1341` drops kwargs; measured seq_len stays 1024). | Encode 58.5 → 27.1 s alone (−31 s/short, −62 s/campaign); on top of #2: 24.5 → 9.1 s (−15 s/short). A ~20 s token-independent floor (dequant + page faults) means de-padding alone is ~2×, not 6×. Together #2+#3: LTX short 105 → ~58 s (81 f) / ~81 s (121 f [E]). | with #2: ~20 % / 18 % | [M-standalone, n=1] te_bench2 STRIP=1; per-token cosine ≥ 0.999999, rel-L2 0, max abs 0.0007. No end-to-end render with the patch yet. Lightricks' own ComfyUI-LTXVideo exposes `max_length` ≥ 16 as a supported input. | 0.5 d | Write the 10-line node in the scratchpad (clone clip, set `tokenizer.gemma4.min_length = max(16, round_up8(real_len))`, never truncate), bind-mount into `/home/server/ComfyUI/custom_nodes` at the recreate, insert between CLIPLoader 387 and both CLIPTextEncode nodes; render seed 12345 with/without → PSNR > 40 dB, node-364 span ~9–27 s. |
| 4 | **Use the client's product photo as the LTX i2v keyframe** for product-in-frame shorts (`source_photo_path` / `library_image_id` branch, short.py:224-296) | i2v and t2v graphs differ only by an `LTXVImgToVideoInplace` VAE encode (< 1 s), so the entire keyframe cost is 2 × 14 s FLUX (the judge is vLLM backfill = 0 lane s). A real photo is more faithful to the product than a FLUX re-imagining. | −56.4 s raw / −70.4 s weighted per 2-short campaign ≈ −$0.07. | 11.5 % / 10 % | [M] FLUX 14.1 s; workflow diff. Correction: 0/5 shorts in the DB used this branch (all SmartDevice, 3 candidates each); it fires only for multi-photo campaigns (`campaign.py:306`), single/no-photo campaigns need a planner option. Quality: unmeasured — centre cover-crop to 9:16 (`_fit_library_image`) can lose framing; label text must survive `LTXVPreprocess img_compression=18`. Keep FLUX for lifestyle/mood shorts. | 1 d (planner `keyframe_from: photo|post|generate`, aspect/framing check with the existing single-image judge) | Run one multi-photo campaign (2+ photos, 2 shorts): confirm `result.ref_image_source='campaign_photo'`, zero `Requested to load Flux` between LTX renders, LTX `Prompt executed` equal to the i2v baseline; judge the cropped reference for framing loss and the short for label legibility. |
| 5 | **Fix full-vs-partial transformer loads at the decision point inside ComfyUI** (monkeypatch `get_free_memory` → psutil MemAvailable when `mem_get_info total == RAM`, gated on MemAvailable ≥ ~36 GB; or re-enable DynamicVRAM so comfy-aimdo's integrated-GPU MemAvailable poll is used) | ComfyUI sizes loads from MemFree; the prompt's own mmap reads (14.6 GB Gemma + 20.5 GB transformer) plus the previous wave's checkpoint drop MemFree to 4–7 GB → partial loads (10.6 GB post-FLUX today, 3.76 GB idle-box) → base steps 1.4–2.2×, refine 1.05–1.4× slower, plus unload/reload churn on the next renders until the load completes (5 renders today). | Variance lever, not a mean lever: 0–60 s per campaign (12–40 s per affected render at 81 f; +20 s per FLUX image that partial-loads after an LTX wave). Partial loading itself adds 0 s to load time (27 s partial vs 27–34 s cold full; 9.9 s is a hot-cache reload). | 0–10 % | [M] expA/expC partial loads; live `vram_free` = MemFree; earlyoom `-m 2 -s 80`, swap 16/16 GB used. **The 24 GB allocate-touch-free reclaim is unsafe** (MemAvailable is 13–23 GB during LTX waves; it evicts the hot Gemma/transformer pages and would trip earlyoom); no sysctl changes what `cudaMemGetInfo` returns. | 0.5–1 d + bench; risk of earlyoom at bring-up | Custom node wrapping `comfy.model_management.get_free_memory`; bench per plan 5.3 with zero `loaded partially` and no earlyoom lines across the reference campaign; record MemAvailable at each load. Prerequisites: vLLM at ≤ 0.30 (#7) and swapoff (plan 5.4). |
| 6 | **Non-tiled `VAEDecode`** instead of `VAEDecodeTiled(512/64/64/16)` (node 374) | The tail is 23.5 of 48.6 GPU-side seconds; the 2×3×2 tile grid re-decodes ~40 % of pixels and blends seams. One full-frame decode is 18.1 s but needs +15.5 GB transient (peak 111.2 GB at 81 f; ~+23 GB at 121 f [E]). | −5.4 s/render, −10.8 s/campaign (81 f); ~−8 s/render at 121 f [E]. Pixel-identical (37–38.5 dB seam noise). | 2 % | [M] expA c6. On the Spark only with vLLM ≤ 0.25 and no concurrent job; free on any rented GPU with ≥ 16–23 GB headroom. Untested middle ground: tile 1024 / temporal 96. | 15 min + memory precondition | Try `tile_size 1024, temporal_size 96` first on the Spark (same seed, watch MemAvailable at 1 Hz); switch class_type to `VAEDecode` on rented hosts. |
| 7 | **Right-size the vLLM reservation** (`--max-model-len 8192 --gpu-memory-utilization 0.24-0.25 --max-num-seqs 16`) | Reservation = util × 119.6 GiB regardless of need; KV is sized for 44 × 24k tokens while campaigns use ≤ 16 × 6k (~2–3 GiB). Returns ~13 GB vs 0.35. | 0 s directly; enables #6 on the Spark, lowers partial-load risk for #5, lowers c_i (~−15 s). The 24k context is used only by the tailnet learning app. | 0 (enabling) | [M] startup log KV 15.85–16.66 GiB, concurrency 44×; plan §1.5 KV usage 3.8 % at 8 concurrent. | 0.5 d inside the planned 5.4 recreate | Add the flags, verify KV ≥ 2.5 GiB and concurrency ≥ 20× for 8192, re-run bench.py b/c (expect unchanged), measure idle MemTotal−MemAvailable (−13 GB). |
| 8 | **Charge same-prompt re-renders at warm-GPU cost and keep the prompt string byte-identical** | ComfyUI's RAMPressureCache keys CLIPTextEncode on (text, clip object); a QA re-render with a new seed re-runs only noise → samplers → decode (~48.6 s, not 105 s) provided `__PROMPT__` is never mutated per render, no `/free`, and no other workflow runs in between. | −55 s per re-render/extra seed today (expected −13 s/campaign at r=0.10); after #2+#3 the marginal value is ~−2 s but the cost-model correction stays (p90 × r_i should use 48.6 s, not 115). | ~2 % today | [M] plan §1.3 renders 2–3, expC node 373 cached for a/b/c. | 0.25 d | Submit the same prompt with two seeds via :8104 back-to-back → second render has no encode span; insert a FLUX job between two renders as the control; assert `gen_config['video_prompt']` is passed verbatim in `_make_video`. |
| 9 | **Cache `describe_image` per library photo** (sha256 + prompt version into `LibraryAsset.analysis`; resize fresh calls to ≤ 672 px) | `campaign.py:_describe_source_photo` sends every uploaded photo at native phone resolution to the VLM, serially, before any GPU work (S0); 8 library assets, 0 with analysis. | −3…−5 s per photo of critical-path idle time (likely more at native resolution); −10…−60 s of paid idle before the first FLUX job for multi-photo returning clients. | 1–3 % of rented s, 0 % of GPU | [M] plan §1.5 vision 3.1–5.3 s; prefix caching is structurally 0 % so app-level caching is the only route. | 0.25–0.5 d | Run the reference campaign twice for the same app; second run shows zero `describe_image` requests in the spark-llm log and S0 < 1 s. |
| 10 | **Vision payload budget**: judge/has_text/describe at 672 px, video QA 3 frames at 448 px (client resize in `_image_data_url`, or server `--mm-processor-kwargs longest_edge 451584`) | Qwen3.6 = 1 token per 32×32 px: 720×1280 = 880 tokens, 672 px = 252, 448 px = 112; ~15k → ~4.5k vision prefill tokens per campaign, all of it time-slicing FLUX/LTX steps (Marlin W4A16 prefill is compute-bound, ~0.6–1k tok/s). | −8…−10 s of GPU time-slicing per campaign; per-call latency 3–5 → 1–1.5 s. | ~1.5–2 % | [M] preprocessor_config longest_edge 16.7 Mpx; plan §1.5 ~900 tokens/image. Quality "small": judge criteria are large-scale; has_text needs ≥ 672. Pairs with the quality-gate finding that 4 full-res frames cannot see temporal defects anyway (§9). | 0.5 d | Re-judge 20 stored keyframe pairs + 20 has_text decisions at 1280/672/448; require best_index agreement ≥ 90 %, has_text ≥ 95 %, QA verdict ≥ 90 %. |
| 11 | **RadTTS uk → CPU** (`CUDA_VISIBLE_DEVICES=''`, 4 threads on A725 cores) | RadTTS++ 90 M + Vocos synthesises 29.5 s of mykyta in 2.8–4.2 s on CPU; today it holds 1.64 GB of GPU and its kernels time-slice FLUX/LTX sampling (voice stage runs under `asyncio.gather` with the render). | −1…−3 s of GPU time-slicing per short [E], −1.64 GB resident; +3 s CPU on the little cores. | ~1 % | [M] radtts_cpu_bench.py; nvidia-smi pid 1640 MiB. CPU RNG stream differs from CUDA → takes change once; re-review golden takes. | 0.25 d | Restart gen-tts-kokoro with the env, POST /generate 40-word uk script, `audio.elapsed_s` 3–5 s; confirm nvidia-smi no longer lists the process. |
| 12 | **TTS-native word timings for captions; whisper only for QA; tts_qa reuses the short's wav** | `RADTTS.infer` returns per-token durations (`radtts.py:860`) and the joiner is deterministic → word spans for free; whisper today mis-transcribes brand words onto the screen ("HD-камера" → "з камера"). tts_qa re-synthesises and re-transcribes the same text. | −6…−8 s CPU per short (captions) and −7…−12 s (tts_qa) off the same cores that run the Gemma encode; 0 GPU. Also a caption-quality fix. | 0 % GPU | [M] whisper 5.9–8.2 s, ASR errors in all three model sizes; tts_qa jobs WER 0.0 on 5/5. | 1–1.5 d (RadTTS word mapping) + 0.25 d | Return `words[]` from `/generate`; compare vs whisper on 5 stored wavs (|Δ| < 80 ms); `TTS_QA_MODE=inline` computing WER from the existing transcript. |
| 13 | **Captions via libass `.ass`** instead of 40 PNG overlays; `-preset veryfast -threads 6` on the little cores | The bundled imageio-ffmpeg 7.0.2 has libass (contrary to the subtitles.py comment); one `subtitles=` filter replaces 40 inputs, removes the MAX_CHUNKS=40 cap and gives karaoke `\k` tags. veryfast is 2.5× less CPU at SSIM 0.9837 vs 0.9853. | −1 s wall, −24 core-s per short; contention budget, not $ | 0 % GPU | [M] cap_bench.py, `ffmpeg -filters` lists ass/subtitles. | 0.5 d | Generate `caps.ass` from `chunk_words()`, add `subtitles=caps.ass:fontsdir=data/fonts` to the `[0:v]` chain, PNG path as fallback. |
| 14 | **`--use-flash-attention`** at the next recreate | flash_attn 2.7.4 with sm_120 SASS is in the container (sm_121 is binary-compatible); attention is ~15 % of an LTX refine step, ~11 % of a FLUX step; FA-2 is exact. SageAttention / FA-3 / FA-4 / ck int8 attention are dead ends on sm_121. | −1…−1.5 s per LTX render, −0.5…−0.8 s per FLUX image [E] ≈ −6 s/campaign. Confidence low until the log shows "Using Flash Attention" without fallback. | ~1 % | cuobjdump (read-only); flag never set here. | 0.1 d | Add the flag at the same recreate as #2/#7; compare s/it against expA/expB (1.09 / 5.19 / 1.54); drop on any regression or fallback line. |
| 15 | **Persist the CUDA JIT cache and inductor/Triton caches on a volume; `CUDA_CACHE_MAXSIZE=4 GiB`** | `/root/.nv/ComputeCache` (358 MB) lives in the container layer and is discarded at every recreate; first renders re-JIT. | 0 s per render; tens of seconds per rental-block boot (T_boot term). | 0 % | docker inspect; AEON-7 Spark image practice [C]. | 0.1 d | Bind-mount `/root/.nv`, set the env, time the first FLUX/LTX after a recreate. |
| 16 | **Deferred/subset VAE decode** (critic): sample → save the video-only latent, decode 6–8 latent frames (~2–3 s) for QA/preview, full decode only after QA pass / client approval | Decode is frame-linear and the largest GPU-side stage; with the no-audio graph the latent is a plain tensor so core SaveLatent/LoadLatent work (check `latent_formats` scaling). | −23.5 s (81 f) / ~−35 s (121 f) on every clip that fails QA or is never approved; −20 s per remix-pool clip stored as latent. 0 change for approved clips. | 0–4 % | expA stage split; 10-line node needed. | 1 d | Add SaveLatent after node 348, a frame-slice node + VAEDecode for QA frames; compare QA verdicts on subset-decode frames vs full decode on 20 shorts. |

Not on the list (dead ends, verified): base-step trimming as a keep-quality item (changes
the draw); single-stage full-res sampling (+34 %); batching seeds (compute-bound, 1.03×);
distilled→dev model; replacing Gemma with Qwen or a smaller encoder (projection trained on
Gemma-4-12B hidden states); more encoder quantisation for CPU speed (every format is
dequantised per forward on CPU); `--fp32/bf16/fp16-text-enc` (no effect); vLLM prefix
caching / padding prompts to 2096 tokens; FLUX T5/CLIP conditioning cache (≤ 1 s/image);
SageAttention/FA-3/FA-4 builds; CUDA graphs (steps are 1–5 s); the bf16 encoder file as a
separate lever (its 24 → 4 s dequant claim overlaps the tcmalloc gain and doubles the
page-cache pressure that causes partial loads); the plan's §11 Q1 "Gemma on GPU" and Q2
sidecar as urgent items (after #2+#3 they are worth ~5–9 s/short, not 60).

---

## 4. TRADE-QUALITY ideas, ranked

Ranking by seconds saved per unit of quality given up; "who notices" is explicit.

| # | Idea | Mechanism | Saving (81 f / 121 f) | % of 610 / 691 s | What you give up, who notices | Evidence | Effort | First step |
|---|---|---|---|---|---|---|---|---|
| 1 | **LTX refine 3 → 2 steps** (`ManualSigmas` node 396: `0.85, 0.4219, 0.0`) | Each refine step is a full-res 9,680-token 22B pass (5.19 s/it), the most expensive step; dropping the middle sigma keeps the 0.85 re-noise start and endpoint. Output is the same clip lightly re-refined, not a new draw. | −5.2 s/render (−10.4/campaign) / −7.4 s/render (−15) | 2 % | Tiny at n=1: PSNR 33–43 dB vs shipped, VLM tie in both orders. Theoretical risk: fine-texture shimmer, label-edge softness on product close-ups. Nobody at feed size; possibly a client zooming a product shot. Must pass the §9 gate (8–16 seeds) before becoming the campaign default. | [M] expA c1; Lightricks: "4 steps usually enough" for the upsampler stage | 1 h (`__REFINE_SIGMAS__` placeholder, campaign vs studio tiers) | 8 campaign prompts, same seeds, 3 vs 2 steps; QA judge + PSNR/SSIM; ship if every pair > 30 dB and verdicts agree. |
| 2 | **Drop the audio branch on voiceover shorts** (campaign workflow variant without LTXVEmptyLatentAudio / ConcatAV / SeparateAV / AudioVAE decode) | `short.py:798-812` discards LTX's soundtrack whenever a VO exists; the AV transformer accepts a plain video latent (`run_ax=False`), removing audio tokens from both samplers (base −25 %, refine −18 % per step) and the audio VAE (−0.7 GB). | −5.5 s/render (−11/campaign) / ~−8 s [E] | 2 % | The video is a different draw (a2v cross-attention feeds video; PSNR 18–22 dB), so it cannot be pixel-A/B'd; VLM abs 8 = baseline, pairwise tie / "marginally smoother flare" for the AV version. No-VO shorts lose the synced ambient audio (keep the AV graph for them and for the Studio picker). | [M] expA c5 | 0.5 d (`ltx25_i2v_novo_api.json`, adapter flag `has_voiceover`) | Build from `scratchpad/expA/c5.json`, route in `app.py _wf_file`, confirm compose still muxes the VO, gate with §9. |
| 3 | **Frame budget: campaign spec 121 → 81 f; 'ambient' shorts → 49 f; or 61 f @12 fps + RIFE ×2** | Cost is linear in latent frames (16 / 11 / 7 latent frames); `_compose` already `-stream_loop`s the clip to the VO length (only when a VO exists). | 121 → 81: ~−23 s GPU-side/short [E from fit], −47/campaign; 81 → 49: −18.7 s/short [M], −37/campaign; 121 → 49: ~−42 s/short [E], −84/campaign. 61f@12+RIFE: est. −12…−15 s/short [E], RIFE 1.5 s. | up to 12 % | Loop seam every 5.0 / 3.4 / 2.04 s: a 15 s uk VO loops ~3 / ~4 / ~7 times. Every viewer who watches the whole short sees the repeat; ambient shots (steam, slow dolly, product turn) hide it, action shots do not. 49 f is a different video, not a truncation. RIFE: ghosting on fast hands; 12-fps conditioning is off-distribution (unmeasured). | [M] expA c4; fit −2.8 + 4.675 s/latent frame | 1 line (`_MODEL_SPECS['ltx2']`) behind a tier/tag; 0.5 d for RIFE plumbing | Same keyframe/prompt/seed at 121/81/49 f via run_short (new prompt each), compose with a real VO, human-rate seam visibility; try ping-pong (forward+reverse) looping. |
| 4 | **FLUX 8 → 6 steps for posts/photos; 4 steps @0.125 for keyframes only** | Compute-bound at 1.84 s/step (1024²); the Hyper 8-step LoRA still converges at 6 and 4 steps at its calibrated scale 0.125 (0.25/0.5 collapse the image — LoRA over-application, not a step effect). LTX conditions the base pass at half res and the refine re-synthesises detail, so keyframe softness is mostly erased. | 6 steps: −3.7 s/1024² image, ~−3.1 s/keyframe [E] → −31 s raw / −42 s weighted per campaign; 4-step keyframes: −6.4 s each → −26 s | 7 % (6 steps) | 6 steps: unproven at n=1 — a different image, not a softer one (pixel MAD 14/255); the 8-step baseline had its own seed artefacts (pseudo-text, hand through glass) and the judge preferred the 6-step image overall; loses the native-crop test 2/2 at medium confidence. 4 steps on posts: soft/"waxy" hands at 100 % zoom (VLM phrase, confirmed by eye at n=1), invisible at feed size and to the ≤ 672 px QA. A client downloading hero stills notices; feed viewers do not. | [M] expB c1/c2b/c2/c2c | 0.25 d (`__STEPS__` placeholder + `steps` pass-through in `backend/ads/providers/image.py`, which today never sends it) | ≥ 10 campaign prompts × 3 seeds at 8/6/4 steps; order-swapped pairwise + native-crop judge + text-artifact QA fail rate + one human pass. |
| 5 | **Keyframes at 576×1024 (+ 6 steps) instead of 720×1280** | The keyframe conditions LTX at 352×640 (strength 0.7); only the 3-step refine sees 704×1280 after the latent upsampler, so a 1.22× Lanczos upscale (existing PIL cover-crop) carries nearly the same information. | −4.5 s/candidate [M] (13.87 → 9.41); with 6 steps ~7.6 s [E] → −18…−25 s per campaign (4 candidates), −9…−13 s with 1 candidate | 3–4 % | Small: judge tie (9 vs 9) at 672 px; the loss would show only on product close-ups where label text must survive the upscale + `img_compression=18`. The client never sees the keyframe itself. | [M] expB c4/c5; plan §11 Q10 pending check | 0.25 d (`short.py:282` request size) | Same-seed i2v from 720×1280 vs 576×1024 keyframes; video QA + eyeball frames 0/60 for label legibility. |
| 6 | **Keyframe candidates 2 → 1, or sequential early-accept** (judge candidate 0 alone; render #2 only below threshold) | Each candidate is a full FLUX render (batching 1.03×); the judge is backfill. | Flat: −28 s raw / −35 s weighted; sequential at p_fail 0.3–0.4: −17…−20 s | 5–6 % | You give up best-of-2. DB: candidate 0 won 3/5; where all scored 3 nothing rescued it. Under flat N=1 ~30–40 % of shorts open on a slightly weaker frame; sequential recovers most of it for +1 vision call. No judge_scores are persisted yet, so the pick-rate is unmeasured. | [M] plan §1.4; DB gen_config.keyframe | 0.1 d flat / 0.5 d sequential | Log judge_scores + best_index for 20 shorts first; invert `short.py:274-290` to judge-then-render with threshold = 25th percentile of observed `overall`. |
| 7 | **Base 8 → 6 steps** (node 397: drop 0.9875, 0.98125) | Base steps are cheap (1.09 s/it at half res). | −2.1 s/render / −3 s [E] | < 1 % | A different draw (PSNR 12–18 dB = seed level); abs 9, won pairwise in both orders at n=1 — the only condition immune to the judge's position bias. Cannot be pixel-A/B'd; gate on 8–16 seeds. | [M] expA c2 | 15 min | Bundle with #1 in the same gate run. |
| 8 | **LTX 704×1280 → 576×1024** (campaign tier; lanczos to 1080×1920 in compose) | Tokens scale with H·W: 6,336/1,584 (−35 %); every sampler stage and the tiled decode shrink; FLUX keyframe at 576×1024 matches natively. | GPU-side 48.6 → ~32 s/short [E] (−16, −32/campaign) / ~−24 s at 121 f | 5 % | Noticeable: softer product close-ups and label text after upscale; phone viewers rarely notice, a client reviewing on a monitor or a product-shot-heavy brief does. Keep 704×1280 for Studio. | [E] from measured token-linearity; not rendered at 576×1024 | 0.5 d | Add a `campaign` entry to `_MODEL_SPECS`, `scale=1080:1920:flags=lanczos` in `_compose`; same-seed side-by-side on a phone; judge label legibility. |
| 9 | **Zero-GPU 'slideshow motion' short** (ffmpeg zoompan/crossfade over the campaign's FLUX heroes; Depth-Anything-V2-Small parallax for product-on-background; animated PNG/libass overlays) | Reuses an image already paid for; runs on the little cores off the GPU lane. `RESEARCH_LOCAL_SHORTS_MODELS.md §3.4` lists the tier; nothing implemented. | −105…−140 s render −28 s keyframes ≈ −133 s per converted short; +5–15 s CPU | 22 % per converted short | Noticeable: a moving still, not a generated scene. Every viewer sees it is a slideshow/parallax; it is a standard reel format, so sell it as a Lite tier or the 3rd/4th short, never as the hero short. Parallax is weak on people. DepthFlow is AGPL (run unmodified as a CLI or reimplement); only the Small depth model is Apache-2.0. | [M] compose 3 s; zoompan is core libavfilter; [C] DepthFlow | 1 d (+1–2 d parallax) | `video_model='still'` in short.py building the clip with `zoompan=z='min(zoom+0.0008,1.2)':d=121:s=1080x1920`; time on the A725 cores during a Gemma encode; 5 shorts through QA + a human. |
| 10 | **Raw-clip remix pool for repeat clients** (persist raw clip + wav + keyframe content-addressed; recompose new hook/VO/captions/grade; ffmpeg variants: zoom-crop, 0.9×/1.1× speed, flip, alternate grade) | Everything after the render is a 3 s ffmpeg compose; today only the final MP4 is blob-stored. | 0 GPU per remixed short (−133 s); policy 1-of-2 per weekly campaign −133 s; all-remix week ≈ 46 GPU-s | 22 % per remixed short; from the client's 2nd campaign | Noticeable: the same footage narrated differently; the client and returning followers see repeated b-roll. Standard for real brand footage but must be a disclosed 'remix' tier. Client acceptance is unmeasured (0 campaigns, 0 ratings). | [M] compose 0.9–3 s; `short.py:464` blob path | 2 d | Keep `video_rel` + wav in blobs (sha256 key), add a `remix` path skipping prepare/keyframe/render; re-run `_compose` on the 5 stored shorts with new hooks and show pairs to the client. |
| 11 | **Shared visual concept**: one FLUX hero per concept across platform-variant posts (copy + overlay differ) and as the short's keyframe | Posts already differ by copy and branded overlay; `short.py`'s ref-image path skips FLUX and the judge. | −16 s per avoided post image (3 posts sharing 1 hero: −32 s) + −28 s per short keyframes → up to −88 s raw / −105 s weighted per campaign | up to 17 % | Fewer distinct visuals per campaign — the client sees the same hero on 3 posts and opening the short. Consistent hero imagery is normal in ad campaigns; motion from a post hero is less scene-specific than a purpose-rendered keyframe. Needs a taller 1024×1536 master if 1:1 and 9:16 share pixels. | [M] FLUX 16/14.1 s; workflow diff | 1–1.5 d (planner `concept_id` / `hero_of` / `keyframe_from`) | `CAMPAIGN_HERO_MODE=shared`; count `image_provider.generate` calls (expect n_heroes + n_photos); client rates variety vs consistency. |
| 12 | **Video QA on 3 frames at 448 px + CPU CLIP temporal-drift pre-gate**; never skip QA on a high keyframe score | QA is the most expensive vision call (4 × 880 tokens); CLIP ViT-L/14 (already on disk as the FLUX encoder) drift between frames on CPU gates the clean majority. LTX failure modes are temporal and not predicted by the still keyframe score. | −3 s GPU per short + −1…−2 s if 70 % skip the VLM | ~1 % | Small: fewer/lower-res frames may miss a mid-clip morph; §9 says the fix is MORE frames at lower res (8–12 at ≤ 448 px, same tokens), so adopt that form instead. | [M] plan §1.5; HPSv3 paper (CLIP r=0.30 as a quality judge, fine as a drift gate) [C] | 1 d | SQL over jobs.result: P(fail \| overall ≥ 8) vs P(fail); replay 40 stored clips through QA at 4×1280 vs 8×448; keep recall of fails = 1.0. |
| 13 | **Hosted LLM/vision API for campaigns** (Haiku 4.5 / Gemini Flash-Lite / OpenRouter Qwen3.6-35B at $0.03–1.00/M) | ~20–27k input + 4.5–6k output tokens per campaign ≈ $0.005–0.05; removes the 29–42 GB vLLM slice, the 2× FLUX step slowdown during decode, and vLLM's 240 s of T_boot. | −15…−20 s contention + −24 s/campaign of boot at N=10; enables 24/32 GB rented cards. The sweep's "−265 s / 43 %" was double-counted: the CPU-encode term is removed by §3 #2/#3 independently and S_switch does not vanish because vLLM shrinks. | ~5–7 % | Business trade, not pixels: client product photos and uk brand data leave the box; provider quantisation/sampling differ (uk copy must be re-judged); the tailnet learning app loses its local LLM; vendor latency 1–4 s. | [M] plan §1.4/§1.6/§7; pricing [C] | 0.25 d (OpenAI-compatible host) / 1–2 d (Claude adapter) | Point `LLM_BASE_URL` at a host for 10 uk campaigns; log usage tokens; blind-rate 30 captions + 10 scripts vs local; require ≥ 90 % parity. |
| 14 | **Wan2.2-TI2V-5B-Turbo** (4-step, CFG 1, GGUF Q8) as a 480×832 draft tier | The shipped 5B graph is slow only because 20 steps × CFG 5 = 40 passes (96 s sampling); Turbo needs 4 passes (~2.4 s each), umt5 encodes on GPU in ~2 s, 12.5 GB footprint. | ~105 → ~38–40 s/short at 480×832×81 [E]; NOT cheaper than CPU-fixed LTX at 704×1280 | up to 24 % [E] | Noticeable: 5B at 480p vs 22B at 720p — softer textures, weaker text/hands/product fidelity, no synced audio, less diversity. The Turbo repo has no LICENSE (FastWan2.2 is the Apache-2.0 alternative, Diffusers-only). No Turbo render was run. Confidence low. | [M] Wan 5B shipped 123.5 s warm, decode 23–24 s; [C] GGUF card | 1–2 d | New workflow with `UnetLoaderGGUF`, steps 4, cfg 1; 3 renders at 480×832 and 704×1280 vs the same LTX short; judge + human. |
| 15 | **NVFP4 LTX-2.5 transformer** (comfy-stamped file) | comfy-kitchen reports native `scaled_mm_nvfp4` on GB10; int8-convrot dequantises per GEMM. Community 5090: fp8 → nvfp4 1.3–1.4× per step. The on-disk file lacks `.comfy_quant` markers. | Sampling 24.3 → ~15–19 s/short [C], −12…−20 s/campaign; −2.8 GB | 2–3 % | Small with a stack-specific risk: an RTX 5090 test of LTX-2.3 nvfp4 found i2v "failed to preserve reference image features"; product fidelity is the whole ad. Needs a judged A/B, not a speed test. On a rented Blackwell card the share is larger (sampling is the whole render there). | [C]; header diff of the local file | 1 d incl. 18.7 GB download | Download BennyDaBall/LTX-2.5-22b-distilled-nvfp4-comfy, swap `UNETLoader` node 384, same seed A/B, human check on product shape/label/colour. |
| 16 | **Salvage a QA-failed clip by trimming to the clean span** instead of a full re-render | `_compose` loops anyway; if the defect is confined to one end, loop the clean ≥ 1.5 s span. | −48…−105 s per flagged short × r ≈ −5…−10 s expected | ~1 % | The QA sees 4 frames and cannot localise a defect between them; a 1.5 s span over a 10 s VO is 7 repeats — the seam problem of #3 squared. Only viable with the 8–12-frame QA of §9. Low priority. | [M] jobs 8e5d6411 (1 Wan flag in 5 shorts, 0/4 LTX) | 0.5–1 d | Only after §9's multi-frame QA exists. |

Dead ends verified for this list: ffmpeg `ultrafast` (SSIM 0.9685, 2.3× file size);
`VIDEO_SMOOTH` minterpolate (5 s/54 core-s for blend ghosting); whisper `small` for uk;
learned ESRGAN upscale after a low-res render (10–20 s on the GB10, no model installed);
12-fps delivery without RIFE; EasyCache/TeaCache on 8-step distilled schedules (only ~6
eligible steps, artefacts reported); FLUX.1-schnell (dominated by klein-4B); Wan 14B /
MiniMax-H3 / HunyuanVideo 1.5 / CogVideoX / SVD / AnimateDiff for campaigns; FLUX/LTX
LoRA training as a cost lever (LTX: 10–20 h whole-box outage, distilled-schedule
compatibility unanswered upstream, ≤ 5 % prize; FLUX product LoRA pays back only via
candidates 2 → 1 and is unproven end-to-end).

---

## 5. Fresh product-shaped ideas from the critic

1. **Deliverable-count SKUs with a per-SKU cost seed.** n_i dominates every sampler knob:
   one LTX short is 105–140 s of a 350–530 s campaign. Lite = 3 posts + 1 LTX short (81 f,
   refine-2, no-audio) + 1 slideshow/remix short; Standard = 2 shorts at 81 f; Studio = 121 f,
   best-of-2 keyframes, 3 refine steps, 8-step heroes. The market listing already prices a
   short at $1.00 against a $3.00 campaign — the product is under-priced exactly on the
   expensive deliverable. Cost effect: Lite −105…−133 s GPU (−30…−38 %); Studio +40…+60 s
   but priced. Quality: explicit, chosen by the client.
2. **Brief-difficulty router.** The planner (already an LLM call) tags each piece:
   `product_in_frame`, `motion_class` (ambient/action), `faces`, `legibility_needed`,
   `hero_downloadable`. A routing table maps tags to the measured conditions: ambient
   b-roll → parallax/zoompan or LTX 49 f no-audio; action/faces → 81 f + best-of-2;
   keyframes → 576×1024 @4–6 steps; feed posts → 1024² @6; downloadable heroes → 8 steps.
   The video-QA judge stays the safety net; a misroute falls back one rung. Expected
   −40…−60 % GPU seconds on a typical product campaign; quality small when tags are right.
3. **Client-side compositing.** Ship the raw clip + an edit-decision JSON (hook, word-timed
   captions, end card, VO wav, music) and let the web client render finals with
   WebCodecs/canvas. Unlimited free hooks/caption styles/crops/language VOs, no job, no blob
   per variant; server ffmpeg stays as fallback. Pixels unchanged; encoder/font differences
   must be pinned.
4. **Pre-rendered motion templates per product.** At onboarding render 5–8 LTX primitives
   per product (turntable, push-in, pick-up, pour, unboxing) at Studio quality off-peak on
   the Spark (~10 min GPU once, $0 marginal at night); campaigns pick a primitive, composite
   a fresh product cutout (background-removal model present) or hero, and recompose. Marginal
   GPU per templated short → ~0. Homogeneity across a client's campaigns: sell as Lite/remix.
5. **Express vs overnight pricing tied to the existing 12 h SLA.** A `deliver_by` field lets
   the queue defer non-urgent campaigns into one nightly block: k > 1 merged waves
   (S_switch/k), U 0.6 → 0.85–0.9, boot/N at N ≥ 25, spot instances ($0.08–0.15/h) usable
   because a preemption only delays an overnight job. Overnight tier ≈ −30…−45 % rented
   seconds and 2–4× lower hourly price → roughly −60…−80 % $/campaign vs quoting everything
   at k = 1 on-demand; express pays for idle pods and interactive breaks.
6. **Deferred / subset VAE decode** (listed as §3 #16): decode 6–8 latent frames for QA and
   preview, full decode after approval; store latents for the remix pool.

Critic's un-researched gaps worth a task each: the VAE decode tail (dtype, tile 1024 /
temporal 96, torch.compile/cudnn autotune on the causal 3D decoder, PyAV frame-copy cost);
the 121-frame spec (never measured); no end-to-end campaign has ever been measured
(0 campaigns in the DB; `routers/metrics.py` cost endpoint does not exist, so r_i, c_i, U,
S_switch, T_boot are all seeds); tail/variance control (hard caps: max 1 re-roll, max 1 QA
re-render, reject-and-refund instead of a 3rd attempt; admission control per block; p90
quotes); in-place text-artifact repair (PIL/LaMa inpaint on CPU, or let the overlay cover
small lettering) instead of a 16 s re-roll; vLLM's own host-CPU multimodal preprocessing
and the omniparser restart loop as tenants of the same 20 cores; storage/egress and ~80 GB
weight distribution per fresh rented host (same order of magnitude as GPU cost there).

---

## 6. What the verifiers refuted or corrected

| Claim as submitted | Verdict | Correction used in this document |
|---|---|---|
| Negative-prompt removal is "never read", saves 55 s "even for an empty string", cuts "FLUX→LTX switch 130 → 75 s" | mechanism holds; wording and attribution wrong | The negative is read by `extra_conds` (ms) but never enters the denoiser; shipped node 373 carries the template text, not an empty string; 130 s is the ROUND-TRIP S_switch. Saving = 45–56 s per cold encode; FLUX→LTX +93 → ~35–48 s; round trip 115–130 → ~60–85 s. Gate on `video_cfg == audio_cfg == 1`. |
| tcmalloc + pinning: 58.5 → 24.5 s, cosine 1.000000, −68 s/campaign | measurement lens: real (transcript-verified, n=1, idle box); feasibility lens: no artefact found, live service cannot take it without a recreate | Booked as [M-standalone, n=1]; the "1.000000" is the padded-vs-stripped check inside the pinned run — cross-config cosine is 1.000039 / per-token ≥ 0.999999 for the single-change runs. Campaign effect today −95…−100 s (cold first + warm second), −68 s only after negative removal, ~18 s/encode after de-padding. Live-service effect under whisper/TTS/ffmpeg/vLLM host contention is unmeasured; re-run `te_bench2.py` and save the log. |
| De-padding: 27.1 s alone / 9.1 s stacked, "~10-line custom node" | measurement lens: numbers real, floor explained; feasibility lens: could not find the artefact, physics floor ~20–25 s | A ~20 s token-independent floor (int8-convrot dequant + page faults) caps de-padding alone at ~2×; 9.1 s only with tcmalloc + pinning. The custom node must set the tokenizer attribute directly (or forward kwargs at `gemma4.py:1341`) — the `tokenizer_options` route is dead. Equivalence is numerical (max abs 0.0007), not bit-identical. No de-padded render exists yet. |
| Full-load guarantee via a 24 GB allocate-touch reclaim or sysctl, worth −25…−45 s/campaign | refuted | The 3.76 GB partial load was an idle-box event 4.5 h after the last FLUX job (swap full), not a post-FLUX-wave effect; the real post-FLUX load was 10.6 GB and completed over 5 renders. Partial loading adds 0 s to load time (27 s partial vs 27–34 s cold full; 9.9 s is a hot-cache reload). GPU phase +42 s in the pathological case, +7…+17 s typical. The reclaim is unsafe (earlyoom -m 2, swap 16/16 GB, MemAvailable 13–23 GB during LTX waves; it evicts the hot Gemma/transformer pages; it cannot outlast the prompt's own 35 GB of file reads); no sysctl changes `cudaMemGetInfo`. Fix must sit inside ComfyUI (psutil MemAvailable gated ≥ ~36 GB, or DynamicVRAM). Prize 0–60 s/campaign, variance not mean. |
| Combined safe stack: 476 → 291 lane-s, $0.47 → $0.285 (−39 %), zero quality change, 2.5 days, LTX short 105 → 52.5 s, 80–90 % GPU-bound | refuted | 476/291 used a non-plan basis and divided by U twice ($0.47 = 476/0.85 × $3/3600). Negative removal and de-padding act on the same CPU forward (non-additive); negative removal lives in S_switch, not in the per-short 105 s; the full-load guarantee is 0 s against a full-load baseline; refine-2 is a quality trade. On the plan basis: 81 f $0.60 → ~$0.43–0.47 (−22…−29 %); 121 f $0.68 → ~$0.48–0.52; per short ~58 (optimistic) to ~74 s (pessimistic) at 81 f; ~80 % GPU-bound at best. ~1 day for the encoder items; the load guarantee is plan §11 Q1 work. |
| Photo keyframe: "already used by 5/5 Yaknove shorts", saves 56 s (12 %) + 4 s judge, stacks to $0.21–0.22 | refuted on provenance and accounting | 0/5 shorts used the branch (all SmartDevice, 3 FLUX candidates + judge each); it fires only for multi-photo campaigns (`campaign.py:306`). The judge is backfill (0 lane s). Saving 56.4 s raw / 70.4 s weighted (11.5 % / 10 %) ≈ $0.07. $0.21–0.22 omitted S_switch + boot (160 s): correct stacked figure ~$0.36–0.40 at 81 f. Quality unmeasured (centre cover-crop of arbitrary client photos). |
| 49 frames: "48.6 → 29.9 s GPU-side, −37 s/campaign, seam 2.0 vs 3.4 s" | measured but mislabelled and against the wrong spec | 48.6/29.9 s are cached-encode prompt walls (include ~9 s of CPU/VAE/mux), not pure GPU; the campaign spec is 121 f, so the relevant delta is 121 → 49 (~72 → 30 s [E], −42 s/short, −84/campaign) and the seam cadence 5.0 → 2.04 s (~7 repeats over a 15 s VO); the 49 f clip is a different video. |
| FLUX 6 steps "only native-crop softness"; 4 steps "visibly waxy"; LoRA 0.25/0.5 "destroys the image at 4 steps" | timings hold; quality claims not supported | n = 1 prompt/seed; 6-step output is a different image (MAD 14/255), the 8-step baseline had seed artefacts the 6-step lacked, objective sharpness does not rank 8 > 6 > 4; "waxy" is a VLM phrase (same judge scored the image 9/10). LoRA collapse is over-application of a scale-0.125 LoRA, independent of step count. `steps` is not plumbed from `providers/image.py`. |
| Sweep-level double counts | corrected | The Gemma encode appears in 6 sweeps and the negative prompt in 4; percentages in the sweeps are not comparable (baselines 346/450/476/531/610 s). fp8_matrix_mult, nvfp4 FLUX and torch.compile address the same GEMM path (one per model). The LLM-memory angle's "−250 s / 40 %" and hosted-API "−265 s" double-counted the CPU-encode term and assumed S_switch vanishes. Video-alternatives' single-stage 540p "−45 %" contradicts expA c3 (+34 %). The bf16 encoder file's "24 → 4 s" overlaps the tcmalloc gain. The audio-branch drop was rated "none" in both lists although it is a different draw. |

---

## 7. Stacked scenarios

Plan §7 formula, k = 1, N = 10, U = 0.85. Encoder items #2+#3 taken at the standalone
figure (9.1 s, "optimistic") and at 25 s ("pessimistic": one of the two levers only, or
live contention). S_switch 130 → 75 after negative removal. p90 = p50 + ~5–7 s.

| Scenario | Composition | GPU-lane s (weighted) | + switch + boot | Rented s | $ at $3/h | Δ vs today |
|---|---|---|---|---|---|---|
| Today, 81 f (plan) | 5 × 1024² @8 + 4 kf @720×1280 + 2 × LTX 81 f | 450 | 610 | 718 | **$0.60** | — |
| Today, 121 f spec (plan, [E]) | same, LTX 121 f | 531 | 691 | 813 | **$0.68** | — |
| **SAFE** 81 f, optimistic | §3 #1–#3 (+#5,#7,#8 as enablers), same deliverables | 330 | 435 | 512 | **$0.43** | −29 % |
| SAFE 81 f, pessimistic | encode 25 s instead of 9.1 s | 369 | 474 | 558 | $0.47 | −22 % |
| SAFE 121 f, optimistic [E] | LTX GPU-side ~72 s + 9.1 s encode → p90 88 | 388 | 493 | 579 | **$0.48** | −29 % |
| SAFE 121 f, pessimistic [E] | p90 105 | 427 | 532 | 626 | $0.52 | −23 % |
| SAFE + photo keyframes (multi-photo campaigns only), 81 f | 4 keyframes → 0 | 259 | 364 | 429 | $0.36 | −40 % |
| **LITE** 81 f | posts/photos @6 steps (p90 12.5), 1 keyframe/short at 576×1024 @6 (p90 8.5), LTX 81 f refine-2 + no-audio + base-6 (p90 50) on top of SAFE | 213 | 318 | 374 | **$0.31** | −48 % (−54 % vs the 121 f spec) |
| LITE 49 f (ambient shorts) | LTX 49 f p90 35 | 179 | 284 | 334 | $0.28 | −53 % |
| LITE + 1 slideshow short | one LTX short + one zero-GPU short, 1 keyframe | 146 | 251 | 296 | $0.25 | −59 % |
| Repeat-client remix week (from campaign 2) | 1 LTX short + 1 remix, shared hero as keyframe | ~113 | ~268 | ~315 | ~$0.26 | −56 % |

Quality given up by LITE: a different, slightly re-refined draw of each short with no
ambient soundtrack; 6-step heroes that lose a native-crop sharpness test; a single 576-px
keyframe instead of best-of-2 at 720 px; at 49 f a loop seam every 2 s. None of it has
passed the §9 gate yet.

### 7.1 Cheapest rented host (economics research: marketplace RTX 5090, $0.27/h on-demand, $0.08/h spot; RTX 4090 $0.29/h; all prices 2026-09-05, availability varies)

On a discrete card the CPU-encode items are moot (encoder on GPU: 3–4 s; on 24/32 GB cards
it is swapped per prompt, cost unmeasured), so SAFE there = negative removal + full loads +
untiled decode; LITE = the same trade-quality knobs. Per-host LTX seconds are community-
anchored [C/E] (5090 ~30 s/short, 4090 ~45 s), not measured; switch ~30 s (NVMe reload),
boot/N 30 s, LLM on a per-token API +$0.009/campaign.

| Host | Scenario | Rented s | $ GPU + LLM API | Campaigns/h per card |
|---|---|---|---|---|
| RTX 5090 OD $0.27 | SAFE | ~200 | **$0.024** | ~18 |
| RTX 5090 OD $0.27 | LITE | ~163 | $0.021 | ~22 |
| RTX 5090 spot $0.08 | SAFE / LITE | 200 / 163 | $0.013 / $0.013 | — |
| RTX 4090 OD $0.29 | SAFE / LITE | 255 / 203 | $0.030 / $0.025 | 14 / 18 |
| RTX PRO 6000 96 GB $0.66 (all models resident, vLLM self-hosted, no switch) | SAFE | ~147 | $0.027 | ~25 |
| DGX Spark owned, power only (~$0.02–0.04/h at 22–100 W) | SAFE / LITE | 512 / 374 | ~$0.005 / ~$0.004 + amortisation | 7 / 9.6 |

Reading: on rented consumer cards GPU is 0.7–1 % of a $3 campaign and the whole inference
ladder is worth ~$0.003–0.01 per campaign; ops dominate (1-hour block minimum = $0.27
whether it runs 1 or 20 campaigns, ~80 GB of weights per fresh Vast host, idle drain, egress).
The ladder's value there is campaigns per block-hour (18 → 22 on a 5090) and on the owned
Spark it is throughput and SLA (4.9 → ~7–10 campaigns/h) plus the p90 the client is quoted.
The "$3/h" column is the right one for the Spark-class or H100-class rental the owner may
be forced into by availability; the 5090 column is the target.

---

## 8. Hardware / hosting recommendation

1. **Owned DGX Spark**: keep for dev, interactive one-offs, uk RadTTS/voice work, DB/API/
   embeddings, the tailnet LLM, the free tier, and off-peak template/onboarding renders.
   Do not load its $130/mo amortisation onto campaign COGS — the box is required anyway;
   marginal campaign cost there is power. Apply §3 #1–#3, #7 (vLLM 0.24), #11–#13 and the
   in-ComfyUI accounting fix; expect ~7–10 campaigns/h at 81 f after SAFE.
2. **Campaign batches**: rent marketplace RTX 5090 (first choice, native NVFP4 for a later
   LTX nvfp4 test) or RTX 4090 pods in blocks of ≥ 25 campaigns with weights on a persistent
   volume (RunPod network volume ~$6/mo for 80 GB; Vast has no cross-host volume). Same
   ComfyUI workflows; flip `CLIPLoader` 387 to `device: default` (templated by an
   `HW_PROFILE`), keep int8 LTX until the nvfp4 A/B passes, LLM on a per-token API
   (OpenRouter Qwen3.6-35B-A3B $0.10/$0.95 per M, image input supported) unless the client
   contract forbids data leaving the box.
3. **RTX PRO 6000 Blackwell 96 GB** ($0.66–0.93/h) if the LLM must stay self-hosted: all
   three models resident (LTX 38 + FLUX 17 + Qwen ~28 GB), no family switch, ties the 4090
   at ~$0.03/campaign.
4. **Do not rent**: H100/H200/B200 (1.5–2× faster, 6–12× the price → $0.07–0.15/campaign),
   L4/A10G/rented Spark (GB10-class speed at $0.32–1.01/h), serverless per-second (3–5×
   the pod price), reserved contracts below ~75 % duty (≥ ~550 rented h/month).
5. **Spot** once stage rows + blob-stored intermediates exist (plan phase B): $0.08–0.15/h,
   interruption tax ~+5 %.
6. Caveats: every per-host LTX number is community-anchored; the 24/32 GB encoder swap is
   unmeasured; Vast $0.27 rows are marketplace lows. **Run the 1-hour bench (§10 #8)
   before quoting.**

---

## 9. Quality gate to adopt before shipping any cut

Today's instruments (absolute 0–10 VLM rubric on 4 frame-centre PNGs, pass/fail QA,
binary has_text, golden_run forcing Wan 5B draft, 0 human ratings) cannot certify "a little
quality loss": in experiments A–C the judge scored 7–9 for everything and chose the second
position in 7/8 and 8/10 pairwise calls; only the native-resolution crop pairwise separated
4/6/8 FLUX steps. Adopt, in this order:

1. **Seed-noise baseline (A-vs-A).** Render the unchanged config twice with different seeds
   on 8–16 briefs per family; the resulting win-rate spread, ΔHPSv2 σ, Δflicker σ and defect
   rate define "small". Report every later cut in σ_seed units. One-time ~4 min FLUX + ~28 min
   LTX GPU.
2. **Pairwise, order-swapped VLM judge** (both orders; a win only when both agree; ties
   otherwise) replacing absolute scores for ship/no-ship. 8–12 frames at ≤ 448–672 px per
   arm for video (same token budget as 4 full-res frames, sees temporal defects); native-res
   crops for image sharpness. Null calibration: A-vs-A identical files must give ~100 % tie;
   a known-bad arm (FLUX 3 steps; LTX no-refine + 576 wide) must lose ≥ 80 %.
3. **Sample-size rule.** N = 8 is a smoke test only (rejects a true-parity cut 36 % of the
   time, ships a true-35 % cut 29 %). FLUX cuts: N ≥ 24 pairs (~12 min GPU). LTX cuts: SPRT
   (H0 p = 0.5 vs H1 p = 0.35, α 0.1, β 0.2 → each B-win ×0.7, loss ×1.3, stop at LR ≤ 0.22
   or ≥ 8), typically 10–14 pairs, max 32 (~56 min GPU at 81 f, run A-arm then B-arm with
   the family resident).
4. **Hard-defect axis, separate from preference.** Video QA pass/fail on both arms + a CPU
   OCR text detector (PP-OCRv5 / EasyOCR with uk/ru) on images; ship only if
   fail(B) ≤ fail(A) + 1/N.
5. **Cheap proxies as paired deltas** (never absolute thresholds): HPSv2 / PickScore
   (CLIP ViT-H, 1.25 GB, 50–100 ms) for images; VBench custom-input subset (subject/background
   consistency, imaging quality) + a 20-line inter-frame flicker metric for video; require
   mean Δ ≥ −0.5 σ_seed and ≤ 10 % of pairs below −2 σ_seed. Do not install full VBench
   (detectron2 pins CUDA ≤ 12.1); vendor the backbones.
6. **Anchored 0–5 rubric with exemplar images** (from rated assets) for the dashboards, and
   a 1-in-10 mandatory human star rating of shipped assets to grow `asset_ratings` from 0;
   monthly judge-vs-human Spearman with a drift alarm (< 0.5).
7. **Ship rules.** Keep-quality cut: bit/PSNR-identical, or SPRT accepts parity, or observed
   win-rate ≥ 45 % with N ≥ 24. Trade-quality cut: Wilson-90 lower bound ≥ 0.30 AND defect
   axis flat AND the $/campaign saving ≥ the owner's price for the lost preference. Re-gate
   the combined LITE config once at the end (knobs interact).
8. **Fix `golden_run`** to render the campaign stack (it forces `quality='draft'` = Wan 5B),
   keep frames + seeds, and compare pairwise with Wilson CI instead of Δmean; make it the
   nightly regression in the studio lane.

Gate cost is 1–3 % of the render cost it certifies; a 15 s/short LTX cut pays for its gate
after ~100 campaigns, a 3.5 s/image FLUX cut after ~20.

---

## 10. Next experiments (ordered)

1. **Negative-prompt removal A/B** (1 h; ~10 min GPU): cold render (FLUX in between) with
   the shipped vs modified JSON; expect one ~57 s encode span, identical frame hashes,
   FLUX→LTX +93 → ~40 s. Ship immediately on pass.
2. **Container recreate bundle** (operator; bench per plan 5.3): pd-comfyui with
   `LD_PRELOAD` tcmalloc + `OMP_NUM_THREADS=10` + `--cpuset-cpus 5-9,15-19` +
   `--use-flash-attention` + CUDA cache volume; vLLM at 0.24/8k/16 seqs (+ swapoff). Measure
   the live node-364 span on one cold and three warm renders WITH whisper/TTS/ffmpeg running;
   grep `loaded partially` == 0; MemFree/RSS before/after; FLUX and LTX s/it vs expA/expB.
   Save `te_bench2.log` for the standalone reference.
3. **De-pad custom node** (0.5 d): cosine check on 20 campaign prompts, then a same-seed
   render A/B (PSNR > 40 dB), then the live encode span (target ~9–27 s depending on #2).
4. **121-frame stage split** (the spec; ~15 min GPU): same keyframe/prompt/seed at 121 f,
   tiled decode, MemAvailable at 1 Hz; then tile 1024 / temporal 96 and untiled decode
   memory at 121 f. Re-seed the cost model's p90_121.
5. **Build the §9 gate** (2 d) and run it on the cheap rungs in this order: refine-2,
   no-audio, base-6 (one LTX SPRT run each, seeds shared), FLUX 8/6/4 steps and 576×1024
   keyframes (one N=24 run), then 121 vs 81 vs 49 f with real VO + human seam rating.
6. **End-to-end reference campaign bench with cost accounting** (plan 5.3/5.10): log
   gpu_busy_s, switch_s, partial-load count, r_i (text re-roll, QA re-render), c_i, U per
   campaign; nightly C_actual; alert on |formula − actual| > 10 %. Nothing in this document
   has been measured as a whole campaign.
7. **Multi-photo campaign** to exercise the photo-keyframe branch: `ref_image_source`,
   zero FLUX loads between LTX renders, cover-crop framing judged, label legibility.
8. **1-hour rental bench**: one Vast/RunPod 5090 and one 4090 (~$0.60 total), optionally
   one PRO 6000: same workflows with `CLIPLoader device=default`, per-stage s/it, load
   times from a network volume vs HF download, `loaded partially` = 0, $/campaign via §7;
   also int8 vs nvfp4 LTX on the 5090 (judged for i2v product fidelity).
9. **In-ComfyUI memory accounting** (psutil MemAvailable gated ≥ 36 GB) vs DynamicVRAM
   (comfy-aimdo integrated path): three consecutive renders across a FLUX→LTX switch with no
   partial load and no earlyoom line; then the `device: default` Gemma test (worth ~5–9 s/
   short after #2/#3, so low priority).
10. **VAE decode tail** research: bf16 vs fp32 VAE dtype, cudnn autotune (`--fast autotune`),
    fewer-frame subset decode for QA, PyAV frame-copy profile.
11. **Judge instrumentation**: persist judge_scores / QA verdicts / has_text per asset into
    a `judge_verdicts` table (needed by §9 #6, the sequential-candidate threshold and any
    later distilled scorer); vision payload at 672/448 px A/B.
12. **Product**: SKU composition + per-SKU cost seed; `deliver_by` overnight tier;
    remix/slideshow tier behind client approval; client-side compositing spike.
