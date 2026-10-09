# BoostContent LLM API — use the Spark's models from another project

OpenAI-compatible **and NEAR-AI-Cloud-compatible** (same paths, request/response shapes
and model ids as `https://cloud-api.near.ai`, minus the TEE parts) chat, embeddings and
image API, served from the DGX Sparks and reachable from anywhere (phones on mobile data
included) through **boostcontent.io**. Copy this file into the project that will use it.

| | |
|---|---|
| **Base URL — NEAR style** | `https://boostcontent.io` (the client appends `/v1/...`) |
| **Base URL — OpenAI SDK style** | `https://boostcontent.io/v1` (or the older `https://boostcontent.io/api/llm/v1`; identical) |
| **Auth** | `Authorization: Bearer adc_…` (a client access token from the owner, see below). `x-no-aliasing` is accepted and ignored |
| **Endpoints** | `GET /v1/models` · `POST /v1/chat/completions` · `POST /v1/embeddings` · `POST /v1/images/generations` · `POST /v1/images/edits` |
| **Context** | 24,576 tokens shared by the prompt and the answer |
| **Max answer** | `max_tokens` defaults to 1,024, clamped to 4,096 |

**Model ids** — send NEAR's ids; each answers under the id you sent:

| Id you send | What answers | For |
|---|---|---|
| `Qwen/Qwen3.6-35B-A3B-FP8` (default) | Qwen3.6-35B-A3B (NVFP4, vLLM) | chat, tools, writing, translation |
| `Qwen/Qwen3.8-27B`, `z-ai/glm-5.3-flash` | the same Qwen3.6 (alias) | — |
| `Qwen/Qwen3-VL-30B-A3B-Instruct` | the same Qwen3.6 — it is natively multimodal | photos, OCR (`image_url` parts) |
| `Qwen/Qwen3-Embedding-0.6B` | **the real Qwen3-Embedding-0.6B**, 1024-d, normalised | embeddings (vectors match NEAR's) |
| `black-forest-labs/FLUX.2-klein-4B` | Qwen-Image-2512 (generations), Qwen-Image-Edit-2511 (edits) | pictures, stickers |
| any other chat id | Qwen3.6 | — |

Not served: `/v1/audio/transcriptions`, `/v1/attestation/report`, `/v1/signature/{id}`,
E2EE headers. No TEE, no attestation, no encryption beyond HTTPS: **the server sees every
prompt in plain text** — use it for development and test data.

## 1. Get a key

The key is a **client access token**: the owner creates a client in **Admin → Clients**
(one client per integrating app) and hands over its token (`adc_…`, shown once). It is
the same token that signs in to the BoostContent dashboard, so keep it out of anything you
ship. **Disable** or **Rotate token** on that client cuts the gateway off immediately.

No per-minute or per-day caps: usage is counted (prompt + answer; a picture counts as
2,000 tokens), not limited.

## 2. Check it works

```bash
export BOOST_LLM_KEY=llm_xxxxxxxx
curl -s https://boostcontent.io/v1/chat/completions \
  -H "Authorization: Bearer $BOOST_LLM_KEY" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Say hi in Ukrainian"}]}'
```

Streaming (Server-Sent Events, tokens arrive as they are generated):

```bash
curl -N https://boostcontent.io/v1/chat/completions \
  -H "Authorization: Bearer $BOOST_LLM_KEY" -H "Content-Type: application/json" \
  -d '{"stream":true,"messages":[{"role":"user","content":"Write a 3-line poem"}]}'
```

## 3. Use it from code

**Prefer streaming for anything longer than a sentence.** The public edge (Cloudflare)
drops a request that sends no bytes for ~100 s; a stream sends bytes immediately, and
the user sees text appear at once instead of waiting.

### Any OpenAI SDK (Python / Node / server side)

```python
from openai import OpenAI
client = OpenAI(base_url="https://boostcontent.io/v1", api_key=os.environ["BOOST_LLM_KEY"])
for chunk in client.chat.completions.create(
        model="boost", stream=True,
        messages=[{"role": "user", "content": "Hello"}]):
    print(chunk.choices[0].delta.content or "", end="") if chunk.choices else None
```

```ts
import OpenAI from "openai";
const client = new OpenAI({ baseURL: "https://boostcontent.io/v1", apiKey: process.env.BOOST_LLM_KEY });
const r = await client.chat.completions.create({ model: "boost", messages: [{ role: "user", content: "Hello" }] });
console.log(r.choices[0].message.content);
```

### iOS (Swift, URLSession, streaming)

```swift
struct BoostLLM {
    let key: String
    let url = URL(string: "https://boostcontent.io/v1/chat/completions")!

    /// Calls `onToken` with each piece of text as it arrives.
    func stream(_ messages: [[String: String]], onToken: @escaping (String) -> Void) async throws {
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["stream": true, "messages": messages])

        let (bytes, response) = try await URLSession.shared.bytes(for: req)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw URLError(.badServerResponse)          // 401 bad key · 503 model busy
        }
        for try await line in bytes.lines {
            guard line.hasPrefix("data: "), !line.hasSuffix("[DONE]"),
                  let data = line.dropFirst(6).data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let choice = (obj["choices"] as? [[String: Any]])?.first,
                  let delta = choice["delta"] as? [String: Any],
                  let text = delta["content"] as? String else { continue }
            await MainActor.run { onToken(text) }
        }
    }
}
```

HTTPS on a public domain, so no App Transport Security exceptions are needed.

### Android (Kotlin, OkHttp, streaming)

```kotlin
// implementation("com.squareup.okhttp3:okhttp:4.12.0")
class BoostLLM(private val key: String) {
    private val http = OkHttpClient.Builder()
        .readTimeout(5, TimeUnit.MINUTES)        // long answers stream for a while
        .build()

    /** Blocking — call from Dispatchers.IO. Invokes onToken for each piece of text. */
    fun stream(messages: JSONArray, onToken: (String) -> Unit) {
        val body = JSONObject().put("stream", true).put("messages", messages).toString()
            .toRequestBody("application/json".toMediaType())
        val req = Request.Builder()
            .url("https://boostcontent.io/v1/chat/completions")
            .header("Authorization", "Bearer $key")
            .post(body).build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) error("LLM ${resp.code}: ${resp.body?.string()}")
            val src = resp.body!!.source()
            while (!src.exhausted()) {
                val line = src.readUtf8Line() ?: break
                if (!line.startsWith("data: ") || line.endsWith("[DONE]")) continue
                val choices = JSONObject(line.removePrefix("data: ")).optJSONArray("choices") ?: continue
                if (choices.length() == 0) continue
                choices.getJSONObject(0).optJSONObject("delta")?.optString("content")
                    ?.takeIf { it.isNotEmpty() }?.let(onToken)
            }
        }
    }
}
```

Needs `<uses-permission android:name="android.permission.INTERNET"/>`.

### Flutter / Dart (iOS + Android from one codebase)

```dart
// pubspec: http: ^1.2.0
import 'dart:convert';
import 'package:http/http.dart' as http;

Stream<String> boostChat(String key, List<Map<String, String>> messages) async* {
  final req = http.Request('POST', Uri.parse('https://boostcontent.io/v1/chat/completions'))
    ..headers.addAll({'Authorization': 'Bearer $key', 'Content-Type': 'application/json'})
    ..body = jsonEncode({'stream': true, 'messages': messages});
  final resp = await http.Client().send(req);
  if (resp.statusCode != 200) {
    throw Exception('LLM ${resp.statusCode}: ${await resp.stream.bytesToString()}');
  }
  await for (final line in resp.stream.transform(utf8.decoder).transform(const LineSplitter())) {
    if (!line.startsWith('data: ') || line.endsWith('[DONE]')) continue;
    final choices = (jsonDecode(line.substring(6))['choices'] as List?) ?? const [];
    if (choices.isEmpty) continue;
    final text = choices.first['delta']?['content'];
    if (text is String && text.isNotEmpty) yield text;
  }
}
```

### React Native / Expo

RN's built-in `fetch` cannot read a response as a stream. Either call without
`stream` (fine for short answers), or use `react-native-sse` / `expo/fetch` for streaming.

## 4. Request options

**Chat** — standard OpenAI fields: `messages` (text, or `content` parts with
`{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,…"}}`), `temperature`,
`top_p`, `seed`, `max_tokens` / `max_completion_tokens`, `stop`, `stream`,
`stream_options` (the final usage chunk is always sent when streaming),
`response_format` (`json_object`, or `json_schema` — enforced, the model cannot break it),
`tools` + `tool_choice` (`"auto"`, `"none"`, `"required"`, or a named function; tool-call
deltas stream in OpenAI's format and end with `finish_reason: "tool_calls"`).

**Thinking mode.** The model can reason before answering. The gateway turns that **off**
by default (fast, chat-like answers). For hard problems, turn it on per request:

```json
{"messages": [...], "chat_template_kwargs": {"enable_thinking": true}, "max_tokens": 4096}
```

The reasoning then counts toward `max_tokens`.

**Embeddings** — `{"model": "Qwen/Qwen3-Embedding-0.6B", "input": "text" | ["up to 64 texts"]}`
→ `data[i].embedding` (1024 floats, L2-normalised). Instruction prefixes
(`Instruct: …\nQuery: …`) are embedded as given. Any other `model` is refused (400):
vectors from another model would silently corrupt a search index.

**Image generation** — `{"model": "black-forest-labs/FLUX.2-klein-4B", "prompt": "…",
"n": 1, "size": "1024x1024", "response_format": "b64_json"}` → `{"data": [{"b64_json": "<PNG>"}]}`.
`size` sides 256–1536 (e.g. `768x1344` portrait, `1344x768` banner), `n` 1–4. Always returns
`b64_json`.

**Image edit** (photo → sticker etc.) — multipart form: `image` (the file, ≤ 20 MB),
`prompt`, optional `model`, `n`, `size`, `response_format`. Without `size` the output keeps
the photo's framing. Same response as generation. Takes ~5–30 s.

## 5. Errors

Errors use OpenAI's shape, so SDKs raise them normally: `{"error": {"message", "type", "code"}}`.

| Status | `code` | Meaning · what to do |
|---|---|---|
| 401 | `invalid_api_key` | Missing, wrong, disabled or deleted key — do not retry |
| 400 | `invalid_request_error` / `model_not_found` | Bad body, empty `messages`, wrong embedding model, bad `size` |
| 503 | `upstream_unavailable` | A model is restarting or under maintenance — retry with backoff |
| 524 | — | (Cloudflare) a non-streaming answer took > 100 s — use `stream: true` |

Retry 503 with exponential backoff (1 s, 2 s, 4 s, … up to ~30 s).

## 6. Keys inside a mobile app — read this

Anything shipped in an app binary can be extracted. Choose deliberately:

* **Prototype / personal app:** put the key in the app only for your own dev builds.
  The token is uncapped and also signs in to the dashboard; if it leaks, the owner
  rotates or disables the client in one click and you ship a new one.
* **Public app with real users:** keep the key on **your own backend** and let the app
  call your backend (which checks your user's login and then calls this API). Then one
  abusive user cannot drain everyone's quota, and the key never leaves your server.
* Use **one key per app/environment** (e.g. `myapp-ios-dev`, `myapp-prod-backend`) so
  usage is visible per app and a revoke does not break the others.
* Never put the key in a public git repository. Load it from config / secure storage.

## 7. Fair use and availability

The same GPU serves BoostContent's ad pipelines and other users, so answers can slow
down when it is busy, and the model is briefly unavailable (503) during maintenance.
Design for it: stream, show a typing indicator, retry with backoff, and keep requests
small (send only the conversation history you need — every message counts against
the 24k context).

## 8. Obliq (NEAR-compatible dev server)

The Obliq app and its harness talk to this server exactly as to NEAR AI Cloud:

| Who | Setting | Value |
|---|---|---|
| App | `--dart-define=OBLIQ_NEAR_AI_BASE_URL=` | `https://boostcontent.io` — no `/v1` |
| App | `--dart-define=OBLIQ_NEAR_AI_API_KEY=` | the client's `adc_…` token |
| App | `--dart-define=OBLIQ_AI_DEV_SERVER=true` | the app's dev mode (skips attestation/E2EE; app-side work) |
| Harness | `NEAR_AI_BASE_URL` | `https://boostcontent.io/v1` — with `/v1` |
| Harness | `NEAR_AI_API_KEY`, optional `NEAR_AI_MODEL` | your key; e.g. `Qwen/Qwen3.6-35B-A3B-FP8` |

```bash
cd packages/obliq_ai_harness
NEAR_AI_BASE_URL=https://boostcontent.io/v1 NEAR_AI_API_KEY=adc_… \
  fvm dart run bin/run_scenarios.dart --cloud --open --cloud-only
```

Status codes match what the app expects: 5xx = retry once. There are no rate or daily
caps, so a scenario run is never throttled.

Smoke test:

```bash
B=https://boostcontent.io/v1; K=adc_…
curl -s $B/models -H "Authorization: Bearer $K" | head -c 300
curl -sN $B/chat/completions -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3.6-35B-A3B-FP8","stream":true,"stream_options":{"include_usage":true},
       "messages":[{"role":"user","content":"What is on my calendar tomorrow?"}],
       "tools":[{"type":"function","function":{"name":"calendar_list_events","description":"events on a day",
         "parameters":{"type":"object","properties":{"day":{"type":"string"}},"required":["day"]}}}],
       "tool_choice":"auto","max_tokens":200}'
curl -s $B/embeddings -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3-Embedding-0.6B","input":["dinner on sunday"]}' | head -c 200
curl -s $B/images/generations -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model":"black-forest-labs/FLUX.2-klein-4B","prompt":"a red circle","n":1,"size":"1024x1024","response_format":"b64_json"}' | head -c 120
```

