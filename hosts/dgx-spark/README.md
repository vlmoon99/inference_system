# dgx-spark: gateway host (GB10, 122 GB unified, aarch64)

Tailnet `100.64.0.1`. Runs the gateway and the v1 model set. One compose project `inf`:

```
cd hosts/dgx-spark
cp .env.example .env   # fill secrets (first install only)
docker compose up -d --build
../../smoke.sh         # PASS = this host serves the contract
```

| Service | Container | Listens | Notes |
|---|---|---|---|
| LiteLLM gateway | inf-litellm | **100.64.0.1:8000** | the only model door for projects; keys + usage in inf-litellm-db (127.0.0.1:5440) |
| admin | inf-admin | **100.64.0.1:8091** | first visit sets the password; reset: `docker exec inf-admin python -m reset` |
| node-agent | inf-node-agent | **100.64.0.1:8090** | `X-Agent-Token` |
| SearXNG | inf-searxng | **100.64.0.1:8888** | `/search?q=…&format=json` |
| vLLM Qwen3.6-35B-A3B-NVFP4 | inf-llm | **100.64.0.1:8010** (key) | `LLM_GPU_UTIL=0.26` |
| Qwen3-Embedding-0.6B | inf-embed | **100.64.0.1:8013** (key) | 1024-dim, last-token pool, L2 |
| ComfyUI v0.33.3 | inf-comfyui | 127.0.0.1:8188 | `--highvram`; code + weights from `~/ComfyUI` |
| inf-image (Qwen-Image-Edit-2511) | inf-image | **100.64.0.1:8102** (key) | OpenAI images API + URL contract |

Everything has `restart: unless-stopped`: after a power cut Docker brings it all back. The tailnet-bound
services crash-loop until tailscaled has the IP, then settle.

## Measured (2026-10-09)

| What | Number |
|---|---|
| vLLM cold start (weights in page cache) | ~6 min to healthy |
| vLLM KV cache at 0.26 | 1.34 M tokens fp8, 54× concurrency at 24k ctx |
| `LLM_GPU_UTIL=0.20` | **fails**: "No available memory for the cache blocks" |
| `LLM_GPU_UTIL=0.35` + ComfyUI default | ComfyUI sees 8–17 GB usable, partial loads, **75–125 s** per 1024² edit |
| Qwen-Edit, 1024² (or 768×1024), warm, 0.26 + `--highvram` | **19–27 s** |
| Qwen-Edit, first render after ComfyUI start | 150–230 s (weights load); the keep-warm loop takes this hit |
| Memory with everything warm | ~78 GB used of 121 |

The memory rule from the old system still governs: on GB10, vLLM and ComfyUI size themselves from MemFree,
and page cache counts against them. If renders slow down to minutes, look at `free -g` first.

## GLM-5.3-Flash over both Sparks (2026-10-11, recipe defaults: PARALLEL=4, fp8 KV, q4 dense, DFlash2 drafts)

Rank 0 here, rank 1 on dgx-spark-2, two RoCE rails. Load 376 s. Startup estimate 88.09 GiB of 98.04; MemAvailable
with the server idle: 8-9 GiB here, 10 GiB on dgx-spark-2 (114 GiB each before the start, engines off).
Through the gateway: one request 3,000 tokens in 44.9 s (66.8 tok/s, prefill included); 4 at once 6,000 tokens in
56.9 s (105.5 tok/s together). Checkpoint 164 GiB + DFlash2 2.2 GiB in `~/.cache/huggingface`, image 24.6 GB.
