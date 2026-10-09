# LTX-2.5 in the Video Studio (2026-08-23)

Lightricks **LTX-2.5** (22B distilled, int8-convrot) is the third model in the
Studio picker: `5b` (fast draft) | `14b` (max quality) | `ltx2`. It replaces
MiniMax-H3, which was removed the same day (weights deleted, workflows and
code stripped — the user judged it not worth 34GB against Wan and LTX).

## Why this model

- **License**: free commercial use under $10M/yr revenue (LTX Community
  license) — the tier we accepted for the prototype phase.
- **Synced audio+video in ONE pass** — the model generates its own ambient
  soundtrack/speech via a dedicated audio VAE. Unique among our models; the
  shorts pipeline still overlays voiceover/music on top.
- Distilled = few-step: 8 base steps + 3 refine steps (fixed ManualSigmas
  schedules), much lighter compute than Wan 14B's ladder.
- ComfyUI-native since 0.33 (our pd-comfyui runs 0.33.3).

## Files (in /home/server/ComfyUI/models/)

| File | Dir | Size |
|---|---|---|
| ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors | diffusion_models/ | 21.5GB |
| gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors | text_encoders/ | 15.4GB |
| ltx-2.5-video-vae-bf16.safetensors | vae/ | 1.5GB |
| ltx-2.5-audio-vae-bf16.safetensors | vae/ | 0.4GB |
| ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors | latent_upscale_models/ | 1.0GB |

Also downloaded but NOT wired (kept for experiments):
`ltx-2.5-22b-distilled-transformer-nvfp4.safetensors` (18.7GB) — the NVFP4
variant is not ComfyUI-prefixed; the official template uses int8-convrot.

The HF repo is **auto-gated**: access was accepted for the vlmoon99 account
(POST /Lightricks/LTX-2.5/ask-access with the stored token).

## Workflow provenance

`inference/adapters/gen-video/workflows/ltx25_{i2v,t2v}_api.json` were machine-
converted from the OFFICIAL ComfyUI templates (`video_ltx2_5_i2v.json` /
`_t2v.json` from the comfyui-workflow-templates package) — the whole pipeline
lives in a subgraph there; the converter flattened it to API format, resolved
widget values via /object_info, dropped the optional prompt-enhancer path and
the V3 ResizeImageMaskNode (our FLUX keyframes are already at target size),
and turned the Primitive/Math nodes into `__TOKEN__` placeholders that
gen-video-comfy's `_build_ltx()` fills per request:

`__PROMPT__ __WIDTH__ __HEIGHT__ __WIDTH_HALF__ __HEIGHT_HALF__ __FRAMES__
__FRAME_RATE__ __SEED__ __IMAGE__ __PREFIX__`

Graph shape: gemma4 text encode → LTXVConditioning → base gen at HALF res
(EmptyLTXVLatentVideo + LTXVEmptyLatentAudio → LTXVConcatAVLatent →
SamplerCustomAdvanced, 8 steps, euler_ancestral, LTXVDualCFGGuider cfg 1/1)
→ LTXVLatentUpsampler 2x → i2v re-inject (LTXVImgToVideoInplace, strength
0.7 base / 1.0 refine) → 3-step refine → LTXVSeparateAVLatent →
VAEDecodeTiled (video) + LTXVAudioVAEDecode (audio) → CreateVideo 24fps.

## Pipeline settings

- `_MODEL_SPECS["ltx2"] = (704, 1280, 121)` — vertical 11:20, 121 frames =
  5s @ 24fps. **Dimensions must be multiples of 64**, not 32: the graph
  renders at half resolution and the half-res latent snaps to 32, so a 736
  request silently comes out 704.
- RIFE interpolation is SKIPPED for ltx2 (native 24fps, audio would desync).
- Heavy-model path: vLLM sleep + ComfyUI `/free` purge around each render
  (same as 14b) — the stack wants ~40GB unified memory.
- Verified E2E 2026-08-23: FLUX keyframe -> 5.04s / 24fps / 704×1280 H.264
  **with an AAC stereo track the model generated itself**. Sampling is fast
  (8 base steps 13s + 3 refine steps 25s); the cost is the cold model load.
