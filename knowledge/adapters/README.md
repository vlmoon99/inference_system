# inference/ — everything that runs on a GPU box

The backend never knows what hardware exists. It knows four URL pools and asks
each service what it can do. Adding a machine class is adding a folder here.

```
inference/
├─ CONTRACT.md · openapi.yaml   the 3 endpoints every service speaks (v1)
├─ conformance/                 python -m inference.conformance --host <name> [--at <ip>]
├─ hostspec.py                  host.yaml schema; python -m inference.hostspec validates all
├─ adapters/                    engine-agnostic HTTP fronts over the engines
│  ├─ gen-image/                FLUX via ComfyUI (engine: comfy)
│  ├─ gen-video/                LTX-2.5 / Wan 2.2 via ComfyUI; advertises 5b|14b|ltx2 it can serve
│  ├─ gen-tts/                  kokoro en · radtts/styletts2/f5/piper uk,ru registry (CPU/CUDA)
│  └─ gen-tts-ukrainian/        optional ESPnet sidecar (own venv, GPL weights)
├─ workflows/                   ComfyUI API graphs shared by comfy-engine hosts
├─ training/                    FLUX LoRA fine-tune (kohya) — runs on a host GPU
├─ requirements*.txt            adapter deps (installed into the repo .venv)
└─ hosts/                       ONE FOLDER PER MACHINE CLASS
   ├─ _template/                copy me
   ├─ dgx-spark/                built — models + api + worker + web on one box
   └─ rtx3090x3/                planned — 3 discrete cards, joins as a worker
```

## How the pieces fit

1. A host runs its services on its own ports (`host.yaml:placement`).
2. Each service answers `GET /health` with a `capabilities` block (CONTRACT.md).
3. An `ads-worker` on the box (or anywhere that can reach it) probes the four
   URLs, derives which job kinds it may claim
   (`backend/ads/providers/hosts.py::REQUIREMENTS`), and heartbeats them to the
   executor registry. No video service → it never claims a short.
4. The short pipeline picks a video model from the advertised list, so a request
   for a model the host lacks is corrected before it is sent; the adapter 422s
   anything that still slips through.
5. `/v1/status` shows every configured service's capabilities on Admin → Infra.

## Adding hardware

```
cp -r inference/hosts/_template inference/hosts/<name>   # edit host.yaml
make host-validate                                        # schema
# install engines + weights on the box (install.sh or up.sh in that folder)
python -m inference.conformance --host <name> --at <ip>   # PASS = supported
```

Engine choices the adapters are built for:

| Kind | NVIDIA | Apple silicon | Intel / AMD |
|---|---|---|---|
| LLM (OpenAI-compatible) | vLLM | llama.cpp server, mlx-lm | vLLM-XPU, llama.cpp SYCL/ROCm |
| Image | ComfyUI CUDA | ComfyUI MPS, mflux | ComfyUI XPU / ROCm |
| Video | ComfyUI LTX-2.5 / Wan 2.2 | advertise only when measured | advertise only when measured |
| TTS | kokoro ONNX (CPU), RadTTS uk (CUDA) | kokoro (CPU) | kokoro (CPU) |

A host that serves LLM + image + TTS but no video is a first-class worker: it takes
posts, photos, analytics and TTS work and leaves shorts to boxes that have video.

## Rules

* Never import `backend/ads` from an adapter. Adapters are plain FastAPI + httpx.
* Measured numbers live in the host README next to the hardware, not in `docs/`.
* Changing a request/response field is a contract version bump; changing `/health`
  additively is not.
