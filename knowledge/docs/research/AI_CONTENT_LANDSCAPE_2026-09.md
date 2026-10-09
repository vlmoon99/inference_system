# AI content landscape for this app — what shipped, what is usable, what to do (2026-09-27)

Scope: open-weight image / video / TTS / LLM / translation models released or re-licensed since
the stack was fixed (Qwen3.6-35B-A3B NVFP4 on vLLM :8010, FLUX.1-dev fp8 + Hyper 8-step via
ComfyUI :8102, LTX-2.5 22B distilled int8 + Gemma-4-12B encoder :8104, RadTTS uk / F5-TTS ru /
kokoro en, bge-m3, MetricX-24 gate), plus what the commercial content tools shipped this year.
Hardware frame: DGX Spark GB10 (ARM sbsa, 122 GB unified, Blackwell sm121) and the optional
3× RTX 3090 24 GB box. Ukraine-first (uk/ru/en). Anything non-commercial (NC) is excluded.

Tags, as in `INFERENCE_COST_IDEAS.md`: **[P]** read on the primary source (model card, licence
file, vendor blog, GitHub README), **[C]** claimed by a secondary source only, **[U]** unverified /
could not confirm. Every row cites the URL the claim was read from. Nothing below was measured on
this box; hardware-fit numbers are arithmetic (params × bytes) unless the card states them.

Fit rule of thumb used in the tables: bf16 = 2 B/param, fp8/int8 = 1 B/param, NVFP4/GGUF-Q4 ≈ 0.55
B/param; add the text encoder and ~15 % activations. "Spark" = fits in 122 GB with the LLM
resident; "3090" = fits in 24 GB with reasonable offload.

---

## 0. The five things that matter

1. **FLUX.1-dev is non-commercial and always was; BFL re-tightened it on 2025-11-25.** Serving it in
   a paid product needs a paid self-hosted licence [P]. Every Apache-2.0 replacement now beats it
   on speed and equals it on quality: Qwen-Image-2512 (20B), Z-Image-Turbo (6B), FLUX.2 klein 4B.
2. **Qwen's image line split.** 1.x (Qwen-Image, Edit-2509/2511, 2512) is Apache-2.0; Qwen-Image-2.0
   (Feb 2026) was API-only; Qwen-Image-2.1 (2026-09-20, 7B, RGBA) is **Qwen Research License,
   non-commercial** [P]. Use 2512 + Edit-2511, not 2.1.
3. **Video: LTX-2.5 (2026-08-11) is the right open choice and already deployed;** its licence is free
   below $10 M annual revenue [P]. Wan open weights stopped at 2.2; 2.5/2.6/2.7 are API-only [P].
4. **Translation: Tencent Hy-MT2 (2026-05-21, Apache-2.0, 1.8B/7B/30B-A3B, uk+ru listed) is the
   first serious open MT family with a commercial licence** [P]; the running bake-off is the right
   experiment. No MetricX-25 weights exist; keep MetricX-24.
5. **No new Ukrainian TTS.** Qwen3-TTS, Chatterbox, CosyVoice 3 all add Russian, none add Ukrainian
   [P]. RadTTS stays the uk engine; the only defensible upgrade is Russian (Qwen3-TTS 1.7B).

---

## 1. Qwen image models

| Model | Released | Source | Licence | Size / fit | ComfyUI / vLLM | What it would change here |
|---|---|---|---|---|---|---|
| Qwen-Image | 2025-08-04 | https://huggingface.co/Qwen/Qwen-Image | Apache-2.0 [P] | 20B MMDiT + Qwen2.5-VL-7B encoder; fp8 20.4 GB + 7B fp8 encoder (docs.comfy.org) → Spark yes; 3090 yes with offload ("86 % of 24 GB", https://docs.comfy.org/tutorials/image/qwen/qwen-image) | native since 2025-08-05 (https://github.com/QwenLM/Qwen-Image) | Licence-clean drop-in for FLUX.1-dev; best open text rendering, but EN/ZH only — see Cyrillic note |
| Qwen-Image-Edit-2509 / **2511** | 2025-09-22 / 2025-12-23 | https://huggingface.co/Qwen/Qwen-Image-Edit-2511 | Apache-2.0 [P] | 20B; same footprint as above | native (https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit-2511) | Product-photo edits: background swap, relight, multi-angle from one reference, identity-preserving people |
| **Qwen-Image-2512** | 2025-12-31 | https://huggingface.co/Qwen/Qwen-Image-2512 | Apache-2.0 [P] | 20B; as above | same weights layout as Qwen-Image; Lightning 4-step LoRA 2026-01-01 (https://github.com/ModelTC/LightX2V-Qwen-Image-Lightning) | The candidate to replace FLUX.1-dev: "more realistic humans, less AI look, improved text" (card) |
| Qwen-Image-2.0 | 2026-02-10 | https://github.com/QwenLM/Qwen-Image (news), report https://arxiv.org/abs/2605.10730 | API-only, no weights [C] (https://webkul.com/blog/qwen-image-2/) | 7B | — | Nothing; not downloadable |
| Qwen-Image-2.1 | 2026-09-20 | https://huggingface.co/Qwen/Qwen-Image-2.1 · licence https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE | **Qwen Research License: "FOR NON-COMMERCIAL PURPOSES ONLY"**, commercial via model-business@notice.qwencloud.com [P] | 7B DiT, native 2K, RGBA, up to 10 refs | native in ComfyUI v0.37.0 (https://blog.comfy.org/p/qwen-image-21-in-comfyui-open-weight) | **Excluded** by licence. The native-alpha cut-outs would have been the useful part |
| Lightning LoRAs (lightx2v) | 2025-08 → 2026-01 | https://github.com/ModelTC/LightX2V-Qwen-Image-Lightning · https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning | Apache-2.0 [P] | 4- and 8-step LoRAs for Qwen-Image, Edit-2509, Edit-2511 (fused fp8 checkpoint), 2512 | ComfyUI native | 4 steps instead of 40–50; comparable to our Hyper-8 FLUX setup |

**Cyrillic text in images.** No primary source claims Cyrillic support. The Qwen-Image card says
"especially Chinese" plus alphabetic English [P]; issue #161 reports garbled Polish diacritics
(https://github.com/QwenLM/Qwen-Image/issues/161) [P]; the 2.0 report speaks of "multilingual
typography" without a language list [P]. HiDream-O1 (§2) reports LongText-Bench EN/ZH only.
Conclusion: `backend/ads/pipelines/overlay.py` (Pillow, Montserrat with full Ukrainian Cyrillic) stays
the only text renderer; pick the image model for photos, not lettering.

**Quantised variants.** Official = bf16 only. Community fp8 (`qwen_image_fp8_e4m3fn`, ComfyOrg
repack), GGUF and NVFP4 exist; the 2.1 community write-up ranks int8 ConvRot > MXFP8 > fp8 for
latent fidelity [C] (https://talkaiwith.substack.com/p/qwen-image-21-every-quantisation). Same
lesson as LTX: prefer int8-convrot over on-the-fly fp8 on the Spark.

**Versus FLUX.1-dev.** No primary head-to-head. 2512 "ranked strongest open-source model on AI
Arena" per its own news entry [P-vendor]. Treat as "at least as good"; verify with the Exp-B
protocol (seed 12345, same prompt, VLM pairwise) before switching.

---

## 2. Other open-weight image models and the licence map

| Model | Released | Source | Licence | Size / fit | ComfyUI | Change here |
|---|---|---|---|---|---|---|
| FLUX.1-dev (current) | 2024-08 | https://bfl.ai/legal/non-commercial-license-terms · https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/LICENSE.md | **FLUX [dev] Non-Commercial License v2.0, updated 2025-11-25**; commercial use "must request a license… may be subject to a fee, royalty or other revenue share"; content filter now mandatory [P] | 12B | native | **Must go or be licensed** (self-serve portal https://bfl.ai/licensing, price not public [P]) |
| FLUX.1-schnell | 2024-08 | https://huggingface.co/black-forest-labs/FLUX.1-schnell | Apache-2.0 [P] | 12B, 1–4 steps | native | Licence-clean but older quality; the "schnell NIM fallback" in `docs/MODELS.md` is the only Apache piece of the current image stack |
| FLUX.1 Kontext dev | 2025-06 | https://bfl.ai/announcements/flux-1-kontext-dev | Non-commercial [P] | 12B | native | Excluded |
| FLUX.2 [dev] | 2025-11-25 | https://huggingface.co/black-forest-labs/FLUX.2-dev · https://bfl.ai/blog/flux-2 | **FLUX Non-Commercial License** [P] (the blog fetch that said "Apache" was wrong; the card and https://github.com/black-forest-labs/flux2/blob/main/model_licenses/LICENSE-FLUX-DEV say NC) | 32B + Mistral-3 24B VLM encoder: bf16 ≈ 64 + 48 GB → Spark yes, 3090 no | native fp8 (NVIDIA/Comfy) | Excluded |
| **FLUX.2 [klein] 4B** | 2026-01-15 | https://huggingface.co/black-forest-labs/FLUX.2-klein-4B · https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence | **Apache-2.0** [P] | 4B, distilled 4 steps 8.4 GB / base 50 steps 9.2 GB (https://blog.comfy.org/p/flux2-klein-4b-fast-local-image-editing); Qwen 2.5 3B encoder (comfy) vs "Qwen3 8B" (bfl blog) — encoder size **[U]** | native (https://docs.comfy.org/tutorials/flux/flux-2-klein) | Sub-second T2I **and multi-reference edit** in one model; the cheapest edit engine for the 3090 box |
| FLUX.2 [klein] 9B | 2026-01-15 | https://huggingface.co/black-forest-labs/FLUX.2-klein-9B | FLUX Non-Commercial [P] | 9B | native | Excluded |
| **Z-Image-Turbo** | 2025-11-26 | https://huggingface.co/Tongyi-MAI/Z-Image-Turbo · https://github.com/Tongyi-MAI/Z-Image | Apache-2.0 [P] | 6B single-stream DiT, 8 NFE, "16 GB consumer" [P] → 3090 yes | native (https://docs.comfy.org/tutorials/image/z-image/z-image-turbo) | Fastest licence-clean photoreal model; EN/ZH text only |
| Z-Image (base) / Edit / Omni-Base | 2026-01-27 base; Edit & Omni listed, release **[U]** | https://github.com/Tongyi-MAI/Z-Image | Apache-2.0 [P] | 6B | native for base | Fine-tuneable base if a brand-style LoRA is wanted later |
| HiDream-I1 | 2025-04-07 | https://blog.comfy.org/p/hidream-i1-native-support-in-comfyui | MIT [C] (comfy blog) | 17B MoE DiT; fp8/GGUF/nf4 repacks; 3090 with offload | native | Strong prompt adherence; superseded in text rendering by O1 |
| HiDream-O1-Image-Dev | 2026-05-08 | https://huggingface.co/HiDream-ai/HiDream-O1-Image-Dev | MIT [P] | 8B pixel-native "UiT", no VAE/encoder; 28 steps | Transformers only; ComfyUI **[U]** | LongText-Bench EN 0.979 / ZH 0.978 [P]; worth a text-heavy poster test once a Comfy node exists |
| HunyuanImage 3.0 (+Instruct/Distil 2026-01-26) | 2025-09-28 | https://huggingface.co/tencent/HunyuanImage-3.0 | Tencent Hunyuan Community: commercial OK, **void in EU/UK/South Korea**, 100 M MAU cap, no training other models on outputs [P via HunyuanVideo-1.5 LICENSE] | 80B-A13B: bf16 160 GB → Spark **no**; NF4 community ≈ 45 GB → Spark yes, 3090 no | community nodes only | Too heavy for the win; skip |
| SD 3.5 Large | 2024-10 | https://huggingface.co/stabilityai/stable-diffusion-3.5-large | Stability Community (free < $1 M revenue) [P] | 8B | native | No reason over Z-Image / Qwen |
| Lumina-Image 2.0 | 2025-03 | https://huggingface.co/Alpha-VLLM/Lumina-Image-2.0 | Apache-2.0 [P] | 2B | — | Not competitive; no Lumina 3 found **[U]** |
| Kolors 2 | — | — | — | — | — | **Not found** as an open release [U] |

---

## 3. Open-weight video models

| Model | Released | Source | Licence | Size / fit | Audio / I2V | Change here |
|---|---|---|---|---|---|---|
| LTX-2 | 2026-01-06 | https://github.com/Lightricks/LTX-2 · licence https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x | LTX-2.x Community: free below **$10 M annual revenue**, paid above [P] | — | native synced audio + video, I2V | Superseded by 2.5 |
| **LTX-2.5** (current) | **2026-08-11** (https://comfyui-wiki.com/en/news/2026-08-11-ltx-2-5-open-weights-release [C]; the HF card shows the family date 2026-01-06 [P]) | https://huggingface.co/Lightricks/LTX-2.5 | same Community licence [P] | 22B dev + distilled; bf16, ComfyUI int8, **NVFP4** transformer variants; Gemma-4-12B encoder bf16/int8 [P] | native audio, multishot, diffusion decoder, duration head | Already deployed. NVFP4 transformer is the untested lever (memory: LTX cold peak was 108 GB) |
| Wan 2.2 (TI2V-5B, T2V/I2V-A14B, S2V-14B, Animate-14B) | 2025-07-28 → 2025-11 | https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B · https://huggingface.co/Wan-AI | Apache-2.0 [P] | 5B: 24 GB; A14B: 27B total | I2V yes; S2V = audio-driven | Still the campaign default (memory notes); nothing newer is open |
| Wan 2.5 / 2.6 / 2.7 | 2025-09 → 2026 | https://huggingface.co/Wan-AI (org listing: newest = Wan2.2-Animate, 2025-11) | **API-only, no weights** [P org page; C for 2.6/2.7 features] | — | — | Nothing to self-host |
| HunyuanVideo 1.5 | 2025-11-20 | https://huggingface.co/tencent/HunyuanVideo-1.5 · licence https://huggingface.co/tencent/HunyuanVideo-1.5/blob/main/LICENSE | Hunyuan Community: commercial OK, **not EU/UK/KR**, 100 M MAU, "identify machine-generated content" [P] | 8.3B DiT, 14 GB with offload → 3090 yes | I2V 480p/720p; no audio | Cheap I2V for the 3090 box; Ukraine is outside the excluded territories; EU customers would be a problem |
| SkyReels-V3 (R2V/V2V-14B, A2V-19B) | 2026-01-29 | https://huggingface.co/Skywork/SkyReels-V3-A2V-19B | "Skywork License" — terms **[U]** | 19B, fp8 + block offload "< 24 GB" [P] | A2V = portrait + audio → talking avatar, 200 s | Candidate for spokesperson shorts if the licence allows commerce |
| CogVideoX, Mochi, HunyuanVideo 2 | — | — | — | — | — | No 2026 release found **[U]**; nothing to act on |

---

## 4. Open TTS for uk / ru / en (2025-09 → 2026-09)

| Model | Released | Source | Licence | uk / ru / en | Cloning / control | Change here |
|---|---|---|---|---|---|---|
| **Qwen3-TTS** 0.6B/1.7B | 2026-01-22 | https://github.com/QwenLM/Qwen3-TTS | Apache-2.0 [P] | **no / yes / yes** (10 languages) [P] | 3-s clone; VoiceDesign by instruction; 97 ms streaming; vLLM day-0 (offline) [P] | Best licence-clean **Russian** upgrade over F5-TTS; prosody by instruction, no explicit stress marks |
| Chatterbox Multilingual V3 | 2025–2026 (undated) | https://github.com/resemble-ai/chatterbox | MIT [P] | **no / yes / yes** (23 languages) [P] | 10-s clone; emotion exaggeration; PerTh watermark on every clip [P] | ru alternative; the mandatory watermark is a product decision |
| Fun-CosyVoice 3 0.5B | 2025-12 | https://github.com/FunAudioLLM/CosyVoice | Apache-2.0 [P] | **no / yes / yes** (9 languages) [P] | zero-shot + cross-lingual clone, instruct (emotion/speed), 150 ms streaming | Smaller ru alternative |
| Fish Audio S2 Pro 4B | 2026 (report arXiv 2603.08823) | https://github.com/fishaudio/fish-speech · https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md | **Fish Audio Research License — NC**, commercial via business@fish.audio [P] | yes (80+ incl. uk) / yes / yes | 10–30 s clone | **Excluded**; the only model listing Ukrainian |
| Higgs Audio v3 4B | 2026 | https://github.com/boson-ai/higgs-audio | Research & Non-Commercial [P] | "100+" [U] | zero-shot clone | Excluded |
| IndexTTS 2.5 | 2026-08-10 | https://github.com/index-tts/index-tts | bilibili Model Use License [P] | zh/en/ja/es/ar only | duration + emotion control | No uk/ru; skip |
| VibeVoice TTS 1.5B / Realtime; **ASR-Streaming** 2026-09-03 | 2025–2026 | https://github.com/microsoft/VibeVoice | MIT [P] | TTS en/zh; ASR "50+ languages" [P] | 4-speaker dialogue | ASR side may serve subtitles / QA; not a uk voice |
| Dia 1.6B / Dia2 | 2025 | https://github.com/nari-labs/dia | Apache-2.0 [P] | en only | clone | No |
| Orpheus multilingual | 2025-04 preview | https://github.com/canopyai/Orpheus-TTS | Apache-2.0 [P] | language list not on README **[U]** | zero-shot clone | Check the 7 language pairs; likely no uk |
| Kokoro-82M v1.0 (current en) | 2025-01-27 | https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md | Apache-2.0 [P] | **no / no / yes** [P] | none | Unchanged; no 2026 release |
| Qwen3-Omni-30B-A3B | 2025 | https://huggingface.co/Qwen/Qwen3-Omni-30B-A3B-Instruct | Apache-2.0 [P] | speech out 10 languages incl. ru, not uk [P] | — | Too heavy for a voice; no uk |
| robinhad/ukrainian-tts (ESPnet) | ongoing | https://github.com/robinhad/ukrainian-tts | MIT [P] | uk | stress queue: acute > user > dictionary > model [P] | Same family as the RadTTS/наголос chain already in `inference/adapters/gen-tts-ukrainian`; no new checkpoints found |

Net: **no open commercially-usable model added Ukrainian in the last twelve months.** The uk
roadmap in `UKRAINIAN_TTS_ROADMAP.md` (own data, RadTTS) stands. For Russian, Qwen3-TTS 1.7B is the
one to A/B against F5-TTS.

---

## 5. LLMs for a 122 GB unified-memory box

| Model | Released | Source | Licence | Total / active; NVFP4 on Spark | vLLM on GB10 | Fit as writer (uk/ru copy) + judge |
|---|---|---|---|---|---|---|
| Qwen3.6-35B-A3B (current) | 2026-04 | https://huggingface.co/Qwen/Qwen3.6-35B-A3B · NVFP4 https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4 | Apache-2.0 [P] | 35B/3B; NVFP4 ≈ 20 GB | recipe https://recipes.vllm.ai/Qwen/Qwen3.6-35B-A3B: vLLM ≥ 0.28, `--moe-backend marlin`, `--gpu-memory-utilization 0.5`, 97.7 tok/s decode measured 2026-08-31 [P] | Baseline |
| Qwen3.7 | 2026-05-20 (Max, API) | https://codersera.com/blog/qwen-3-7-vs-qwen-3-6-2026/ [C] | closed | — | — | No open weights |
| **Qwen3.8-27B** (dense, VL) | 2026-08-14 | https://huggingface.co/Qwen/Qwen3.8-27B · NVFP4 https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4 | Apache-2.0 [P] | 27B dense; NVFP4 ≈ 16 GB, official FP8 27 GB | NVIDIA card "tested on GB300"; GB10 **[U]** | Strongest Apache Qwen for judging/vision; dense = slower decode than 35B-A3B on Spark |
| Qwen3.8-Flash-Next | 2026-08-26 | https://huggingface.co/Qwen/Qwen3.8-Flash-Next | "Qwen Community 1.0" — terms **[U]** | 125B/6B (51B n-gram embeddings); NVFP4 ≈ 70 GB | new arch (sparse attn, MTP) — kernel support on sm121 **[U]** | Wait for licence text + GB10 reports |
| **Gemma 4** 31B / 26B-A4B / 12B / E4B / E2B | 2026-04-02 | https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/ · https://huggingface.co/google/gemma-4-26B-A4B-it | **Apache-2.0** (+ Gemma terms) [P] | 31B dense bf16 62 GB / NVFP4 ≈ 18 GB; 26B-A4B bf16 50 GB | vLLM instructions on card [P]; NVFP4 checkpoints **[U]** | "140+ languages" [P]; the Gemma-3 line produced Lapa and MetricX-25 — the most credible uk/ru writer candidate; the 12B is already resident as the LTX encoder family |
| Mistral Small 4 | 2026-03-16 | https://mistral.ai/news/mistral-small-4 | Apache-2.0 [P] | 119B/6B; NVFP4 ≈ 65 GB → Spark yes, tight beside LTX | vLLM listed [P]; NVFP4 checkpoint **[U]** | Reasoning + vision in one; uk quality unknown |
| gpt-oss-120b | 2025-08 | https://github.com/openai/gpt-oss | Apache-2.0 [P] | 117B/5.1B MXFP4 ≈ 65 GB | forum: vLLM MXFP4 slower than llama.cpp on GB10 (https://forums.developer.nvidia.com/t/vllm-on-gb10-gpt-oss-120b-mxfp4-slower-than-sglang-llama-cpp-what-s-missing/356651) [P] | English-centric; weak case for uk |
| Nemotron 3 Super 120B-A12B NVFP4 | 2026 | https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 · https://vllm.ai/blog/2026-06-01-vllm-dgx-spark | NVIDIA Nemotron Open Model License, "ready for commercial use" [P] | 120B/12B NVFP4 ≈ 70 GB | 22.7–23.7 tok/s decode on one Spark [P] | Too slow and too big next to LTX |
| DeepSeek V4-Flash / V4-Pro | 2026-04-26 | https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash | MIT [P] | 284B/13B "FP4+FP8 mixed" ≈ 150+ GB → **Spark no** | — | No |
| GLM-5 / 5.2 (744B), Kimi K2.6 (1T), MiniMax M2.5 / M3 | 2026 | https://huggingface.co/blog/mlabonne/glm-5 · https://huggingface.co/moonshotai/Kimi-K2.6 · https://huggingface.co/MiniMaxAI/MiniMax-M2.5 | MIT / modified MIT / custom [C] | all > 200 GB at 4-bit | — | No |
| Llama 4.x / 5 | — | https://huggingface.co/meta-llama (newest: Llama-4 2025-05) | Llama Community | — | — | **No Llama 5 on the org page; blog claims conflict [U]** |

**vLLM on GB10 state.** Docker `cu130` track recommended (https://vllm.ai/blog/2026-06-01-vllm-dgx-spark);
plain PyPI aarch64 wheels reported working in 2026-08 [C]; NVFP4 MoE needs `--moe-backend marlin`
on sm121, FlashInfer CUTLASS paths improving, AWQ-4bit was still faster than NVFP4 in the
Dec-2025 forum baseline (https://forums.developer.nvidia.com/t/psa-state-of-fp4-nvfp4-support-for-dgx-spark-in-vllm/353069) [P].

**Recommendation.** Keep Qwen3.6-35B-A3B as the throughput writer. Run one bake-off for the
*judge/translator/long-copy* slot between Gemma-4-31B (NVFP4 if a checkpoint appears, else FP8 at
31 GB) and Qwen3.8-27B-NVFP4, scored by the existing MetricX gate + owner sample from
`backend/scripts/translate_bakeoff.py`. Two resident LLMs (20 + 18 GB) still leave ~80 GB for LTX.

---

## 6. What the commercial tools shipped in 2026 (feature bar for a small-business content manager)

| Vendor | Shipped (2026) | Source |
|---|---|---|
| Jasper | Jasper Agents; GEO Agent + GEO Hub (06-16); **Translation Agent** (brand-voice MT); Product IQ (08-24); MCP connector (08-18); Jira/Webflow/GSC/Semrush integrations | https://www.jasper.ai/blog [P] |
| Canva | "Canva AI 2.0" research preview (04): conversational design, editable layered output, campaign creation, brand intelligence, **scheduled AI workflows**; Dream Lab style transfer | secondary only, newsroom blocked (403) [C] https://www.digitalapplied.com/blog/ai-social-media-management-tools-2026-comparison |
| Adobe | Creative Agent / Firefly AI Assistant (04-15): multi-step workflows across apps, "batch content generation across social channels from a single prompt", Precision Flow, AI Markup; Kling 3.0 added to the 30+ partner models | https://news.adobe.com/news/2026/04/adobe-new-creative-agent [P] |
| Meta | Advantage+ Creative Suite; Video Generation 2.0 (images → multi-scene video + text + music); Muse Image model rolling in Q3 (announced 07-07); stated goal "full automation from a URL or product image" | secondary [C] https://hawky.ai/blog/meta-ads-updates-2026 · https://benly.ai/learn/meta-ads/advantage-plus-updates-2026 |
| Google Ads | GML 05-20: Asset Studio on Gemini + Veo (+ "Nano Banana"), brief → storyboard → video, **URL → image assets**, one-click A/B; AI Max; "Ask Advisor" agent | https://blog.google/products/ads-commerce/google-marketing-live-2026-collection/ [P] · https://business.google.com/us/accelerate/announcements/multimodal-video-creation-in-asset-studio/ |
| HeyGen | Avatar V (04-08): 15-s recording → consistent multi-angle avatar; Seedance 2.0 integration; "177+ languages" platform-wide | https://www.heygen.com/blog/announcing-avatar-v [P] |
| Mirage (ex-Captions) | rebrand to an AI lab; **product URL → video ad**; $75 M raise (03-24) | https://techcrunch.com/2026/03/24/mirage-raises-75m-to-continue-building-models-for-its-ai-video-editing-app-captions/ [P] |
| Runway | Gen-4.5 (2025-12): 720p T2V/I2V, all control modes | https://runway.com/research/introducing-runway-gen-4.5 [P] |
| Hootsuite / Buffer / Predis / Ocoya | OwlyGPT image gen bundled; Buffer AI unlimited on all plans; Predis predictive engagement + auto-post (paid tiers); Ocoya Shopify/WooCommerce import → posts | secondary [C] https://buffer.com/resources/buffer-vs-hootsuite/ · https://predis.ai/resources/predis-ai-vs-ocoya/ |

**The 2026 expectation list** (every one of the above has at least three): product URL / photo →
full campaign; brand kit that the generator actually obeys; multi-language in one click; avatar or
spokesperson video from a short recording; music + captions on every short; predicted performance
before posting and **regeneration driven by measured performance**; scheduled, recurring agent
runs. This repo already has `pipelines/campaign.py`, `optimize.py`, `metrics_sync.py`, `reflect.py`,
`overlay.py`, `subtitles.py`; the gaps are music beds, spokesperson video and product-URL intake.

---

## 7. Translation for uk / ru

| Model | Released | Source | Licence | uk / ru | Fit | Change here |
|---|---|---|---|---|---|---|
| **Hy-MT2 1.8B / 7B / 30B-A3B** | 2026-05-21 | https://huggingface.co/tencent/Hy-MT2-7B · https://huggingface.co/tencent/Hy-MT2-30B-A3B · https://huggingface.co/tencent/Hy-MT2-1.8B | Apache-2.0 [P] | both listed (33 languages) [P] | 7B bf16 14 GB; 30B-A3B-FP8 ≈ 30 GB; vLLM docs on card | The bake-off in `backend/scripts/translate_bakeoff.py` is the right test; 30B-A3B-FP8 is the next rung if 7B loses to Qwen |
| Lapa LLM v0.1.2 (Gemma-3-12B, uk tokenizer) | 2025-11 | https://huggingface.co/lapa-llm/lapa-v0.1.2-instruct · https://github.com/lapa-llm/lapa-llm | card: Gemma terms; repo: MIT — **conflict [P both]**; both allow commerce | uk (33 BLEU FLORES en→uk claimed) [P-vendor] | 12B bf16 24 GB | uk-only specialist; 1.5× fewer tokens for uk. Worth a slot in the bake-off for en→uk |
| Seed-X 7B (Instruct/PPO) | 2025-07-18 | https://huggingface.co/ByteDance-Seed/Seed-X-PPO-7B | OpenMDW [P] (permissive; read the text before shipping **[U]**) | both (28 languages) [P] | 7B | Older; behind Hy-MT2 on their own numbers [C] |
| Tower-Plus 9B | 2025-06 | https://huggingface.co/Unbabel/Tower-Plus-9B | **CC-BY-NC-SA-4.0** [P] | both | — | Excluded |
| Qwen-MT / Qwen3-MT | — | https://huggingface.co/models?search=Qwen-MT (no Qwen-org repo) | API only | — | — | No open weights **[P]** |
| Gemma 4 as translator | 2026-04-02 | see §5 | Apache-2.0 | 140+ languages [P] | — | The general-model contender; same bake-off |
| MetricX-24 (current gate) | 2024-11 | https://github.com/google-research/metricx | Apache-2.0 [P] | — | mT5 XXL | Keep |
| MetricX-25 | paper 2025-10 (https://arxiv.org/abs/2510.24707) | — | — | — | Gemma-3 encoder backbone | **No weights on GitHub or HF** (search returns nothing) [P]; nothing to deploy |

---

## 8. Other genuinely new and relevant

| Area | Model | Released | Source | Licence | Fit / note |
|---|---|---|---|---|---|
| Music beds for shorts | **ACE-Step 1.5** | 2026-01-28 | https://github.com/ACE-Step/ACE-Step | Apache-2.0 [P] | 19 languages incl. ru (uk not listed); ≥ 8 GB; 4 min of music in ~20 s on A100 [P] |
| | **HeartMuLa-oss-3B** (+ MuLaCover 2026-09-16) | 2026-01-14 | https://github.com/HeartMuLa/heartlib | Apache-2.0 code + weights [P] | "almost all languages" for lyrics [P-vendor]; 7B announced, not out |
| | YuE 7B | 2025 | secondary [C] https://www.spheron.network/blog/deploy-open-source-ai-music-generation-gpu-cloud-2026/ | Apache-2.0 [C] | Slower AR model |
| Talking product / spokesperson video | InfiniteTalk (Wan2.1-I2V-14B) | 2025-08-19 | https://github.com/MeiGen-AI/InfiniteTalk | Apache-2.0 [P] | fp8 + full offload option; ComfyUI via kijai wrapper [P]; Spark yes, 3090 with fp8 |
| | Wan2.2-S2V-14B | 2025-08-26 | https://huggingface.co/Wan-AI/Wan2.2-S2V-14B | Apache-2.0 [P] | "min 80 GB single GPU" [P] → Spark only |
| | LatentSync 1.6 (lip-sync existing footage) | 2025-06-11 | https://github.com/bytedance/LatentSync | Apache-2.0 [P] | 18 GB → 3090 yes |
| | SkyReels-V3 A2V-19B | 2026-01-29 | see §3 | Skywork licence **[U]** | — |
| Product-photo OCR / intake | **PaddleOCR-VL-1.6** | 2026-05-28 | https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6 | Apache-2.0 [P] | 1B; 109 languages incl. Russian Cyrillic (uk not named) [P]; vLLM |
| | DeepSeek-OCR 3B | 2025-10-21 | https://huggingface.co/deepseek-ai/DeepSeek-OCR | MIT [P] | vLLM upstream |
| Subtitles / QA ASR | VibeVoice-ASR-Streaming | 2026-09-03 | https://github.com/microsoft/VibeVoice | MIT [P] | "who said what", 50+ languages [P]; uk unconfirmed **[U]** |

Agent frameworks: nothing found that changes the executor-agent design in `ads/worker`; the
vendors above ship "agents" as scheduled multi-step workflows over their own tools, which is what
`worker/queue.py` + `pipelines/*` already are.

---

## 9. What to do in this app — ranked by value ÷ effort

1. **Get off FLUX.1-dev (licence) — replace with an Apache image ladder.** Change
   `inference/adapters/gen-image/app.py` (`MODEL = "flux.1-dev-fp8"`, workflow `flux1_dev_api.json`,
   readiness check on `flux1-dev-fp8.safetensors`) to serve **Qwen-Image-2512 fp8 + Lightning
   4-step** as the quality tier and **Z-Image-Turbo (8 NFE)** as the fast tier; advertise both in
   `/health.capabilities.models`; mirror in `inference/openapi.yaml`, parse in `ads/providers/hosts.py`;
   update `docs/MODELS.md` and `weights.lock`/`parity.py` for the 3090 box. Expected gain: legal
   commercial use; 4-step Qwen ≈ our 8-step Hyper time [E]; Z-Image well under 5 s on the 3090.
   Risk: composition/style drift versus the FLUX look the owner calibrated on; the Hyper-LoRA
   scale lessons (Exp B) do not transfer. Prereqs: ~28 GB (2512 fp8 + Qwen2.5-VL-7B fp8 encoder),
   ~12 GB Z-Image + encoder; one Exp-B style pairwise run before flipping the default.
2. **Product-photo editing job kind (Qwen-Image-Edit-2511 + Lightning fp8 fused).** New `photo_edit`
   in `RUNNERS` (`ads/worker/queue.py`) + `hosts.REQUIREMENTS`, workflow in gen-image; unlocks
   background swap / relight / multi-angle from one customer photo — the "product-to-ad" feature
   every vendor in §6 now has. Gain: real photos instead of prompt-only renders; keeps the
   customer's actual product. Risk: 20B second model resident (fp8 20 GB) beside 2512 — fine on
   the Spark, alternate on the 3090. Prereq: 21 GB download; identity-preservation QA via the
   existing VLM critic (`pipelines/critic.py`).
3. **Finish the translator bake-off with three more rows:** Hy-MT2-30B-A3B-FP8, Lapa v0.1.2
   (en→uk only) and Gemma-4-31B; keep MetricX-24 as the gate (no MetricX-25 weights). Files:
   `backend/scripts/translate_bakeoff.py`, `ads/pipelines/translate.py` (`TRANSLATE_BASE_URL/MODEL`
   seam already exists), `deploy/spark/` unit for a second vLLM. Gain: the copy customers read in
   uk/ru. Risk: two LLM services on one box; Hy-MT2 needs vLLM "from source" per its card [P].
4. **Judge/long-copy LLM bake-off: Gemma-4-31B vs Qwen3.8-27B-NVFP4** (§5). Files:
   `ads/providers/llm.py`, `ads/core/config.py`, `deploy/spark/ads-vllm*.service`. Gain: a stronger
   judge for `critic.py`/`tts_qa.py` and better uk long-form; keep 35B-A3B for volume. Risk: dense
   27–31B decode is slower on GB10 than the A3B MoE (vLLM blog: "dense models are less aligned"
   with the Spark) [P]; NVFP4 Gemma checkpoint unverified. Prereq: 16–31 GB per model.
5. **Russian voice: A/B Qwen3-TTS 1.7B against F5-TTS.** `inference/adapters/gen-tts/engines.py`,
   `ads/providers/tts.py` engine map, `tts_qa.py` for scoring. Gain: instruction-controlled prosody,
   3-s cloning for a per-customer voice (`pipelines/voice_clone.py`). Risk: no explicit stress
   control; ru only — uk stays RadTTS. Prereq: 3.5 GB; vLLM offline mode only for now [P].
6. **Music beds for shorts (ACE-Step 1.5 or HeartMuLa-3B).** New adapter `inference/adapters/gen-music`
   (contract v1 `/generate`), job kind `music`, mixed in `pipelines/short.py` under the voiceover
   (loudness chain already there). Gain: matches the Meta/Google "music on every short" bar without
   licensed-library risk. Risk: ru/uk lyric quality unknown — use instrumental beds only; GPU time
   (~20 s on A100 per 4 min [P]) fits between waves. Prereq: ~10 GB.
7. **Spokesperson / product-explainer shorts (InfiniteTalk on Wan2.1-14B, Apache).** `pipelines/film.py`
   or a new `talking` job; the customer records 15 s (HeyGen's Avatar V bar). Gain: the highest-value
   format vendors ship this year. Risk: 14B I2V + audio model on the Spark competes with LTX for
   memory (LTX resident 105 s to load — scheduling cost); quality on uk speech unmeasured. Do after
   1–3.
8. **Intake OCR for product photos (PaddleOCR-VL-1.6, 1B, Apache).** `pipelines/intake.py` reads
   prices/labels/ingredients off customer photos into the brief; vLLM-served, ~2 GB. Gain: fewer
   wrong facts in copy. Risk: Ukrainian text quality unverified (only Russian Cyrillic listed) [P].
9. **LTX-2.5 NVFP4 transformer trial.** Same adapter (`inference/adapters/gen-video/app.py`); could
   cut the 108 GB cold peak and the 105 s load. Risk: fidelity vs int8-convrot (community ranking
   [C]); measure with Exp-A conditions. Prereq: the NVFP4 files from the LTX-2.5 card.
10. **Licence hygiene file.** Record per-model licence + territory clauses in `docs/MODELS.md`
    (LTX $10 M revenue ceiling; Hunyuan EU/UK/KR exclusion if ever used; Qwen-Image-2.1 and all
    FLUX dev/klein-9B/FLUX.2-dev as excluded), so the next model swap does not repeat the FLUX
    mistake. Zero GPU cost.

Not recommended: Qwen-Image-2.1 (NC), FLUX.2 dev / klein 9B / Kontext (NC), Fish S2 / Higgs v3 (NC),
Tower-Plus (NC), HunyuanImage 3.0 (80B, weak return), DeepSeek/GLM/Kimi/MiniMax (do not fit),
Wan 2.5+ (API only).
