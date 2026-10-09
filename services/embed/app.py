"""embed-qwen3 — OpenAI-compatible embeddings with the REAL Qwen/Qwen3-Embedding-0.6B.

    GET  /health         -> {ok, loaded, model, dim, device}
    POST /v1/embeddings  {model?, input: str | [str] (≤ 64)} -> OpenAI embedding list

Served for the public LLM gateway (backend llm_gateway → /v1/embeddings). Clients such
as Obliq build search indexes on these vectors and switch between this server and NEAR
AI Cloud, so the vectors must be exactly Qwen3-Embedding-0.6B's: last-token pooling
over LEFT-padded batches, L2-normalised, 1024 dims (the model card's reference code).
Instruction prefixes ("Instruct: …\\nQuery: …") arrive already applied — embedded as given.

Runs inside product_dream-svc-embed's image (NGC torch for sm_121 + transformers ≥ 4.51);
see inference/hosts/dgx-spark/embed-qwen3.sh. No `ads` imports (adapters never do).
"""

import os
import threading

import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Its own variable: the base image bakes EMBED_MODEL_ID=BAAI/bge-m3 in, which must not leak here.
MODEL_ID = os.environ.get("QWEN3_EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")
MAX_LEN = int(os.environ.get("MAX_LEN", "8192"))
MAX_INPUTS = 64
DEVICE = os.environ.get("DEVICE", "cuda")
if DEVICE.startswith("cuda") and not torch.cuda.is_available():
    DEVICE = "cpu"

app = FastAPI(title="embed-qwen3")
_lock = threading.Lock()
_m: dict | None = None


def _model() -> dict:
    global _m
    with _lock:
        if _m is None:
            from transformers import AutoModel, AutoTokenizer
            tok = AutoTokenizer.from_pretrained(MODEL_ID, padding_side="left")
            model = AutoModel.from_pretrained(
                MODEL_ID, torch_dtype=torch.float16 if DEVICE.startswith("cuda") else torch.float32)
            _m = {"tok": tok, "model": model.to(DEVICE).eval()}
        return _m


def _last_token_pool(hidden: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    if bool((mask[:, -1].sum() == mask.shape[0])):          # left padding: last column is real
        return hidden[:, -1]
    lengths = mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), lengths]


@torch.inference_mode()
def embed(texts: list[str]) -> tuple[list[list[float]], int]:
    m = _model()
    batch = m["tok"](texts, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt")
    batch = {k: v.to(DEVICE) for k, v in batch.items()}
    out = m["model"](**batch)
    vecs = F.normalize(_last_token_pool(out.last_hidden_state, batch["attention_mask"]).float(), p=2, dim=1)
    return vecs.cpu().tolist(), int(batch["attention_mask"].sum())


class EmbedRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    encoding_format: str | None = None   # "float" only; accepted for SDK compatibility


@app.on_event("startup")
def _warm() -> None:
    threading.Thread(target=lambda: embed(["warm-up"]), daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True, "loaded": _m is not None, "model": MODEL_ID, "dim": 1024, "device": DEVICE}


@app.post("/v1/embeddings")
def embeddings(req: EmbedRequest):
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    if not texts or len(texts) > MAX_INPUTS or not all(isinstance(t, str) for t in texts):
        raise HTTPException(400, f"input must be a string or 1..{MAX_INPUTS} strings")
    try:
        vecs, tokens = embed(texts)
    except Exception as e:  # noqa: BLE001 — a load/CUDA failure is a clean 503, not a crash
        raise HTTPException(503, f"embedding model unavailable: {type(e).__name__}: {e}")
    return {"object": "list", "model": MODEL_ID,
            "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)],
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}
