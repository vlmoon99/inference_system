"""mt-gate — translation quality gate: MetricX-24 (Apache-2.0) in reference-free
mode, on CPU by default (docs/AGENTIC_PLAN.md §2.4).

POST /score {"items": [{"source": "<English>", "candidate": "<translation>"}]}
  -> {"scores": [float, ...]}          0 = perfect, 25 = worst (MQM-style error score)
GET  /health -> {"loaded": bool, "model": str, "device": str}

Env: GATE_MODEL (google/metricx-24-hybrid-large-v2p6), GATE_TOKENIZER
(google/mt5-large), GATE_DEVICE (cpu|cuda), GATE_MAX_LEN (1536), PORT (8012).
The model loads lazily on the first /score; /health says whether it is up.
No `ads` imports: this is an inference adapter like the others.
"""

import os
import threading
import time

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from metricx_models import MT5ForRegression

MODEL_ID = os.environ.get("GATE_MODEL", "google/metricx-24-hybrid-large-v2p6")
TOKENIZER_ID = os.environ.get("GATE_TOKENIZER", "google/mt5-large")
DEVICE = os.environ.get("GATE_DEVICE", "cpu")
MAX_LEN = int(os.environ.get("GATE_MAX_LEN", "1536"))

app = FastAPI(title="mt-gate", version="1")
_lock = threading.Lock()
_state: dict = {"model": None, "tok": None, "error": None}


class Item(BaseModel):
    source: str = Field(max_length=6000)
    candidate: str = Field(max_length=6000)


class ScoreIn(BaseModel):
    items: list[Item] = Field(min_length=1, max_length=64)


def _load():
    with _lock:
        if _state["model"] is not None:
            return
        try:
            from transformers import AutoTokenizer
            t0 = time.time()
            tok = AutoTokenizer.from_pretrained(TOKENIZER_ID)
            # MetricX checkpoints carry an UNTIED lm_head (the regression read-out).
            # transformers >= 5 marks the mT5 config as tied, which makes the
            # vendored forward rescale the decoder output by d_model**-0.5 and
            # squashes every score to ~0-1. Verified 2026-09-26: with the rescale
            # a wrong-product translation scored 0.28/25; without, 9.1 (gibberish 22.8).
            model = MT5ForRegression.from_pretrained(MODEL_ID, torch_dtype=torch.float32,
                                                     tie_word_embeddings=False)
            model.config.tie_word_embeddings = False
            model.to(DEVICE).eval()
            _state.update(model=model, tok=tok, error=None)
            print(f"[mt-gate] loaded {MODEL_ID} on {DEVICE} in {time.time() - t0:.1f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            _state["error"] = f"{type(e).__name__}: {e}"
            raise


def _inputs(items: list[Item]) -> list[str]:
    # Reference-free ("QE") template from metricx24/predict.py; the trailing
    # EOS token is removed after tokenisation, as the reference code does.
    return [f"source: {i.source} candidate: {i.candidate}" for i in items]


@torch.inference_mode()
def _score(items: list[Item]) -> list[float]:
    # One item per forward, as the reference predict.py does (batch_size 1):
    # transformers 5's T5 attention rejects hand-padded batches, and a single
    # short segment scores in well under a second on the CPU.
    tok, model = _state["tok"], _state["model"]
    out: list[float] = []
    for text in _inputs(items):
        enc = tok(text, max_length=MAX_LEN, truncation=True, padding=False)
        ids = enc["input_ids"][:-1]                       # drop EOS
        mask = enc["attention_mask"][:-1]
        input_ids = torch.tensor([ids], device=DEVICE)
        attention = torch.tensor([mask], device=DEVICE)
        preds = model(input_ids=input_ids, attention_mask=attention).predictions
        out.append(float(preds.detach().cpu().reshape(-1)[0]))
    return out


@app.get("/health")
def health():
    return {"loaded": _state["model"] is not None, "model": MODEL_ID, "device": DEVICE,
            "error": _state["error"]}


@app.post("/score")
def score(body: ScoreIn):
    if _state["model"] is None:
        try:
            _load()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(503, f"mt-gate: model not loaded: {e}") from e
    t0 = time.time()
    scores = _score(body.items)
    print(f"[mt-gate] scored {len(scores)} item(s) in {time.time() - t0:.2f}s", flush=True)
    return {"scores": scores, "model": MODEL_ID}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8012")))
