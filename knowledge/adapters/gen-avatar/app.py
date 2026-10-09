"""gen-avatar — the talking-spokesperson service: one photo + one audio file ->
a video of that person speaking it (InfiniteTalk on Wan2.1-I2V-14B, Apache-2.0,
through kijai's ComfyUI-WanVideoWrapper). Contract v1 like the other media
services (inference/CONTRACT.md): /health (+capabilities), /generate, /files.

The voice is NOT made here — the backend's TTS makes the voiceover the way it
does for a short, and sends it as audio_path / audio_b64. The photo comes from
the customer's library with a consent tick (docs/AGENTIC_PLAN.md: only the
client's own face). Output size follows the requested width/height (portrait
480x832 for shorts), 25 fps, length = the audio's length (81-frame windows,
motion_frame overlap), audio muxed in by ComfyUI's CreateVideo.

Memory: the 14B model runs with block swap to the CPU side of the unified pool
and the LLM is put to sleep for the render (VLLM_SLEEP_URL), like gen-video's
14B path. No `ads` imports.
"""

import asyncio
import base64
import binascii
import json
import math
import os
import random
import shutil
import time
import uuid
import wave
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

COMFY_URL = os.environ.get("COMFY_URL", "http://localhost:8188").rstrip("/")
COMFY_OUT = Path(os.environ.get("COMFY_OUTPUT", "/home/server/ComfyUI/output"))
COMFY_IN = Path(os.environ.get("COMFY_INPUT", "/home/server/ComfyUI/input"))
ASSETS_DIR = Path(os.environ.get("ASSETS_DIR", "./data/assets"))
WF_DIR = Path(os.environ.get("WF_DIR", str(Path(__file__).resolve().parents[2] / "workflows")))
WF = WF_DIR / "infinitetalk_i2v_api.json"
MODEL = "infinitetalk-wan21-14b"
WEIGHT = "Wan2_1-InfiniTetalk-Single_fp16.safetensors"
HOST_NAME = os.environ.get("HOST_NAME", "")
DEVICE = os.environ.get("DEVICE", "cuda")
VLLM_SLEEP_URL = os.environ.get("VLLM_SLEEP_URL", "").rstrip("/")
BLOCK_SWAP = int(os.environ.get("AVATAR_BLOCK_SWAP", "10"))
FPS = 25
MAX_SECONDS = float(os.environ.get("AVATAR_MAX_SECONDS", "60"))

app = FastAPI(title="gen-avatar-comfy")


class GenerateRequest(BaseModel):
    prompt: str = "a person talking to the camera, natural expression, steady framing"
    product_id: str = "adhoc"
    # the face: a path relative to ASSETS_DIR on this host, or the bytes for a remote worker
    ref_image_path: str | None = None
    ref_image_b64: str | None = None
    # the speech: same two forms; wav (TTS output) or mp3
    audio_path: str | None = None
    audio_b64: str | None = None
    audio_ext: str = "wav"
    width: int = 480
    height: int = 832
    steps: int = 6
    seed: int | None = None


def _stage(rel: str | None, b64: str | None, prefix: str, default_ext: str) -> Path:
    COMFY_IN.mkdir(parents=True, exist_ok=True)
    if b64:
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, f"{prefix}: not valid base64")
        name = f"{prefix}-{uuid.uuid4().hex[:8]}{Path(rel or '').suffix or default_ext}"
        (COMFY_IN / name).write_bytes(data)
        return COMFY_IN / name
    src = Path(rel or "")
    if not src.is_absolute():
        src = ASSETS_DIR / (rel or "")
    if not rel or not src.is_file():
        raise HTTPException(404, f"{prefix} not found: {src} (a remote worker must send it as base64)")
    name = f"{prefix}-{uuid.uuid4().hex[:8]}{src.suffix or default_ext}"
    shutil.copy(src, COMFY_IN / name)
    return COMFY_IN / name


def _audio_seconds(p: Path) -> float:
    """wav via the stdlib; anything else via ffprobe when present; else assume the cap."""
    if p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as w:
                return w.getnframes() / float(w.getframerate() or 1)
        except wave.Error:
            pass
    import subprocess
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True,
                             timeout=20).stdout.strip()
        return float(out)
    except Exception:  # noqa: BLE001
        return MAX_SECONDS


def _speech_16k(audio: Path) -> Path:
    """A 16 kHz mono copy for the wav2vec audio encoder. The node would resample
    with torchaudio, whose native library does not load in the Spark's ComfyUI
    container (aarch64 ABI mismatch) — at 16 kHz it skips that call. The
    original file still goes into the final video, so the voice keeps its rate."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    data, sr = sf.read(str(audio), always_2d=True)
    mono = data.mean(axis=1)
    if sr != 16000:
        g = np.gcd(int(sr), 16000)
        mono = resample_poly(mono, 16000 // g, int(sr) // g)
    out = audio.with_name(audio.stem + "-16k.wav")
    sf.write(str(out), mono.astype("float32"), 16000, subtype="PCM_16")
    return out


def _build(req: GenerateRequest, image: Path, audio: Path, seconds: float) -> dict:
    seed = req.seed if req.seed is not None else random.randint(0, 2**31 - 1)
    # 81-frame windows at 25 fps; the wrapper trims to the audio's real length
    max_frames = int(math.ceil(min(seconds, MAX_SECONDS) * FPS)) + 1
    subs = {
        "__IMAGE__": image.name, "__AUDIO__": audio.name, "__AUDIO16K__": _speech_16k(audio).name,
        "__PROMPT__": req.prompt,
        "__WIDTH__": req.width, "__HEIGHT__": req.height, "__STEPS__": req.steps,
        "__SEED__": seed, "__MAX_FRAMES__": max_frames, "__BLOCK_SWAP__": BLOCK_SWAP,
        "__PREFIX__": f"adtalk_{req.product_id[:8]}",
    }
    wf = json.loads(WF.read_text())
    for node in wf.values():
        for k, v in list(node["inputs"].items()):
            if isinstance(v, str) and v in subs:
                node["inputs"][k] = subs[v]
    return wf, seed


async def _vllm(path: str) -> None:
    if not VLLM_SLEEP_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{VLLM_SLEEP_URL}{path}")
    except httpx.HTTPError as e:
        print(f"[gen-avatar] vllm {path} failed (non-fatal): {e}", flush=True)


async def _loaded() -> bool:
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(f"{COMFY_URL}/object_info/MultiTalkModelLoader")
            return r.status_code == 200 and WEIGHT in r.text
    except httpx.HTTPError:
        return False


@app.get("/health")
async def health():
    ok = await _loaded()
    return {
        "ok": True, "loaded": ok, "mem_gb": 0, "model": MODEL,
        "host": HOST_NAME, "engine": "comfy", "device": DEVICE,
        "capabilities": {"kind": "avatar", "models": [MODEL] if ok else [], "audio": True,
                         "fps": FPS, "max_seconds": MAX_SECONDS, "licence": "apache-2.0"},
    }


@app.get("/files/{path:path}")
async def get_file(path: str):
    f = (ASSETS_DIR / path).resolve()
    if not f.is_relative_to(ASSETS_DIR.resolve()) or not f.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(f)


@app.post("/generate")
async def generate(req: GenerateRequest):
    if not await _loaded():
        raise HTTPException(503, f"{WEIGHT} is not visible to ComfyUI (weights missing or wrapper not loaded)")
    if not (req.ref_image_path or req.ref_image_b64):
        raise HTTPException(422, "ref_image_path or ref_image_b64 is required (the face)")
    if not (req.audio_path or req.audio_b64):
        raise HTTPException(422, "audio_path or audio_b64 is required (the speech)")
    image = _stage(req.ref_image_path, req.ref_image_b64, "adface", ".png")
    audio = _stage(req.audio_path, req.audio_b64, "adspeech", f".{req.audio_ext.lstrip('.')}")
    seconds = _audio_seconds(audio)
    if seconds > MAX_SECONDS:
        raise HTTPException(422, f"audio is {seconds:.0f}s; this host renders up to {MAX_SECONDS:.0f}s")
    wf, seed = _build(req, image, audio, seconds)
    out_dir = ASSETS_DIR / req.product_id
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    await _vllm("/sleep?level=1")
    vid = None
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{COMFY_URL}/prompt", json={"prompt": wf})
            if r.status_code != 200:
                raise HTTPException(502, f"ComfyUI rejected workflow: {r.text[:400]}")
            pid = r.json()["prompt_id"]
            for _ in range(1800):  # up to an hour: a 60 s clip is many 81-frame windows
                await asyncio.sleep(2)
                try:
                    entry = (await c.get(f"{COMFY_URL}/history/{pid}")).json().get(pid)
                except httpx.HTTPError:
                    continue
                if not entry:
                    continue
                if entry.get("status", {}).get("status_str") == "error":
                    raise HTTPException(502, f"ComfyUI error: {entry['status'].get('messages', [])[-2:]}")
                for o in entry.get("outputs", {}).values():
                    vids = o.get("images") or o.get("videos") or o.get("gifs") or []
                    if vids:
                        vid = vids[0]
                        break
                if vid:
                    break
    finally:
        await _vllm("/wake_up")
    if not vid:
        raise HTTPException(504, "ComfyUI generation timed out")
    src = COMFY_OUT / vid.get("subfolder", "") / vid["filename"]
    fn = f"talk-{int(time.time())}-{seed}{src.suffix or '.mp4'}"
    shutil.copy(src, out_dir / fn)
    return {"video": {"path": f"{req.product_id}/{fn}", "seed": seed, "seconds": round(seconds, 2),
                      "fps": FPS, "elapsed_s": round(time.time() - t0, 1)}, "model": MODEL}
