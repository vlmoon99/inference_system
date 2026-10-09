# Shared embedding service — BAAI/bge-m3 on the Spark

A single **bge-m3** embedding endpoint is hosted on the DGX Spark and exposed on the
**tailnet** so other projects can reuse the GPU. Its first consumer is the **books AI
system** on the student PC. It is intentionally decoupled from the ad pipeline (separate
container, separate port) so restarting the ad system never touches it.

| | |
|---|---|
| Model | `BAAI/bge-m3` — 1024-dim dense, multilingual (incl. Russian), 8k context |
| Endpoint | `http://100.64.0.1:8011` (tailnet) · `http://localhost:8011` (on the Spark) |
| API | OpenAI-compatible `POST /v1/embeddings` + `GET /health` |
| Runtime | `product_dream-svc-embed` image on NGC PyTorch 25.09, `--gpus all`, `device: cuda` |
| Lifecycle | `--restart unless-stopped` (survives reboots) |

## Manage (from the ad-system repo)

```bash
inference/hosts/dgx-spark/embed.sh up       # start (first boot downloads ~2.3GB into the HF cache)
inference/hosts/dgx-spark/embed.sh status   # health + tailnet URL
inference/hosts/dgx-spark/embed.sh logs
inference/hosts/dgx-spark/embed.sh down
```

## Consume from the books AI system (student PC)

It speaks the OpenAI embeddings contract, so any OpenAI client works — just point the
base URL at the Spark's tailnet IP:

```python
from openai import OpenAI
client = OpenAI(base_url="http://100.64.0.1:8011/v1", api_key="not-needed")
vecs = client.embeddings.create(model="BAAI/bge-m3", input=["chapter one text…"])
emb = vecs.data[0].embedding          # 1024 floats, L2-normalized (cosine = dot product)
```

Or plain HTTP:

```bash
curl -s http://100.64.0.1:8011/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"input":["War and Peace, opening line"]}'
```

Vectors are L2-normalized, so cosine similarity is just a dot product — index them in
pgvector / FAISS / Qdrant with the cosine (or inner-product) metric.

## Notes

- **Coexistence:** ~1 GB of the Spark's 119 GB unified memory. Does not disturb the ad
  system or the Qwen student endpoint (`:8010`). One model at a time still applies to the
  *big* models — bge-m3 is small enough to stay hot alongside them.
- **First request** after a cold start loads weights (a few seconds); subsequent calls are fast.
