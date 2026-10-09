"""Kokoro-82M TTS — high-quality neural voiceover, professional MALE voice by default.
Replaces Piper on :8105; same product_dream gen-tts /generate contract, so the ad system's
tts_provider uses it unchanged. Runs on CPU (~5x real-time, tiny model) so it never competes
with the GPU models.

uk/ru requests are normalized (numbers/percent/currency to words, brand pronunciations,
optional stress marks — normalize.py) and routed through the per-lang engine registry
(engines.py: TTS_ENGINE_UK/TTS_ENGINE_RU, default piper). All other langs keep Kokoro.

Env: KOKORO_ONNX, KOKORO_VOICES, ASSETS_DIR, TTS_VOICE (default am_michael), PORT,
PIPER_VOICES_DIR, TTS_ENGINE_UK, TTS_ENGINE_RU.
"""

import asyncio
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from kokoro_onnx import Kokoro
from pydantic import BaseModel

import engines
import polish as polish_mod
from normalize import detect_registry_lang, normalize_for_tts, to_plus_stress

# Model assets are repo-owned (data/models/…) — the old product_dream paths are gone.
_MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "models")
KOKORO_ONNX = os.environ.get("KOKORO_ONNX", os.path.join(_MODELS_DIR, "kokoro", "kokoro-v1.0.onnx"))
KOKORO_VOICES = os.environ.get("KOKORO_VOICES", os.path.join(_MODELS_DIR, "kokoro", "voices-v1.0.bin"))
ASSETS_DIR = Path(os.environ.get("ASSETS_DIR", "./data/assets"))
DEFAULT_VOICE = os.environ.get("TTS_VOICE", "am_michael")  # deep, professional American male
MODEL = "kokoro-82m"
HOST_NAME = os.environ.get("HOST_NAME", "")
DEVICE = os.environ.get("DEVICE", "cpu")


def _capabilities(langs: dict, kokoro_loadable: bool) -> dict:
    ready = ["en"] if kokoro_loadable else []
    ready += [lang for lang in REGISTRY_LANGS if langs.get(lang, {}).get("loadable")]
    # which engine each language ACTUALLY runs here — a worker's TTS pool puts the
    # host with the brand voice first, and the Infra page shows it
    engines = {"en": "kokoro" if kokoro_loadable else None}
    engines.update({lang: langs.get(lang, {}).get("active") for lang in REGISTRY_LANGS})
    available = {lang: [n for n, ok in (langs.get(lang, {}).get("available") or {}).items() if ok]
                 for lang in REGISTRY_LANGS}
    return {"kind": "tts", "langs": ready, "engines": {k: v for k, v in engines.items() if v},
            "available_engines": available,
            "clone": any(langs.get(l, {}).get("active") == "f5" for l in REGISTRY_LANGS)}

# Kokoro covers its fixed language set; ru/uk route to the engine registry (Piper by
# default — real Russian and Ukrainian neural TTS, same onnxruntime stack) — the old
# fallback read Cyrillic text with an English voice, which is not a voiceover anyone
# can ship.
LANG_MAP = {"en": "en-us", "en-us": "en-us", "en-gb": "en-gb", "es": "es", "fr": "fr-fr",
            "it": "it", "pt": "pt-br", "hi": "hi", "ja": "ja", "zh": "cmn"}
REGISTRY_LANGS = ("uk", "ru")

_k: Kokoro | None = None
_k_lock = threading.Lock()  # synthesis runs in worker threads — load the model once


def _kok() -> Kokoro:
    global _k
    if _k is None:
        with _k_lock:
            if _k is None:
                _k = Kokoro(KOKORO_ONNX, KOKORO_VOICES)
    return _k


app = FastAPI(title="gen-tts-kokoro")

# GET /system (GPUs, load, RAM) for the admin Infra page — shared with the other adapters
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sysinfo import router as _system_router  # noqa: E402
app.include_router(_system_router)


class GenerateRequest(BaseModel):
    text: str
    lang: str = "en"
    voice: str | None = None
    product_id: str = "adhoc"
    # C3: brand-name -> Cyrillic replacement, applied to the TTS input only (uk/ru)
    pronunciations: dict[str, str] | None = None
    # C3: relative to ASSETS_DIR; per-brand voice reference for engines that clone
    ref_audio_path: str | None = None
    # TTS lab: per-request engine override (uk/ru registry names) + speed multiplier
    engine: str | None = None
    speed: float | None = None
    # Voice shaping. polish = post-processing preset (polish.PRESETS) that takes
    # the dry/robotic edge off; None = the TTS_POLISH default, "off" = raw.
    # expressiveness 0..1 widens pitch/energy variation on engines that model
    # them (radtts) — the cure for a monotone read. ambience mixes a ducked
    # background bed (polish.bed_names()) under the voice.
    polish: str | None = None
    expressiveness: float | None = None
    # depth 0..1 lowers the speaker's mean pitch in the MODEL (radtts f0_mean),
    # which is artefact-free where a DSP pitch shift would smear the formants
    depth: float | None = None
    ambience: str | None = None
    ambience_level_db: float = -26.0


def _shaping() -> dict:
    """Voice-shaping catalogue for the UI: polish presets and background beds."""
    return {"polish_presets": list(polish_mod.PRESETS),
            "polish_default": polish_mod.DEFAULT_PRESET,
            "ambiences": ["none", *polish_mod.bed_names()]}


@app.get("/health")
async def health():
    # Per-lang registry status + asset presence, so a fresh start with missing model
    # files is visible before the first /generate fails. Cheap on the event loop:
    # engine_status is file checks + cached import probes; the kokoro touch (which
    # loads the model on first call) runs in a worker thread.
    langs = {lang: engines.engine_status(lang) for lang in REGISTRY_LANGS}
    try:
        import verbalize
        langs["uk"]["verbalizer"] = verbalize.available()
    except ImportError:
        langs["uk"]["verbalizer"] = False
    kokoro_loadable = os.path.isfile(KOKORO_ONNX) and os.path.isfile(KOKORO_VOICES)
    try:
        voices = await asyncio.to_thread(lambda: _kok().get_voices())
        males = [v for v in voices if v.startswith(("am_", "bm_"))]
        return {"ok": True, "loaded": True, "mem_gb": 0.3, "model": MODEL, "engine": "kokoro",
                "host": HOST_NAME, "device": DEVICE,
                "capabilities": _capabilities(langs, kokoro_loadable),
                "default_voice": DEFAULT_VOICE, "voices": males,
                "engines": {"kokoro": {"loadable": kokoro_loadable}, **langs},
                **_shaping()}
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "loaded": False, "mem_gb": 0, "model": MODEL, "engine": "kokoro",
                "host": HOST_NAME, "device": DEVICE,
                "capabilities": _capabilities(langs, False),
                "engines": {"kokoro": {"loadable": kokoro_loadable}, **langs},
                "error": str(e)}


@app.get("/files/{path:path}")
async def get_file(path: str):
    # Serve generated files so backends on other tailnet machines can fetch results.
    f = (ASSETS_DIR / path).resolve()
    if not f.is_relative_to(ASSETS_DIR.resolve()) or not f.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(f)


def _kokoro_voice(voice: str | None) -> str:
    """A Kokoro voice this build actually has. Unknown names (e.g. a voice
    belonging to a uk engine) degrade to DEFAULT_VOICE instead of raising."""
    try:
        available = _kok().get_voices()
    except Exception:  # noqa: BLE001 — model not loadable; let create() report it
        return voice or DEFAULT_VOICE
    if voice and voice in available:
        return voice
    if voice:
        print(f"[gen-tts] kokoro has no voice {voice!r} — using {DEFAULT_VOICE!r}",
              flush=True)
    return DEFAULT_VOICE


def _shape(samples, sr: int, req: "GenerateRequest"):
    """polish -> ambience. Both are best-effort and return the input untouched
    on any failure, so shaping can never turn a good voiceover into a 502."""
    samples = polish_mod.polish(samples, sr, req.polish)
    samples = polish_mod.ambience(samples, sr, req.ambience, req.ambience_level_db)
    return samples, sr


@app.post("/generate")
async def generate(req: GenerateRequest):
    # Script guard: Cyrillic text must NEVER be read by an English Kokoro
    # voice, whatever lang the caller sent (wrong lab chip, stale project
    # default). Detected from the text itself; explicit uk/ru is honored.
    eff_lang = detect_registry_lang(req.text, req.lang)
    if eff_lang != req.lang:
        print(f"[gen-tts] lang={req.lang!r} but the text is Cyrillic — "
              f"rerouting to {eff_lang!r}", flush=True)
        req.lang = eff_lang
    out_dir = ASSETS_DIR / req.product_id
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = req.voice or DEFAULT_VOICE
    model = MODEL
    try:
        t0 = time.time()
        if req.lang in REGISTRY_LANGS:
            text = normalize_for_tts(req.text, req.lang, req.pronunciations)
            ref = None
            if req.ref_audio_path:
                p = (ASSETS_DIR / req.ref_audio_path).resolve()
                if p.is_relative_to(ASSETS_DIR.resolve()) and p.is_file():
                    ref = str(p)
            if req.engine and (req.engine not in engines.ENGINE_CLASSES
                               or not engines._instance(req.engine, req.lang).available()):
                # The caller pinned an engine this host cannot run (a brand voice that
                # lives on another box). Degrading to a different voice would be a
                # silent lie; 503 lets the worker's pool fail over to a host that has it.
                raise HTTPException(503, f"engine {req.engine!r} is not available on this host for {req.lang}")
            eng = engines.get_engine(req.lang, override=req.engine)
            # multi-second GPU/CPU work — off the event loop so /health and other
            # requests stay responsive
            samples, sr, voice = await asyncio.to_thread(
                eng.synthesize, text, req.voice, ref_audio_path=ref, speed=req.speed,
                expressiveness=req.expressiveness, depth=req.depth)
            model = eng.produced_by  # what actually ran (piper after a mid-request degrade)
            # Shaping runs on every engine's output, in a worker thread — the
            # chain is numpy/scipy and costs milliseconds, but it is still CPU.
            samples, sr = await asyncio.to_thread(_shape, samples, sr, req)
        else:
            lang = LANG_MAP.get(req.lang, "en-us")
            # A voice name from another language's engine must never be fatal.
            # It used to be: a uk project's "mykyta" reaching this path made
            # Kokoro raise, the caller 502'd, and the short shipped mute. Fall
            # back to the (male-verified) default and say so.
            voice = await asyncio.to_thread(_kokoro_voice, voice)
            samples, sr = await asyncio.to_thread(
                lambda: _kok().create(req.text, voice=voice, speed=1.0, lang=lang))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"tts synth failed: {type(e).__name__}: {e}")
    fn = f"vo-{int(time.time())}-{uuid.uuid4().hex[:6]}.wav"
    sf.write(str(out_dir / fn), samples, sr)
    return {
        "audio": {"path": f"{req.product_id}/{fn}", "sample_rate": int(sr),
                  "elapsed_s": round(time.time() - t0, 1), "duration_s": round(len(samples) / sr, 1)},
        "model": model, "voice": voice,
        # what shaping actually ran, so a take in the lab is reproducible
        "polish": (req.polish or polish_mod.DEFAULT_PRESET) if req.lang in REGISTRY_LANGS else None,
        "ambience": req.ambience or None,
        # what was ACTUALLY spoken after normalization (uk/ru) — the QA loop
        # compares the ASR transcript against this, not the raw input
        "text": text if req.lang in REGISTRY_LANGS else req.text,
        # same text with наголос as "+" before the stressed vowel: a combining
        # acute is nearly invisible in a browser, so the TTS lab shows this to
        # let the operator SEE where the stress landed (and copy/edit it back
        # into the input — "+" is accepted on the way in for every uk engine).
        "stress_text": to_plus_stress(text) if req.lang == "uk" else None,
    }


# --- ASR round-trip QA (faster-whisper, optional extra) ----------------------

WHISPER_DIR = os.environ.get(
    "WHISPER_MODEL_DIR", os.path.join(_MODELS_DIR, "whisper", "medium"))
_whisper = None
_whisper_lock = threading.Lock()


def _whisper_model():
    global _whisper
    if _whisper is None:
        with _whisper_lock:
            if _whisper is None:
                from faster_whisper import WhisperModel
                _whisper = WhisperModel(WHISPER_DIR, device="cpu", compute_type="int8")
    return _whisper


class TranscribeRequest(BaseModel):
    path: str          # relative to ASSETS_DIR
    lang: str = "uk"
    word_timestamps: bool = False  # captions need per-word timing; QA doesn't


@app.post("/transcribe")
async def transcribe(req: TranscribeRequest):
    """Transcribe one generated wav (ASR = faster-whisper medium, CPU int8).
    Used by the backend's tts_qa job (text only) and by the shorts caption
    builder (word_timestamps=true adds words: [{word, start, end}])."""
    f = (ASSETS_DIR / req.path).resolve()
    if not f.is_relative_to(ASSETS_DIR.resolve()) or not f.is_file():
        raise HTTPException(404, "audio not found")
    if not os.path.isdir(WHISPER_DIR):
        raise HTTPException(503, f"whisper model missing ({WHISPER_DIR})")
    try:
        def _run():
            segments, info = _whisper_model().transcribe(
                str(f), language=req.lang, beam_size=1, vad_filter=False,
                word_timestamps=req.word_timestamps)
            parts, words = [], []
            for s in segments:
                parts.append(s.text.strip())
                for wd in (s.words or []) if req.word_timestamps else []:
                    words.append({"word": wd.word.strip(),
                                  "start": round(wd.start, 3),
                                  "end": round(wd.end, 3)})
            return " ".join(parts).strip(), words, info
        text_out, words, info = await asyncio.to_thread(_run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"transcribe failed: {type(e).__name__}: {e}")
    out = {"text": text_out, "language": info.language,
           "duration_s": round(info.duration, 1)}
    if req.word_timestamps:
        out["words"] = words
    return out
