# dgx-spark-2 (GB10, 122 GB unified)

Tailnet `100.64.0.12`, LAN `192.168.0.101`. A full core node since 2026-10-10: the same stack as dgx-spark
(`../core.yaml`), nothing box-specific. See the top-level README, "The cluster".

| Service | Listens |
|---|---|
| inf-litellm (gateway) | **100.64.0.12:8000** |
| inf-admin | **100.64.0.12:8091** |
| inf-llm / inf-embed / inf-image | **100.64.0.12:8010 / :8013 / :8102** (key required) |
| inf-node-agent | **100.64.0.12:8090** |
| inf-litellm-db | 100.64.0.12:5440 (follower of the master, or the master) |
| inf-lb, inf-comfyui | 127.0.0.1 |

The ComfyUI base image here is `ads-comfyui:v0.33.3` (`COMFY_BASE` in `.env`); it has no Dockerfile, never prune it.
Qwen-Image-2512 and LTX-2.5 weights are in `~/ComfyUI` (candidates for non-core models; `knowledge/workflows`).

## Measured

| What | Number | When |
|---|---|---|
| memory with all three core models loaded | ~78 GB of 121 | 2026-10-10 |
| Qwen-Edit 1024², warm | ~20 s (same as dgx-spark) | 2026-10-09 |
| first render after start | ~3–4 min (weights from disk) | 2026-10-09 |
| LLM ready after container start | ~4–5 min | 2026-10-10 |

Each box's ComfyUI renders one image at a time; requests queue inside it.
