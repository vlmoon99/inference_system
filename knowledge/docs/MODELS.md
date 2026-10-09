# Models — what runs, where, under which licence (2026-09-27)

Hardware is `inference/hosts/<name>/host.yaml` plus each adapter's `/health`
capabilities (CLAUDE.md "Where things are NOT"). This file is the licence and
role register: every weight file a served workflow loads is in
`inference/workflows/weights.lock`, and every Hugging Face repo in that lock
must have a row below (`tests/test_docs_consistency.py` enforces it). A row
marked **NC** may be used for prototyping and internal evaluation only; nothing
a paying customer receives may come from it until the licence column changes
(docs/research/AI_CONTENT_LANDSCAPE_2026-09.md §2).

## The served set on the Spark

| Role | Port | Model | Repo | Licence | Footprint | Notes |
|---|---|---|---|---|---|---|
| Copy, critics, judges, chat, translator (until the bake-off ships a winner) | 8010 | Qwen3.6-35B-A3B NVFP4, vLLM | nvidia/Qwen3.6-35B-A3B-NVFP4 | Apache-2.0 | ~20 GB weights + KV at `--gpu-memory-utilization 0.20` | KV was 10x oversized at 0.35; 0.20 since 2026-09-27 frees ~18 GB for the image ladder |
| Images, quality tier | 8102 | Qwen-Image-2512 fp8, Lightning 4-step fused, ComfyUI | lightx2v/Qwen-Image-2512-Lightning · Comfy-Org/Qwen-Image_ComfyUI (encoder, VAE) | Apache-2.0 | ~20 GB + 8 GB encoder | `qwen-image-2512` in the ladder; the owner picks the default in admin → AI → Image lab |
| Images, edit rung (product photo → variants) | 8102 | Qwen-Image-Edit-2511 fp8, Lightning 4-step fused, ComfyUI | lightx2v/Qwen-Image-Edit-2511-Lightning (+ the Qwen encoder and VAE above) | Apache-2.0 | ~20 GB (swaps with 2512, same encoder) | `qwen-image-edit-2511`; takes `ref_image_*`; never the text-to-image default |
| Images, prototype tier | 8102 | FLUX.1-dev fp8 + Hyper 8-step LoRA, ComfyUI | Comfy-Org/flux1-dev · ByteDance/Hyper-SD | **NC** — FLUX [dev] Non-Commercial License v2.0 (commercial use needs a paid BFL licence, bfl.ai/licensing) | ~17 GB | `flux1-dev-fp8`; loaded on demand; never the default for customer output without a licence |
| Shorts (video) | 8104 | LTX-2.5 22B distilled int8-convrot + Gemma-4 12B encoder on CPU, ComfyUI | Lightricks/LTX-2.5 | LTX-2.5 Community — free below $10M annual revenue | 21.5 GB transformer + 12 GB encoder; 108 GB cold peak | `ltx2`; Wan 2.2 5B/14B workflows kept for hosts that advertise them |
| Avatar (talking spokesperson) | 8106 | InfiniteTalk on Wan2.1-I2V-14B-480P fp8 + Lightx2v distill LoRA, umT5 fp8, wav2vec2 audio encoder, ComfyUI-WanVideoWrapper | Kijai/WanVideo_comfy · Comfy-Org/Wan_2.1_ComfyUI_repackaged · Kijai/wav2vec2_safetensors (upstream MeiGen-AI/InfiniteTalk, Wan-AI/Wan2.1) | Apache-2.0 (InfiniteTalk, Wan2.1); wav2vec2 MIT | ~25 GB resident with block swap; serialised with LTX through the video gate | one photo + the TTS voiceover → lip-synced video, 25 fps, up to 60 s; consent-gated photos only |
| Voiceover uk | 8105 | RAD-TTS++ (`mykyta`), engine of record for наголос control; StyleTTS2-Ukrainian (patriotyk/styletts2_ukrainian_single) for projects that pin their own voice | repo-owned under `data/models/{kokoro,piper,f5,styletts2}/` | NVIDIA RADTTS licence (research/eval terms — verify before revenue) · StyleTTS2 MIT | CPU/GPU small | `docs/research/UKRAINIAN_VOICE_IMPROVEMENT.md` |
| Voiceover ru | 8105 | F5-TTS | SWivid/F5-TTS | CC-BY-4.0 (code MIT; check the voice-reference terms) | small | Qwen3-TTS 1.7B (Apache) is the A/B candidate |
| Voiceover en | 8105 | Kokoro-82M | hexgrad/Kokoro-82M | Apache-2.0 | small | |
| Embeddings | 8011 | bge-m3 | BAAI/bge-m3 | MIT | ~4 GB | pgvector memory + digest relevance |
| Translation gate | 8012 | MetricX-24 hybrid large | google/metricx-24-hybrid-large-v2p6 | Apache-2.0 | ~5 GB CPU | threshold 7.0 |
| Frame interpolation | in ComfyUI | RIFE v4.26 | — | MIT | small | shorts → 48 fps, `RIFE=0` disables |

Decisions recorded elsewhere: the image ladder and why FLUX must go or be
licensed (research note §2, §9.1); the translator bake-off
(docs/research/TRANSLATOR_BAKEOFF_2026-09.md: Hy-MT2-7B, Apache-2.0, leads);
Spark memory peaks (docs/CAMPAIGN_THROUGHPUT_PLAN.md).

## Excluded by licence (do not propose for production output)

Qwen-Image-2.1 (Qwen Research License, NC) · FLUX.2 [dev], FLUX.2 klein 9B,
FLUX.1 Kontext dev (FLUX Non-Commercial) · Tower-Plus (CC-BY-NC) ·
Unbabel/wmt22-cometkiwi-da (CC-BY-NC-SA) · Fish Audio S2 Pro, Higgs Audio v3 (NC).

## Candidates on the bench (Apache unless noted)

* **Images, fast tier:** FLUX.2 klein 4B (also multi-reference edit; fits a 3090), Z-Image-Turbo 6B.
* **Product-photo edit:** Qwen-Image-Edit-2511 + Lightning fused fp8.
* **Judge / long uk copy:** Gemma-4-31B (Apache + Gemma terms), Qwen3.8-27B-NVFP4.
* **Translation:** Hy-MT2-30B-A3B-FP8, Lapa v0.1.3 (Gemma terms) — RTX box rows of the bake-off.
* **Voice ru:** Qwen3-TTS 1.7B. **Spokesperson video:** InfiniteTalk on Wan2.1-14B. **Music beds:** ACE-Step 1.5.

## Shorts realism chain (unchanged)

Photoreal keyframe (best-of-N with a VLM judge) + anti-AI negative prompt, LTX-2.5
i2v with a VLM output-QA seed-retry pass, GPU RIFE → 48 fps, ffmpeg film grain and
filmic grade, broadcast loudness on the voiceover. Knob reference:
[`SHORTS_OPTIMIZATION.md`](SHORTS_OPTIMIZATION.md).

## Rules

1. A new weight file goes into `weights.lock` **and** a row here, licence first.
2. NC rows are prototype-only. Flipping one into customer output is a licence
   purchase recorded in this table, not a config change.
3. Territory or revenue clauses (LTX's $10M ceiling; any EU/UK/KR exclusion if a
   Hunyuan model is ever used) are written in the Licence column, not remembered.
