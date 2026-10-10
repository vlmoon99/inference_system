# inference_system

One local inference cloud for many projects: **LiteLLM** in front of self-hosted engines on tailnet
machines. Public at `https://api.vramhouse.com/v1` (only `/v1`, through BoostContent's entry and tunnel); everything else is tailnet-only. Design: `docs/PLATFORM_PLAN.md`; scaling + cloud: `docs/SCALING_AND_CLOUD.md`;
progress log: `docs/PROGRESS.md`.

## Using it from a project

```
base_url = https://api.vramhouse.com/v1     # OpenAI-compatible, from anywhere (project key)
base_url = http://100.64.0.1:8000/v1        # the same gateway inside the tailnet
api_key  = <the project's key from the admin → Projects>
```

| model | endpoint |
|---|---|
| `qwen3.6-35b` | `/v1/chat/completions` (streaming, tools; thinking on by default, `chat_template_kwargs.enable_thinking=false` to skip) |
| `qwen3-embedding-0.6b` | `/v1/embeddings` (1024-dim) |
| `qwen-image-edit` | `/v1/images/generations` (JSON) and `/v1/images/edits` (multipart) |

**Images, URL contract.** Add `image_url` (pre-signed GET of a photo to edit) and/or `output_put_url`
(pre-signed PUT where the PNG goes). The reply's `data[0].url` is the object URL without the signature.
Without `output_put_url` you get OpenAI's `b64_json`. Without an input photo the model paints a blank
canvas of `size`. Warm render ≈ 20 s.

Search: `http://100.64.0.1:8888/search?q=…&format=json` (SearXNG).

## Layout

```
gateway/litellm.yaml     models → backends (add a machine = another deployment under the same name)
hosts/<host>/            compose.yaml + .env(.example) + README with measured numbers, one per machine
services/image           OpenAI images API over ComfyUI (Qwen-Image-Edit-2511)
services/comfyui         ComfyUI runtime image (pinned packages on the NGC base)
services/embed           Qwen3-Embedding server
services/node-agent      per-machine /system + container control
services/admin           operator console (FastAPI + React/Vite/Tailwind)
smoke.sh                 definition of done: ./smoke.sh → PASS
knowledge/, weights/     dormant reference (Ukrainian TTS, video workflows, old hosts)
```
