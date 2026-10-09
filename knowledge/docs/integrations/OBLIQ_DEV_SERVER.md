# Obliq → local models through boostcontent.io (dev server mode)

**For:** the Obliq repo (app + `packages/obliq_ai_harness`). Drop this file into that repo
and hand it to whoever builds the app side.

**Goal:** develop and test the Obliq assistant against the owner's own models (two DGX
Sparks) and spend NEAR AI money only on final testing. The server is live and verified
(2026-10-07); what is left is the app's dev mode (§4).

**Changed since the first version of this file (2026-10-07, later the same day):**
- The key is now the **Dev Obliq client access token** (`adc_…`), not an `llm_…` key.
  The separate "LLM API keys" menu is gone. Wherever earlier notes or code say `llm_…`,
  use the `adc_…` token.
- **No rate or daily caps.** The server never answers 429 or 402 any more; usage is only
  counted. Throttling or backoff added for those codes is harmless but never triggers here.
- §1a lists the exact request limits, and how the picture endpoints differ from NEAR's
  FLUX.

**What you give up in dev mode:** no TEE, no attestation, no end-to-end encryption, no
signed answers. The server sees every prompt in plain text. Test accounts and test chats
only, and never in a build anyone else installs.

---

## 1. The server (live)

| | |
|---|---|
| Base URL (app, NEAR style) | `https://boostcontent.io`, with no `/v1`; the app appends `/v1/...` |
| Base URL (harness, SDKs) | `https://boostcontent.io/v1` |
| Auth | `Authorization: Bearer adc_…`. `x-no-aliasing: true` is accepted and ignored |
| Key | the access token of the **Dev Obliq** client (BoostContent admin → Clients). Shown once; *Rotate token* / *Disable* revoke it |
| Protocol | NEAR AI Cloud's API = OpenAI's: same paths, request/response shapes, SSE |

Endpoints and what answers them:

| Endpoint | Model id the app sends | Answered by |
|---|---|---|
| `POST /v1/chat/completions` | `Qwen/Qwen3.6-35B-A3B-FP8` (default) | Qwen3.6-35B-A3B on vLLM. Tools, vision and json_schema all work |
| same | `Qwen/Qwen3.8-27B`, `z-ai/glm-5.3-flash` | the same Qwen3.6 (alias); the context is 24k, not 1M |
| same | `Qwen/Qwen3-VL-30B-A3B-Instruct` | the same Qwen3.6 (natively multimodal; `image_url` data URIs work) |
| `POST /v1/embeddings` | `Qwen/Qwen3-Embedding-0.6B` | **the real model**: 1024-d, normalised, matches the model card's reference scores, so search indexes carry over to and from NEAR |
| `POST /v1/images/generations` | `black-forest-labs/FLUX.2-klein-4B` | Qwen-Image-2512 (~10 s for 768×1344); returns `b64_json` PNG |
| `POST /v1/images/edits` (multipart) | same | Qwen-Image-Edit-2511, for photo → sticker (~20 s) |
| `GET /v1/models` | n/a | lists every id above |

Every reply carries the model id the app asked for. Streams include the final usage chunk
(`stream_options.include_usage`), so the in-app cost meter works.

Not served (the app must not call these in dev mode): `/v1/attestation/report`,
`/v1/signature/{id}`, E2EE headers (`X-Client-Pub-Key`, `X-Model-Pub-Key`,
`X-Encryption-Version`, …), `/v1/audio/transcriptions`.

## 1a. Request limits and differences from NEAR

No per-minute or per-day caps. What remains comes from the models and hardware:

| What | Limit | When exceeded |
|---|---|---|
| `max_tokens` / `max_completion_tokens` | default 1,024, clamped to 4,096 (the context is 24k shared with the prompt) | clamped silently, not refused |
| Prompt + answer | 24,576 tokens | 400 from the model |
| Picture `size` | `WxH`, each side 256–1536, rounded down to a multiple of 16 | 400 `invalid_request_error` |
| Pictures per request (`n`) | 1–4 | 400 |
| Edit photo | one file, 1 byte–20 MB | 400 |
| Embeddings `input` | up to 64 strings; `model` must be `Qwen/Qwen3-Embedding-0.6B` | 400 |

**Pictures: same API, different model.** The app keeps sending
`black-forest-labs/FLUX.2-klein-4B`; Qwen-Image-2512 (generations) and Qwen-Image-Edit-2511
(edits) answer. Request and response shapes are NEAR's, so no code branch is needed, but:
- **Looks differ.** Another model draws the picture: the same prompt and `seed` will not
  reproduce NEAR's image, and Qwen-Image renders text in pictures better. Judge
  picture quality on NEAR; use dev to test that the flow works.
- **Always `b64_json`.** `response_format: "url"` is ignored; read `data[i].b64_json`, never
  `data[i].url`.
- **Only `prompt`, `n`, `size`, `seed` are used** (edits: `image`, `prompt`, `n`, `size`).
  Other picture parameters are ignored.
- **Edits take one photo.** Only the first `image` / `image[]` part is used; extra reference
  images are dropped.
- **The picture `model` id is not checked here**, so a typo works in dev and fails on
  NEAR. Keep the id exactly `black-forest-labs/FLUX.2-klein-4B`.
- **Slower:** ~10 s per new picture, ~20 s per edit (within the app's 120 s picture limit).

## 2. Errors

| Status | `error.code` | The app should read it as |
|---|---|---|
| 200 | n/a | answer |
| 5xx / timeout | `upstream_unavailable` | transient: one retry, then "can't reach" |
| 401 | `invalid_api_key` | refused (wrong, rotated, disabled or expired token) |
| other 4xx | `invalid_request_error`, `model_not_found` | refused request |

That is the same mapping the app already applies to NEAR; keep the app's 402/429 handling
for NEAR, this server just never sends them. Body shape: `{"error": {"message", "type", "code"}}`.

## 3. Use it today: the harness (no app change needed)

The harness has no attestation step, so it works now:

```bash
cd packages/obliq_ai_harness
NEAR_AI_BASE_URL=https://boostcontent.io/v1 NEAR_AI_API_KEY=adc_… \
  fvm dart run bin/run_scenarios.dart --cloud --open --cloud-only
```

No rate cap, so scenario runs are never throttled. Reference scores on NEAR with Qwen 3.6:
everyday 67/67, plans 10/10.

Smoke test:

```bash
B=https://boostcontent.io/v1; K=adc_…
curl -s $B/models -H "Authorization: Bearer $K" | head -c 300
curl -sN $B/chat/completions -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3.6-35B-A3B-FP8","stream":true,"stream_options":{"include_usage":true},
       "chat_template_kwargs":{"enable_thinking":false},
       "messages":[{"role":"user","content":"What is on my calendar tomorrow?"}],
       "tools":[{"type":"function","function":{"name":"calendar_list_events","description":"events on a day",
         "parameters":{"type":"object","properties":{"day":{"type":"string"}},"required":["day"]}}}],
       "tool_choice":"auto","max_tokens":200}'
curl -s $B/embeddings -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-Embedding-0.6B","input":["dinner on sunday"]}' | head -c 200
curl -s $B/images/generations -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model":"black-forest-labs/FLUX.2-klein-4B","prompt":"a red circle","n":1,"size":"1024x1024","response_format":"b64_json"}' | head -c 120
```

Expected: the stream ends with a `calendar_list_events` tool call, `finish_reason:
"tool_calls"`, a usage chunk, then `data: [DONE]`. The embedding has 1024 floats. The
picture call returns `{"data":[{"b64_json":"iVBOR…` after ~10 s.

## 4. To build in the app: `OBLIQ_AI_DEV_SERVER` (about half a day)

Today the app **fails closed**: before every call it fetches NEAR's attestation report,
checks Intel, NVIDIA and NEAR's software, and seals the request to the model's TEE key.
This server cannot pass any of that, so pointing `OBLIQ_NEAR_AI_BASE_URL` at it alone
gets "didn't pass this phone's security check". Add a dev mode:

**Switch**
- `--dart-define=OBLIQ_AI_DEV_SERVER=true` turns it on, read once at startup next to the
  existing `OBLIQ_NEAR_AI_BASE_URL` / `OBLIQ_NEAR_AI_API_KEY` defines.
- **Ignored in prod builds** (`OBLIQ_DEFAULT_ENV=prod`): the flag has no effect there, so it
  can never ship. Log once that it was ignored.
- **Also refuse it when the base URL is NEAR's** (`cloud-api.near.ai`): dev mode with the
  real cloud would silently drop the TEE guarantees.

**Behaviour when on**
- Skip the attestation fetch and verification, E2EE sealing and decryption, and the
  per-answer signature checks.
- Send plain OpenAI requests (`openai_wire.dart`, unchanged) to
  `<OBLIQ_NEAR_AI_BASE_URL>/v1/...` with `Authorization: Bearer <OBLIQ_NEAR_AI_API_KEY>`.
- Everything else stays as is: model ids, routing, tool loop, streaming parser, `<think>`
  stripping, embeddings and index, image calls, usage metering and the cost meter (it will
  read real token counts at $0), and the error mapping in §2.

**Visible, always**
- The assistant header shows **"DEV server — not private"** while the mode is on.
- Every proof/attestation badge reads **"dev server, unverified"**, never "verified".

**Tests**
- With the flag set and `OBLIQ_DEFAULT_ENV=prod`: attestation still runs (flag ignored).
- With the flag set and the NEAR base URL: refused or ignored, per the rule above.
- With the flag set in dev: no request to `/v1/attestation/report` or `/v1/signature/*`,
  no E2EE headers on any request, plain JSON body sent, and the header/badges show the dev
  labels.
- Without the flag: behaviour byte-for-byte as today (existing attestation tests pass).

**Run**

```bash
fvm flutter run \
  --dart-define=OBLIQ_AI_DEV_SERVER=true \
  --dart-define=OBLIQ_NEAR_AI_BASE_URL=https://boostcontent.io \
  --dart-define=OBLIQ_NEAR_AI_API_KEY=adc_…
```

Put the key in a git-ignored defines file (`--dart-define-from-file=dev.local.json`),
never in the repo or a build anyone else installs: the token is uncapped and also signs
in to the BoostContent dashboard as Dev Obliq. If it leaks, the owner presses *Rotate token*.

## 5. Switching to NEAR for final testing

Drop `OBLIQ_AI_DEV_SERVER` and `OBLIQ_NEAR_AI_BASE_URL` (or set the base back to
`https://cloud-api.near.ai`) and use the NEAR key. Index, settings and chats carry over;
the search index does not rebuild, because the embedding model is the same.

## 6. Good to know

- **Shared hardware.** The same GPUs serve BoostContent's ad pipelines and other users.
  Answers can slow down at busy times, and a model is briefly unavailable (503) during
  maintenance. The app's existing "one retry, then can't reach" handling covers it.
- **Timeouts.** The app's limits (20 s connect, 45 s idle between stream pieces, 150 s per
  answer, 120 s per picture) fit: chat streams start in under a second, pictures take
  10–30 s.
- **Context.** 24k tokens instead of GLM's 1M: very long chats get trimmed sooner in dev.
- **Thinking.** Off by default (`enable_thinking: false` is added unless the request sets
  `chat_template_kwargs`); send `{"enable_thinking": true}` to get the reasoning pass.
- **Privacy.** Plain text over HTTPS to a server the owner runs. Use test data.
