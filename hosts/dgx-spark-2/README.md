# dgx-spark-2: Qwen-Edit replica (GB10, 122 GB unified)

Tailnet `100.64.0.12`, LAN `192.168.0.100` (direct link to dgx-spark). Serves `qwen-image-edit` next to
dgx-spark's copy; LiteLLM (least-busy) spreads renders across both. Only ComfyUI + inf-image + node-agent.

| Service | Listens |
|---|---|
| inf-image | **100.64.0.12:8102** (Bearer `IMAGE_API_KEY`, the gateway sends it) |
| inf-node-agent | **100.64.0.12:8090** |
| inf-comfyui | 127.0.0.1:8188 |

The machine has 120 GB free for more models (Qwen-Image-2512 and LTX-2.5 weights are already in `~/ComfyUI`;
see `knowledge/workflows`).

## Measured (2026-10-09)

| What | Number |
|---|---|
| Qwen-Edit 1024², warm | ~20 s (same as dgx-spark) |
| first render after start | ~3–4 min (weights from disk) |
| 4 concurrent requests through the gateway | split 2/2 across the Sparks (LiteLLM least-busy, **1 worker**) |
| memory with Qwen-Edit warm | ~30 GB of 121, so plenty of room for 2512 / LTX later |

Each box's ComfyUI renders one image at a time; requests queue inside it (512² renders measured
19 → 39 → 58 s when three landed on one box).
