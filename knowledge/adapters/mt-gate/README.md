# mt-gate — translation quality gate

MetricX-24 Hybrid Large (`google/metricx-24-hybrid-large-v2p6`, Apache-2.0) in
reference-free mode: `source` (English) + `candidate` (translation) → an MQM-style
error score, **0 = perfect, 25 = worst**. CPU by default (~5 GB RAM), about 0.8 s
per short segment on the Spark's Grace cores; no GPU memory taken from the renderers.

```
GET  /health                     {"loaded": bool, "model", "device", "error"}
POST /score {"items": [{"source": "...", "candidate": "..."}]}  → {"scores": [float]}
```

Start/stop on the Spark: `inference/hosts/dgx-spark/gate.sh {up|down|status|logs}`
(port 8012; the backend reads `GATE_URL`, `GATE_MAX_SCORE`, `GATE_ENABLED`).

`metricx_models.py` is Google's `metricx24/models.py` (Apache-2.0) with three
changes for transformers ≥ 5 and CPU serving: the stacks own their embedding and
are tied to `shared` after construction, the decoder's dummy input follows the
encoder's device instead of jumping to CUDA, and the private warning constant is
read defensively. `app.py` loads the checkpoint with `tie_word_embeddings=False`
(what its `config.json` says; transformers 5 otherwise applies a `d_model**-0.5`
rescale meant for tied heads and squashes every score to ~0–1).

Measured 2026-09-26 (en→uk, one bakery post):

| candidate | score |
|---|---|
| faithful translation | 3.6 |
| wrong product (sneakers instead of cake) | 9.1 |
| literal calque, wrong idiom | 9.4 |
| translation with one invented word + a repeated phrase | 12.5 |
| keyword soup | 14.5 |
| English copied unchanged | 19.1 |
| gibberish | 22.8 |

The backend threshold starts at 7.0 (`GATE_MAX_SCORE`); the owner's accept/reject
decisions calibrate it (docs/AGENTIC_PLAN.md §2.4). Automatic metrics under-detect
flat-but-fluent Ukrainian on social text, so the gate catches drift and omission,
not taste.
