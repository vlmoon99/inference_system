# mac — the whole inference host on one Apple Silicon Mac

A stand-in for the Sparks when they are off: the same three public model ids on `:8000`, served by
smaller models that fit a laptop. Nothing here is used by the Sparks, and nothing outside this folder
refers to it. No Docker (Docker on macOS has no GPU), no cluster, no project keys: one key in `.env`.

| Public id (unchanged) | Here | On the Sparks |
|---|---|---|
| `qwen3.6-35b` | Qwen 3.5 4B, 4-bit, on [MTPLX](https://mtplx.com) (`Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed`, 2.4 GB) | Qwen3.6-35B on vLLM |
| `qwen3-embedding-0.6b` | Qwen3-Embedding 0.6B 4-bit on MTPLX (`mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ`, 1024-dim) | the same model, full precision |
| `qwen-image-edit` | FLUX.2 klein 4B, 4-bit, on mflux (`mflux-community/flux2-klein-4b-mflux-q4`, 4.3 GB, Apache-2.0) | Qwen-Image-Edit-2511 on ComfyUI |

```
brew install youssofal/mtplx/mtplx uv
./fetch.sh                      # 7.1 GB into ~/.cache/inf-mac/models (plain HTTP, resumes)
cp .env.example .env            # set API_KEY (BoostContent: its INFERENCE_KEY)
./run.sh start                  # MTPLX on 127.0.0.1:8001, the gateway on :8000
./run.sh status | stop | logs
```

`gateway.py` is the only code: it maps the public ids, forwards chat and embeddings to MTPLX, and renders
pictures with mflux in its own process (one render at a time). The picture contract is the one of
`services/image` (`image_url`, `image_b64`, `output_put_url`, `size`, `fit`).

## What is different from the Sparks

* **The LLM cannot see photos** (`supports_vision: false` for this pack). Image parts of a chat request are
  dropped, so BoostContent's style profile is written from the description alone. Pictures still start
  from the product photo. Set `LLM_VISION=1` only with a model that takes images.
* **JSON mode** needs `llguidance` inside MTPLX's own venv, or MTPLX refuses `response_format`. `run.sh start`
  installs it.
* **Repetition penalties are added** (`frequency_penalty` 0.6, `presence_penalty` 0.3) when the caller sends
  none: without them the first Ukrainian caption looped on one word.
* **Ukrainian and Russian are weak.** English captions read well; translations come out with invented or
  misspelled words ("Вашня кавовиця"). The 8-bit pack (`…-Optimized-Quality`, 4.6 GB) is untested here.
* No streaming, no `/v1/images/edits`, no per-project keys, limits or usage log, no retries, no search.

## Measured (M1 Max, 64 GB, macOS 26.6, 2026-10-10)

| What | Result |
|---|---|
| LLM | 300 tokens in 7.0 s (≈43 tok/s), JSON mode, thinking off |
| Embeddings | 1024 dimensions |
| Picture 1024×1280 from a product photo, 4 steps, full size | 108 s (first, with the model load) and 172 s |
| The same with `IMAGE_RENDER_SCALE=0.75` (painted 768×960, resized up) | 57 s, 51 s, 52 s |
| BoostContent `smoke.sh` on this Mac, local | 16/16, one post with one picture ready in 81 s |
| The same through a Cloudflare quick tunnel | 16/16, ready in 90 s |
| Memory | 64% free with everything loaded and Docker running |

Not measured: three options in one round, several users at once, a long run (heat), the LLM under a render.
