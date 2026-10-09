"""ComfyUI-backed video service — Wan2.2 A14B (14B MoE) + Lightning 4-step, replacing the
LTX draft. Speaks the product_dream gen-video /generate contract, so the ad system's
video_provider (GEN_VIDEO_URL=:8104) uses it unchanged — the scale seam in action.

Drives the ComfyUI headless backend (:8188): fills a workflow template (t2v, or i2v when a
reference image is given so the FLUX keyframe becomes the first frame), submits, polls
/history, and copies the finished MP4 into the shared ASSETS_DIR.

Env: COMFY_URL, COMFY_OUTPUT, COMFY_INPUT, ASSETS_DIR, WF_DIR, WAN_MODEL (default: first of
VIDEO_MODELS, else 5b), VIDEO_MODELS, VLLM_SLEEP_URL, PORT.
"""

import asyncio
import json
import os
import sys
import random
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal

import base64
import binascii

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

COMFY_URL = os.environ.get("COMFY_URL", "http://localhost:8188").rstrip("/")
COMFY_OUT = Path(os.environ.get("COMFY_OUTPUT", "/home/server/ComfyUI/output"))
COMFY_IN = Path(os.environ.get("COMFY_INPUT", "/home/server/ComfyUI/input"))
ASSETS_DIR = Path(os.environ.get("ASSETS_DIR", "./data/assets"))
WF_DIR = Path(os.environ.get("WF_DIR", str(Path(__file__).resolve().parents[2] / "workflows")))
# 5b = Wan2.2-TI2V-5B (light, stays resident) | 14b = Wan2.2-A14B Lightning (max quality, heavier)
# Service-wide default; each request may override via GenerateRequest.model.
HOST_NAME = os.environ.get("HOST_NAME", "")
DEVICE = os.environ.get("DEVICE", "cuda")
# Which of 5b | 14b | ltx2 this host serves. Empty = detect from ComfyUI's loader
# option lists (weights actually present); set explicitly to pin it.
VIDEO_MODELS_ENV = os.environ.get("VIDEO_MODELS", "").strip()
# The default for a request without `model`: WAN_MODEL when set, else the first
# pinned VIDEO_MODELS entry (an ltx2-only host must not fall back to Wan and 422).
WAN_MODEL = (os.environ.get("WAN_MODEL", "").strip()
             or (VIDEO_MODELS_ENV.split(",")[0].strip() if VIDEO_MODELS_ENV else "")
             or "5b")
MODEL = "ltx-2.5-22b-distilled" if WAN_MODEL == "ltx2" else f"wan2.2-{WAN_MODEL}"

# weight-file fingerprints -> contract model ids
_FINGERPRINTS = {
    "ltx2": ("ltx-2.5", "ltx-2_5", "LTX-2.5"),
    "5b": ("wan2.2_ti2v_5b", "wan2.2_ti2v_5B", "ti2v_5B"),
    "14b": ("wan2.2_t2v_high_noise_14b", "wan2.2_t2v_high_noise_14B", "a14b", "A14B"),
}
_LOADER_NODES = ("UNETLoader", "UnetLoaderGGUF", "CheckpointLoaderSimple")


def _models_from_options(option_lists: list[list[str]]) -> list[str]:
    names = " ".join(str(n) for opts in option_lists for n in opts)
    return [m for m, needles in _FINGERPRINTS.items() if any(n in names for n in needles)]


async def _detect_models() -> list[str]:
    if VIDEO_MODELS_ENV:
        return [m.strip() for m in VIDEO_MODELS_ENV.split(",") if m.strip()]
    lists: list[list[str]] = []
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            for node in _LOADER_NODES:
                r = await c.get(f"{COMFY_URL}/object_info/{node}")
                if r.status_code != 200:
                    continue
                info = r.json().get(node, {}).get("input", {}).get("required", {})
                for spec in info.values():  # {"unet_name": [[...options...]], ...}
                    if isinstance(spec, list) and spec and isinstance(spec[0], list):
                        lists.append([str(x) for x in spec[0]])
    except (httpx.HTTPError, ValueError):
        return []
    return _models_from_options(lists)

# vLLM sleep-mode base URL (e.g. http://100.64.0.1:8010). When set, the LLM is put to
# sleep around 14B renders to free unified memory; requires the vLLM server to run with
# --enable-sleep-mode. Unset => feature off.
VLLM_SLEEP_URL = os.environ.get("VLLM_SLEEP_URL", "").rstrip("/")
# Where ComfyUI runs the LTX text encoder (Gemma-4-12B int8, ~13 GB). The workflows pin
# `cpu` (55–65 s per new prompt) because on a shared Spark the GPU copy peaked at the
# earlyoom line. `default` = the GPU; set only on a host that measured the headroom.
LTX_TEXT_ENCODER_DEVICE = os.environ.get("LTX_TEXT_ENCODER_DEVICE", "cpu")

# GPU RIFE frame interpolation (smooth motion) via ComfyUI's native VFI nodes.
RIFE_ENABLED = os.environ.get("RIFE", "1") == "1"
RIFE_MODEL = os.environ.get("RIFE_MODEL", "rife_v4.26-bf16.safetensors")
RIFE_MULT = int(os.environ.get("RIFE_MULT", "2"))

app = FastAPI(title="gen-video-comfy")

# GET /system (GPUs, load, RAM) for the admin Infra page — shared with the other adapters
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sysinfo import router as _system_router  # noqa: E402
app.include_router(_system_router)


class GenerateRequest(BaseModel):
    prompt: str
    product_id: str = "adhoc"
    ref_image_path: str | None = None  # relative to ASSETS_DIR -> image-to-video
    # The same image as base64 bytes — for a worker on ANOTHER machine whose
    # ASSETS_DIR this host cannot see (library photos, campaign photos). Wins over
    # ref_image_path when both are given; older adapters ignore the field.
    ref_image_b64: str | None = None
    quality: str = "draft"
    width: int = 480
    height: int = 832
    num_frames: int = 81  # Wan wants 4k+1; 81 = ~3.4s @ 24fps
    seed: int | None = None
    # 5b/14b = Wan2.2 (ComfyUI); ltx2 = LTX-2.5 22B distilled int8 (Lightricks,
    # native synced audio+video, two-stage upscale). None => WAN_MODEL env default.
    model: Literal["5b", "14b", "ltx2"] | None = None


def _wf_file(is_i2v: bool, model: str) -> Path:
    if model == "ltx2":
        return WF_DIR / f"ltx25_{'i2v' if is_i2v else 't2v'}_api.json"
    if model == "14b":
        return WF_DIR / f"wan22_a14b_{'i2v' if is_i2v else 't2v'}_lightning_api.json"
    return WF_DIR / f"wan22_ti2v_5b{'_i2v' if is_i2v else ''}_api.json"


def _build_ltx(req: GenerateRequest, hero: str | None) -> dict:
    """LTX-2.5 workflows are placeholder-templated (converted from the official
    ComfyUI subgraph template): substitute __TOKENS__ instead of walking node types.
    The graph renders at half-res, latent-upscales 2x, refines, and decodes video
    AND synchronized audio in one pass (24 fps, frames = 24*s + 1)."""
    fps = 24
    subs = {
        "__PROMPT__": req.prompt or "",
        "__WIDTH__": req.width,
        "__HEIGHT__": req.height,
        "__WIDTH_HALF__": req.width // 2,
        "__HEIGHT_HALF__": req.height // 2,
        "__FRAMES__": req.num_frames,
        "__FRAME_RATE__": fps,
        "__SEED__": req.seed if req.seed is not None else random.randint(0, 2**31 - 1),
        "__IMAGE__": hero or "",
        "__PREFIX__": f"adltx_{req.product_id[:8]}",
    }
    wf = json.loads(_wf_file(bool(hero), "ltx2").read_text())
    for node in wf.values():
        ins = node.get("inputs", {})
        for k, v in ins.items():
            if isinstance(v, str) and v in subs:
                ins[k] = subs[v]
        if node.get("class_type") == "CLIPLoader":
            ins["device"] = LTX_TEXT_ENCODER_DEVICE
    return wf


def _build(req: GenerateRequest, hero: str | None, model: str) -> dict:
    if model == "ltx2":
        return _build_ltx(req, hero)
    wf = json.loads(_wf_file(bool(hero), model).read_text())
    for node in wf.values():
        ct = node.get("class_type")
        ins = node.setdefault("inputs", {})
        if ct == "CLIPTextEncode" and "low quality" not in str(ins.get("text", "")):
            ins["text"] = req.prompt or ins.get("text", "")  # positive prompt
        elif ct == "KSamplerAdvanced":
            ins["noise_seed"] = req.seed if req.seed is not None else random.randint(0, 2**31 - 1)
        elif ct == "KSampler":
            ins["seed"] = req.seed if req.seed is not None else random.randint(0, 2**31 - 1)
        elif ct in ("EmptyHunyuanLatentVideo", "WanImageToVideo", "Wan22ImageToVideoLatent"):
            ins["width"], ins["height"], ins["length"] = req.width, req.height, req.num_frames
        elif ct == "SaveVideo":
            ins["filename_prefix"] = f"adwan_{req.product_id[:8]}"
        elif ct == "LoadImage" and hero:
            ins["image"] = hero
    return wf


def _add_rife(wf: dict) -> dict:
    """Insert GPU RIFE interpolation between VAEDecode and the video output: the decoded
    frames go through FrameInterpolate (multiplier=RIFE_MULT), and CreateVideo's fps is
    scaled up to match — smoother, more natural motion. No-op if the graph shape differs."""
    cv_id = next((k for k, n in wf.items() if n.get("class_type") == "CreateVideo"), None)
    if not cv_id:
        return wf
    src = wf[cv_id].get("inputs", {}).get("images")
    if not isinstance(src, list):
        return wf
    wf["rife_loader"] = {"class_type": "FrameInterpolationModelLoader",
                         "inputs": {"model_name": RIFE_MODEL}}
    wf["rife_interp"] = {"class_type": "FrameInterpolate",
                         "inputs": {"interp_model": ["rife_loader", 0], "images": src,
                                    "multiplier": RIFE_MULT}}
    wf[cv_id]["inputs"]["images"] = ["rife_interp", 0]
    try:
        wf[cv_id]["inputs"]["fps"] = float(wf[cv_id]["inputs"].get("fps", 24)) * RIFE_MULT
    except (TypeError, ValueError):
        wf[cv_id]["inputs"]["fps"] = 24.0 * RIFE_MULT
    return wf


async def _vllm(path: str) -> None:
    """Best-effort POST to the vLLM server's sleep-mode endpoints. Failures are logged
    and never fail the render; no-op when VLLM_SLEEP_URL is unset."""
    if not VLLM_SLEEP_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{VLLM_SLEEP_URL}{path}")
            if r.status_code >= 400:
                print(f"[gen-video-comfy] vllm {path} -> {r.status_code} (non-fatal)", flush=True)
    except httpx.HTTPError as e:
        print(f"[gen-video-comfy] vllm {path} failed (non-fatal): {e}", flush=True)


def _stage_image(rel: str | None, b64: str | None = None) -> str:
    COMFY_IN.mkdir(parents=True, exist_ok=True)
    if b64:
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "ref_image_b64 is not valid base64")
        suffix = Path(rel or "").suffix or ".png"
        name = f"adhero-{uuid.uuid4().hex[:8]}{suffix}"
        (COMFY_IN / name).write_bytes(data)
        return name
    src = Path(rel or "")
    if not src.is_absolute():
        src = ASSETS_DIR / rel
    if not src.is_file():
        raise HTTPException(404, f"ref image not found: {src} (a remote worker must send ref_image_b64)")
    name = f"adhero-{uuid.uuid4().hex[:8]}{src.suffix or '.png'}"
    shutil.copy(src, COMFY_IN / name)
    return name


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            ok = (await c.get(f"{COMFY_URL}/system_stats")).status_code == 200
    except httpx.HTTPError:
        ok = False
    models = await _detect_models() if ok else []
    return {
        "warm": _keepwarm_state["warm"] if _keepwarm_state["mode"] != "off" else None,
        "ok": True, "loaded": ok, "mem_gb": 0, "model": MODEL,
        "host": HOST_NAME, "engine": "comfy", "device": DEVICE,
        "capabilities": {"kind": "video", "models": models, "audio": "ltx2" in models,
                         "i2v": True, "max_frames": 121 if "ltx2" in models else 81},
    }


@app.get("/files/{path:path}")
async def get_file(path: str):
    # Serve generated files so backends on other tailnet machines can fetch results.
    f = (ASSETS_DIR / path).resolve()
    if not f.is_relative_to(ASSETS_DIR.resolve()) or not f.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(f)


def _should_evict(model: str) -> bool:
    """Clear ComfyUI's models before this render?

    Wan 14B: always (it needs the whole pool). LTX-2.5: NO — ComfyUI keeps the
    transformer and the Gemma encoder resident across consecutive ltx2 renders, and a
    warm render is ~105 s versus ~200 s after an eviction (measured 2026-09-05,
    inference/hosts/dgx-spark/README.md). Any other workflow landing in between
    (an image job) evicts LTX by itself, so skipping /free never costs memory it
    would otherwise have freed. VIDEO_EVICT_BEFORE_LTX=1 restores the old behaviour."""
    if model == "14b":
        return True
    if model == "ltx2":
        return os.environ.get("VIDEO_EVICT_BEFORE_LTX", "0") == "1"
    return False


@app.post("/generate")
async def generate(req: GenerateRequest):
    model = req.model or WAN_MODEL  # per-request override, else service default
    served = await _detect_models()
    if served and model not in served:
        # contract: a model this host does not serve is a client error, never a
        # silent substitution — the worker picks from /health before asking.
        raise HTTPException(422, f"model {model!r} is not served by this host (serves {served})")
    hero = (_stage_image(req.ref_image_path, req.ref_image_b64)
            if (req.ref_image_path or req.ref_image_b64) else None)
    wf = _build(req, hero, model)
    if RIFE_ENABLED and model != "ltx2":
        # ltx2 is native 24fps with synced audio; skip interpolation there.
        wf = _add_rife(wf)
    out_dir = ASSETS_DIR / req.product_id
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 14B Wan and the ~40GB LTX-2.5 stack both want the memory headroom.
    is_14b = model in ("14b", "ltx2")
    if is_14b:
        # Free unified memory for the heavy render; woken back up in the finally below.
        await _vllm("/sleep?level=1")
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            if _should_evict(model):
                # Always clear the cache before a 14B render. A conditional, memory-aware
                # eviction was tried and failed in production: after ten consecutive image
                # jobs a campaign's video stage crawled past the 30-minute client timeout
                # even though the reported free memory looked adequate (unified-memory
                # accounting hides reclaimable state). A clean slate costs the next image
                # job one model reload (~40s) and buys deterministic video wall-clock.
                try:
                    await c.post(f"{COMFY_URL}/free",
                                 json={"unload_models": True, "free_memory": True})
                except httpx.HTTPError:
                    pass
            r = await c.post(f"{COMFY_URL}/prompt", json={"prompt": wf})
            if r.status_code != 200:
                raise HTTPException(502, f"ComfyUI rejected workflow: {r.text[:300]}")
            pid = r.json()["prompt_id"]

            vid = None
            for _ in range(1200):  # up to ~40 min (14B is slow on the Spark)
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
        if is_14b:
            await _vllm("/wake_up")

    if not vid:
        raise HTTPException(504, "ComfyUI generation timed out")
    src = COMFY_OUT / vid.get("subfolder", "") / vid["filename"]
    fn = f"video-{int(time.time())}-{uuid.uuid4().hex[:6]}.mp4"
    shutil.copy(src, out_dir / fn)
    return {
        "video": {
            "path": f"{req.product_id}/{fn}",
            "elapsed_s": round(time.time() - t0, 1),
            "quality": req.quality,
            "frames": req.num_frames,
            "model": "ltx-2.5-22b-distilled" if model == "ltx2" else f"wan2.2-{model}",
        }
    }


# --- keep the default model on the GPU 24/7 (KEEP_WARM=once|vram; inference/adapters/keepwarm.py)
from keepwarm import start as _keepwarm_start, state as _keepwarm_state  # noqa: E402


async def _warmup() -> None:
    served = await _detect_models()
    model = os.environ.get("KEEP_WARM_MODEL") or ("ltx2" if "ltx2" in served else WAN_MODEL)
    out = await generate(GenerateRequest(prompt="warmup", product_id="warmup", model=model,
                                         width=256, height=256, num_frames=9, seed=1))
    (ASSETS_DIR / out["video"]["path"]).unlink(missing_ok=True)


_keepwarm_start(app, COMFY_URL, _warmup)
