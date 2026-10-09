# The inference contract

Every media service a worker talks to speaks three HTTP endpoints. The backend
(`backend/ads/providers/*`) knows nothing else about the hardware; a host is
"supported" when `python -m inference.conformance --host <name>` passes against it.
The machine-readable version is [`openapi.yaml`](openapi.yaml).

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness + **capabilities** (below) |
| `POST /generate` | one job; JSON in, JSON out with relative file paths |
| `GET /files/<path>` | fetch a generated file (workers on other machines) |
| `GET /system` | optional: the box's GPUs, load and RAM for the admin Infra page |

The LLM is the one exception: any **OpenAI-compatible** server (`/v1/models`,
`/v1/chat/completions`) — vLLM, llama.cpp, mlx-lm, a hosted API. Its
capabilities are synthesised by the worker from `/v1/models`.

## `GET /health`

```json
{
  "ok": true,
  "loaded": true,
  "host": "rtx3090x3",
  "engine": "comfy",
  "device": "cuda",
  "model": "flux.1-dev-fp8",
  "capabilities": { "kind": "image", "models": ["flux1-dev-fp8"], "lora": true }
}
```

* `loaded` — the model is resident/ready (the worker's health probe uses this).
* `host` — the `name` from the host's `host.yaml`. `engine` and `device` are free text.
* `capabilities.kind` is one of `image | video | tts`, then per kind:

| kind | fields |
|---|---|
| `image` | `models: [str]`, `lora: bool` (accepts `lora_name`), `max_side: int` |
| `video` | `models: [str]` from `5b | 14b | ltx2`, `audio: bool` (synced audio), `max_frames: int`, `i2v: bool` |
| `tts`   | `langs: [str]` (BCP-47 primary tags that will actually synthesise), `clone: bool` |

A service that omits `capabilities` is a **legacy** host: the worker assumes it
can do everything its kind implies, and conformance reports it.

The worker derives the job kinds it claims from the set of kinds that are up
(`backend/ads/providers/hosts.py::REQUIREMENTS`). No video service → no `short`,
`film`, `campaign`, `golden_run`. That is the whole mechanism for heterogeneous hardware.

## `POST /generate`

Request shapes are the adapter's Pydantic models (`inference/adapters/*/app.py`)
and are frozen — the backend's providers send exactly these fields:

* **image**: `prompt, product_id, kind, n, width, height, steps?, seed?, lora_name?, lora_strength?, lora_base?`
  → `{ "images": [{ "path": "<product_id>/<name>.png", "seed": int, "elapsed_s": float }, …], "model": str }`
* **video**: `prompt, product_id, ref_image_path?, ref_image_b64?, quality, width, height, num_frames, seed?, model?`
  (`ref_image_b64` carries the reference image bytes when the worker runs on another machine; it wins over the path)
  → `{ "video": { "path": "<product_id>/<name>.mp4", "elapsed_s": float, "frames": int, "model": str, "quality": str } }`
  A request for a `model` the host did not advertise fails with **422**; the
  short pipeline picks from the advertised list before it asks.
* **tts**: `text, product_id, lang, voice?, pronunciations?, ref_audio_path?, …`
  → `{ "audio": { "path": "<product_id>/<name>.wav", "sample_rate": int, "duration_s": float, "elapsed_s": float }, "model": str, "engine": str }`

Paths are **relative to the service's `ASSETS_DIR`**. A worker on the same box
reads them from the shared directory; a worker elsewhere fetches them with
`GET /files/<path>` (`backend/ads/providers/files.py::ensure_local`).

## `GET /files/<path>`

Serves a generated file. Must refuse paths that resolve outside `ASSETS_DIR` (404).

## `GET /system` (optional)

What the machine is doing right now — every GPU (`util`, `mem_used_mb`/`mem_total_mb`,
`temp_c`, `power_w`), 1/5/15-min `load`, `cpus`, `mem` (RAM + swap in GB), and `unified`
(GPU memory is system RAM, e.g. the GB10). Every adapter on a box reports the same box;
the control plane de-duplicates by `host`. Shared implementation:
`inference/adapters/sysinfo.py`. Missing (404) = the host shows without live numbers.

## Versioning

This is contract **v1**. Adding a field to `/health` or a response is compatible;
renaming or removing one is a v2 and needs every adapter and the providers changed
together. `openapi.yaml` carries the version.
