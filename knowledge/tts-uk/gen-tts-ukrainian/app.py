"""ukrainian-tts (robinhad) sidecar — ISOLATED service on :8106.

Runs in its OWN venv (.venv-uktts): the ESPnet dependency tree must never touch
the shared venv that runs every other host service. The gen-tts-kokoro engine
registry proxies to this over HTTP ("ukrainian_tts" engine), so the TTS lab
gets it as a chip with the five voices — nothing else in the system knows it
exists.

License: code MIT, MODEL WEIGHTS GPL v3 (server-side use is fine, generated
audio is not GPL; never ship the weights inside a distributed product).

Env: UKTTS_MODEL_DIR (default <repo>/data/models/uktts), PORT (default 8106).
Startup chdir's into the model dir — espnet resolves feats_stats.npz from CWD.
"""

import io
import os
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MODEL_DIR = os.environ.get("UKTTS_MODEL_DIR", os.path.join(_REPO, "data", "models", "uktts"))

VOICES = ("tetiana", "mykyta", "lada", "dmytro", "oleksa")
DEFAULT_VOICE = os.environ.get("UKTTS_VOICE", "dmytro")
SAMPLE_RATE = 22050

app = FastAPI(title="gen-tts-ukrainian")

_tts = None
_lock = threading.Lock()


def _model():
    global _tts
    if _tts is None:
        with _lock:
            if _tts is None:
                os.chdir(MODEL_DIR)  # espnet loads feats_stats.npz relative to CWD
                from ukrainian_tts.tts import TTS
                _tts = TTS(device="cpu", cache_folder=".")
    return _tts


class GenerateRequest(BaseModel):
    text: str
    voice: str | None = None


@app.get("/health")
async def health():
    return {"loaded": _tts is not None, "model": "ukrainian-tts (espnet)",
            "voices": list(VOICES), "default_voice": DEFAULT_VOICE,
            "model_dir_ok": os.path.isfile(os.path.join(MODEL_DIR, "model.pth")),
            "license": "model weights GPL-3.0"}


@app.post("/generate")
async def generate(req: GenerateRequest):
    """Synthesize and return raw WAV bytes (22050 Hz mono)."""
    if not os.path.isfile(os.path.join(MODEL_DIR, "model.pth")):
        raise HTTPException(503, f"model missing in {MODEL_DIR}")
    voice = req.voice if req.voice in VOICES else DEFAULT_VOICE
    try:
        from ukrainian_tts.tts import Stress
        import anyio

        def _run() -> bytes:
            buf = io.BytesIO()
            t0 = time.time()
            _model().tts(req.text, voice, Stress.Dictionary.value, buf)
            buf.seek(0)
            data = buf.read()
            print(f"synth voice={voice} bytes={len(data)} elapsed={time.time()-t0:.1f}s", flush=True)
            return data

        wav = await anyio.to_thread.run_sync(_run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"synth failed: {type(e).__name__}: {e}")
    return Response(content=wav, media_type="audio/wav",
                    headers={"X-Voice": voice, "X-Sample-Rate": str(SAMPLE_RATE)})
