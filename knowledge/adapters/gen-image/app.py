"""ComfyUI-backed image service — FLUX.1-dev (fp8), replacing the FLUX.1-schnell NIM.
Speaks the product_dream gen-image /generate contract, so the ad system's image_provider
(GEN_IMAGE_URL=:8102) uses it unchanged. Drives the ComfyUI headless backend (:8188).

Env: COMFY_URL, COMFY_OUTPUT, ASSETS_DIR, WF_DIR, PORT.
"""

import asyncio
import base64
import binascii
import uuid
import json
import os
import sys
import random
import shutil
import time
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
# The image ladder (docs/research/AI_CONTENT_LANDSCAPE_2026-09.md §9.1). One
# workflow per model, same node shapes (a loader, CLIPTextEncode with __PROMPT__,
# EmptySD3LatentImage, KSampler, SaveImage), so _build and the client-LoRA
# injector patch by class_type and never by node id. A model is advertised in
# /health only when its weight file is visible to ComfyUI.
#   flux1-dev-fp8         FLUX.1-dev fp8 + Hyper 8-step LoRA — non-commercial licence: prototype tier
#   qwen-image-2512       Qwen-Image-2512 fp8, Lightning 4-step fused (Apache-2.0) — quality tier
#   qwen-image-edit-2511  Qwen-Image-Edit-2511 fp8, Lightning 4-step fused (Apache-2.0) — EDIT rung:
#                         takes ref_image_path/ref_image_b64 (the product photo); the prompt says what
#                         to change; output size follows the reference. Without a photo it renders
#                         from text on a blank canvas of width x height. Identity of the real product
#                         is preserved, which no text-to-image prompt can promise.
# IMAGE_DEFAULT_MODEL picks the tier a request without `model` gets (the admin
# Image lab sets it); IMAGE_MODELS limits what this host offers, e.g. on a 24 GB card.
MODELS: dict[str, dict] = {
    "flux1-dev-fp8": {"workflow": "flux1_dev_api.json", "weight": "flux1-dev-fp8.safetensors",
                      "loader": "CheckpointLoaderSimple", "steps": 8, "licence": "flux-dev-non-commercial"},
    "qwen-image-2512": {"workflow": "qwen_image_2512_api.json",
                        "weight": "qwen_image_2512_fp8_e4m3fn_scaled_comfyui_4steps_v1.0.safetensors",
                        "loader": "UNETLoader", "steps": 4, "licence": "apache-2.0"},
    "qwen-image-edit-2511": {"workflow": "qwen_image_edit_2511_api.json",
                             "weight": "qwen_image_edit_2511_fp8_e4m3fn_scaled_lightning_comfyui_4steps_v1.0.safetensors",
                             "loader": "UNETLoader", "steps": 4, "licence": "apache-2.0", "edit": True},
}
_offered = [m.strip() for m in os.environ.get("IMAGE_MODELS", "").split(",") if m.strip()]
if _offered:
    MODELS = {k: v for k, v in MODELS.items() if k in _offered}
# Apache-2.0 by default: a host that forgets IMAGE_DEFAULT_MODEL must never hand customers
# the non-commercial FLUX rung (it stays reachable by name for the Image lab).
DEFAULT_MODEL = os.environ.get("IMAGE_DEFAULT_MODEL", "qwen-image-2512")
if DEFAULT_MODEL not in MODELS:
    DEFAULT_MODEL = next(iter(MODELS))
MODEL = DEFAULT_MODEL  # legacy field in /health and /generate replies
# Contract v1 identity (inference/CONTRACT.md): the host.yaml name + where this runs.
HOST_NAME = os.environ.get("HOST_NAME", "")
DEVICE = os.environ.get("DEVICE", "cuda")

app = FastAPI(title="gen-image-comfy")

# GET /system (GPUs, load, RAM) for the admin Infra page — shared with the other adapters
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sysinfo import router as _system_router  # noqa: E402
app.include_router(_system_router)


class GenerateRequest(BaseModel):
    prompt: str
    product_id: str = "adhoc"
    kind: str = "image"
    n: int = 1
    width: int = 1024
    height: int = 1024
    steps: int | None = None
    seed: int | None = None
    # Which rung of the ladder (a key of MODELS); absent -> IMAGE_DEFAULT_MODEL.
    model: str | None = None
    # Edit rungs only: the photo to edit. Same two fields as the video contract —
    # a path relative to ASSETS_DIR on this host, or the bytes as base64 for a
    # worker on another machine (wins when both are given).
    ref_image_path: str | None = None
    ref_image_b64: str | None = None
    # "contain": fit the whole reference into width x height (the rest filled with a
    # blurred extension of the photo) so the edit comes out in the requested frame —
    # a product never gets cropped away. Absent -> output size follows the reference.
    ref_fit: str | None = None
    # Per-client LoRA (P4): path relative to ComfyUI's loras dir (the adapter
    # registry's comfy_name, e.g. "clients/<app_id>/style.safetensors") and the
    # model strength. Absent -> the base workflow, untouched.
    lora_name: str | None = None
    lora_strength: float | None = None
    # The rung the LoRA was trained on. When it names another rung the LoRA is
    # skipped (a FLUX LoRA does not fit Qwen-Image); absent -> applied as before.
    lora_base: str | None = None


CLIENT_LORA_NODE = "client_lora"


def _graph_ok(wf: dict) -> bool:
    """Every node input reference ["id", slot] must point at an existing node —
    the same integrity a ComfyUI /prompt validation would enforce."""
    for node in wf.values():
        for val in node.get("inputs", {}).values():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[1], int):
                if str(val[0]) not in wf:
                    return False
    return True


def _inject_lora(wf: dict, lora_name: str, strength: float) -> dict:
    """Insert a LoraLoaderModelOnly for the client's adapter between the model
    loader and its consumer(s) — same patch-by-class_type style as
    gen-video-comfy's _add_rife, so node ids in the template don't matter.
    Stacks with the template's own LoRAs (Hyper-FLUX speed-up): the client node
    takes the loader's model output, everything that consumed it re-routes
    through the client node. No-op (base workflow, logged) if the graph shape
    differs or the patched graph fails integrity validation."""
    loader_id = next((k for k, n in wf.items()
                      if n.get("class_type") in ("UNETLoader", "CheckpointLoaderSimple")), None)
    if loader_id is None or CLIENT_LORA_NODE in wf:
        print(f"[lora] no model loader found — skipping lora '{lora_name}'", flush=True)
        return wf
    consumers = [(nid, key) for nid, n in wf.items()
                 for key, val in n.get("inputs", {}).items()
                 if key == "model" and isinstance(val, list) and len(val) == 2
                 and str(val[0]) == loader_id and val[1] == 0]
    if not consumers:
        print(f"[lora] model loader {loader_id} has no consumer — "
              f"skipping lora '{lora_name}'", flush=True)
        return wf
    patched = json.loads(json.dumps(wf))  # deep copy — keep the original intact
    patched[CLIENT_LORA_NODE] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"model": [loader_id, 0], "lora_name": lora_name,
                   "strength_model": strength},
    }
    for nid, key in consumers:
        patched[nid]["inputs"][key] = [CLIENT_LORA_NODE, 0]
    if not _graph_ok(patched):
        print(f"[lora] patched graph failed integrity check — "
              f"skipping lora '{lora_name}'", flush=True)
        return wf
    return patched


def _model_for(req: GenerateRequest) -> str:
    if req.model is None:
        return DEFAULT_MODEL
    if req.model not in MODELS:
        raise HTTPException(400, f"unknown image model {req.model!r}; this host offers {list(MODELS)}")
    return req.model


def _stage_image(rel: str | None, b64: str | None = None) -> str:
    """Put the reference photo where ComfyUI's LoadImage can see it (same as gen-video)."""
    COMFY_IN.mkdir(parents=True, exist_ok=True)
    if b64:
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "ref_image_b64 is not valid base64")
        name = f"adref-{uuid.uuid4().hex[:8]}{Path(rel or '').suffix or '.png'}"
        (COMFY_IN / name).write_bytes(data)
        return name
    src = Path(rel or "")
    if not src.is_absolute():
        src = ASSETS_DIR / (rel or "")
    if not rel or not src.is_file():
        raise HTTPException(404, f"ref image not found: {src} (a remote worker must send ref_image_b64)")
    name = f"adref-{uuid.uuid4().hex[:8]}{src.suffix or '.png'}"
    shutil.copy(src, COMFY_IN / name)
    return name


def _blank_canvas(width: int, height: int) -> str:
    """A white width x height PNG staged for LoadImage (one per size, reused)."""
    from PIL import Image

    name = f"blank_{width}x{height}.png"
    dest = COMFY_IN / name
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, height), (255, 255, 255)).save(dest)
    return name


def _fit_contain(name: str, width: int, height: int) -> str:
    """Letterbox the staged reference to width:height with a blurred cover copy
    behind it. Returns the new file name in COMFY_IN."""
    from PIL import Image, ImageFilter, ImageOps

    src = Image.open(COMFY_IN / name)
    src = ImageOps.exif_transpose(src).convert("RGB")
    bg = ImageOps.fit(src, (width, height)).filter(ImageFilter.GaussianBlur(radius=max(width, height) // 24))
    fg = ImageOps.contain(src, (width, height))
    bg.paste(fg, ((width - fg.width) // 2, (height - fg.height) // 2))
    out = f"adfit-{uuid.uuid4().hex[:8]}.png"
    bg.save(COMFY_IN / out)
    return out


def _to_size(src: Path, dest: Path, width: int, height: int) -> None:
    """Scale-and-centre-crop a render to exactly width x height."""
    from PIL import Image, ImageOps

    ImageOps.fit(Image.open(src).convert("RGB"), (width, height), Image.LANCZOS).save(dest)


def _build(req: GenerateRequest, seed: int) -> dict:
    spec = MODELS[_model_for(req)]
    wf = json.loads((WF_DIR / spec["workflow"]).read_text())
    ref = None
    prompt = req.prompt
    if spec.get("edit"):
        if req.ref_image_path or req.ref_image_b64:
            ref = _stage_image(req.ref_image_path, req.ref_image_b64)
            if req.ref_fit == "contain":
                ref = _fit_contain(ref, req.width, req.height)
        else:
            # Text-only on an edit rung (owner 2026-10-03: the Spark keeps Qwen-Edit
            # only): a blank canvas of the asked size, told to become the picture.
            # Measured: ~20 s warm at 1024², a touch less photographic than 2512.
            ref = _blank_canvas(req.width, req.height)
            prompt = f"Replace the blank white canvas with: {req.prompt}"
    for node in wf.values():
        ct = node.get("class_type")
        ins = node.setdefault("inputs", {})
        if ct == "CLIPTextEncode" and "__PROMPT__" in str(ins.get("text", "")):  # positive node
            ins["text"] = prompt
        elif ct == "TextEncodeQwenImageEditPlus" and "__PROMPT__" in str(ins.get("prompt", "")):
            ins["prompt"] = prompt
        elif ct == "LoadImage" and ref:
            ins["image"] = ref
        elif ct == "EmptySD3LatentImage":
            ins["width"], ins["height"], ins["batch_size"] = req.width, req.height, 1
        elif ct == "KSampler":
            ins["seed"] = seed
            if req.steps:
                ins["steps"] = req.steps
        elif ct == "SaveImage":
            ins["filename_prefix"] = f"ad_{_model_for(req)[:8]}_{req.product_id[:8]}"
    if req.lora_name and req.lora_base and req.lora_base != _model_for(req):
        print(f"[lora] '{req.lora_name}' was trained on {req.lora_base}, rendering "
              f"{_model_for(req)} — skipping it", flush=True)
    elif req.lora_name:
        wf = _inject_lora(wf, req.lora_name,
                          req.lora_strength if req.lora_strength is not None else 0.8)
    return wf


async def _run_once(client: httpx.AsyncClient, wf: dict) -> Path:
    r = await client.post(f"{COMFY_URL}/prompt", json={"prompt": wf})
    if r.status_code != 200:
        raise HTTPException(502, f"ComfyUI rejected workflow: {r.text[:300]}")
    pid = r.json()["prompt_id"]
    for _ in range(600):  # up to ~20 min
        await asyncio.sleep(1)
        try:
            entry = (await client.get(f"{COMFY_URL}/history/{pid}")).json().get(pid)
        except httpx.HTTPError:
            continue
        if not entry:
            continue
        if entry.get("status", {}).get("status_str") == "error":
            raise HTTPException(502, f"ComfyUI error: {entry['status'].get('messages', [])[-2:]}")
        for o in entry.get("outputs", {}).values():
            imgs = o.get("images") or []
            if imgs:
                return COMFY_OUT / imgs[0].get("subfolder", "") / imgs[0]["filename"]
    raise HTTPException(504, "ComfyUI generation timed out")


@app.get("/health")
async def health():
    ready: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            seen: dict[str, str] = {}
            for name, spec in MODELS.items():
                loader = spec["loader"]
                if loader not in seen:
                    r = await c.get(f"{COMFY_URL}/object_info/{loader}")
                    seen[loader] = r.text if r.status_code == 200 else ""
                # a model is ready only once its weight file is visible to ComfyUI
                if spec["weight"] in seen[loader]:
                    ready.append(name)
    except httpx.HTTPError:
        ready = []
    return {
        "warm": _keepwarm_state["warm"] if _keepwarm_state["mode"] != "off" else None,
        "ok": True, "loaded": DEFAULT_MODEL in ready, "mem_gb": 0, "model": DEFAULT_MODEL,
        "host": HOST_NAME, "engine": "comfy", "device": DEVICE,
        "capabilities": {"kind": "image", "models": ready, "default": DEFAULT_MODEL,
                         "licences": {m: MODELS[m]["licence"] for m in ready},
                         "edit": [m for m in ready if MODELS[m].get("edit")],
                         "lora": True, "max_side": 1536},
    }


@app.get("/images/{path:path}")
@app.get("/files/{path:path}")
async def get_image(path: str):
    # Serve generated files directly so consumers on other machines (tailnet developers'
    # backends, the learning app) can fetch results without the ad backend's /assets route.
    f = (ASSETS_DIR / path).resolve()
    if not f.is_relative_to(ASSETS_DIR.resolve()) or not f.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(f)


@app.post("/generate")
async def generate(req: GenerateRequest):
    out_dir = ASSETS_DIR / req.product_id
    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    async with httpx.AsyncClient(timeout=60) as c:
        for _ in range(req.n):
            seed = req.seed if req.seed is not None else random.randint(0, 2**31 - 1)
            t0 = time.time()
            src = await _run_once(c, _build(req, seed))
            fn = f"{req.kind}-{int(time.time())}-{seed}.png"
            text_on_edit = MODELS[_model_for(req)].get("edit") and not (req.ref_image_path or req.ref_image_b64)
            if req.ref_fit == "contain" or text_on_edit:
                # the edit model snaps to its own resolutions (944x1104 for 4:5):
                # hand back exactly the frame that was asked for
                _to_size(src, out_dir / fn, req.width, req.height)
            else:
                shutil.copy(src, out_dir / fn)
            images.append({"path": f"{req.product_id}/{fn}", "seed": seed,
                           "elapsed_s": round(time.time() - t0, 1)})
    return {"images": images, "model": _model_for(req)}


# --- keep the default rung on the GPU 24/7 (KEEP_WARM=once|vram; inference/adapters/keepwarm.py)
from keepwarm import start as _keepwarm_start, state as _keepwarm_state  # noqa: E402

# 64x64 grey PNG: an edit rung needs a photo to edit, any photo warms it
_WARM_PNG = "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAATUlEQVR42u3PQQ0AAAgEIDX5RTeFDzdoQCepz6aeExAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQELi3oiwCAJt186UAAAAASUVORK5CYII="


# KEEP_WARM_MODELS=a,b warms every listed rung (dgx-spark-2 keeps 2512 and Edit-2511
# resident side by side); unset = the default rung only.
WARM_MODELS = [m.strip() for m in os.environ.get("KEEP_WARM_MODELS", "").split(",") if m.strip()] or [DEFAULT_MODEL]


async def _warmup() -> None:
    for model in WARM_MODELS:
        req = GenerateRequest(prompt="warmup", product_id="warmup", width=256, height=256, seed=1,
                              model=model)
        if MODELS.get(model, {}).get("edit"):
            req.ref_image_b64 = _WARM_PNG
        out = await generate(req)
        for img in out["images"]:
            (ASSETS_DIR / img["path"]).unlink(missing_ok=True)


_keepwarm_start(app, COMFY_URL, _warmup)
