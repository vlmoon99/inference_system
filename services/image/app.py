"""inf-image — OpenAI-compatible image API over a headless ComfyUI running Qwen-Image-Edit-2511.

    GET  /health                    {ok, loaded, model, comfy}
    GET  /v1/models
    POST /v1/images/generations     JSON (OpenAI shape + the URL contract below)
    POST /v1/images/edits           multipart (OpenAI shape: image file + prompt)

Edit vs text: with an input image the prompt says what to change about it (the product stays the
product). Without one the model paints a blank canvas of the requested size ("text-to-image on an
edit model", ~20 s warm at 1024² on the Spark).

URL contract (JSON only, so callers like Postgres pg_net can use it): optional
  image_url        pre-signed GET of the input photo (or `image_b64`)
  output_put_url   pre-signed PUT for the result (`output_put_urls` when n > 1)
With an output URL the PNG is uploaded there and `data[i].url` is that URL without its query
string; no bytes come back. Without one, `data[i].b64_json` (OpenAI default). LiteLLM forwards
these extra JSON fields untouched (checked 2026-10-09) but drops unknown reply fields, so the
reply stays strictly OpenAI-shaped.

ComfyUI is reached only over HTTP (/upload/image, /prompt, /history, /view): no shared disk.
When API_KEY is set every /v1 call needs `Authorization: Bearer $API_KEY` (the gateway sends it): an image
service bound to a tailnet IP must not be a way around the gateway's project keys.
Env: COMFY_URL, MODEL_NAME, WORKFLOW, KEEP_WARM_S, API_KEY, BIND, PORT.
"""

import asyncio
import base64
import binascii
import io
import json
import os
import random
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import hmac

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen-image-edit")
WORKFLOW = Path(os.environ.get("WORKFLOW", str(Path(__file__).parent / "workflows" / "qwen_image_edit_2511_api.json")))
KEEP_WARM_S = int(os.environ.get("KEEP_WARM_S", "900"))   # 0 = off
API_KEY = os.environ.get("API_KEY", "")
MAX_SIDE = 2048
MAX_N = 4
FETCH_LIMIT = 25 * 1024 * 1024

app = FastAPI(title="inf-image")
_state = {"loaded": False, "last_use": 0.0, "renders": 0, "errors": 0}


@app.middleware("http")
async def _auth(request: Request, call_next):
    if API_KEY and request.url.path.startswith("/v1/"):
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given, API_KEY):
            return JSONResponse({"detail": "bad api key"}, status_code=401)
    return await call_next(request)


class ImageRequest(BaseModel):
    prompt: str
    model: str | None = None
    n: int = 1
    size: str | None = None              # "WxH"; absent → 1024x1024 for text, the photo's own size for edits
    response_format: str | None = None   # "b64_json" (default) | "url" (needs output_put_url)
    seed: int | None = None
    steps: int | None = None
    image_url: str | None = None
    image_b64: str | None = None
    fit: str | None = None               # "contain" (default when size is given with a photo) | "cover"
    output_put_url: str | None = None
    output_put_urls: list[str] | None = None


# ---------- pure helpers (unit-tested) ----------

def parse_size(size: str | None) -> tuple[int, int] | None:
    if not size or size == "auto":
        return None
    try:
        w, h = (int(x) for x in size.lower().split("x"))
    except ValueError:
        raise HTTPException(400, f"size must be WxH, got {size!r}")
    if not (64 <= w <= MAX_SIDE and 64 <= h <= MAX_SIDE):
        raise HTTPException(400, f"size sides must be 64..{MAX_SIDE}")
    return w - w % 16, h - h % 16


def public_url(put_url: str) -> str:
    """The object's address without the signature query — what the caller stores."""
    p = urlsplit(put_url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def output_urls(req: ImageRequest) -> list[str] | None:
    urls = req.output_put_urls or ([req.output_put_url] if req.output_put_url else None)
    if urls is not None and len(urls) != req.n:
        raise HTTPException(400, f"need exactly n={req.n} output_put_urls, got {len(urls)}")
    return urls


def build_workflow(template: dict, prompt: str, ref_name: str, seed: int, steps: int | None) -> dict:
    wf = json.loads(json.dumps(template))
    for node in wf.values():
        ct, ins = node.get("class_type"), node.setdefault("inputs", {})
        if ct == "TextEncodeQwenImageEditPlus" and ins.get("prompt") == "__PROMPT__":
            ins["prompt"] = prompt
        elif ct == "LoadImage":
            ins["image"] = ref_name
        elif ct == "KSampler":
            ins["seed"] = seed
            if steps:
                ins["steps"] = steps
        elif ct == "SaveImage":
            ins["filename_prefix"] = "inf"
    return wf


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def fit_contain(src: Image.Image, w: int, h: int) -> Image.Image:
    """Whole photo inside w×h over a blurred cover copy: the product never gets cropped away."""
    src = ImageOps.exif_transpose(src).convert("RGB")
    bg = ImageOps.fit(src, (w, h)).filter(ImageFilter.GaussianBlur(radius=max(w, h) // 24))
    fg = ImageOps.contain(src, (w, h))
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


def to_size(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS)


# ---------- ComfyUI ----------

async def comfy_upload(c: httpx.AsyncClient, data: bytes) -> str:
    name = f"inf-{uuid.uuid4().hex[:12]}.png"
    r = await c.post(f"{COMFY_URL}/upload/image", files={"image": (name, data, "image/png")},
                     data={"overwrite": "true"})
    if r.status_code != 200:
        raise HTTPException(502, f"ComfyUI upload failed: {r.status_code} {r.text[:200]}")
    j = r.json()
    return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]


async def comfy_run(c: httpx.AsyncClient, wf: dict) -> bytes:
    r = await c.post(f"{COMFY_URL}/prompt", json={"prompt": wf})
    if r.status_code != 200:
        raise HTTPException(502, f"ComfyUI rejected workflow: {r.text[:300]}")
    pid = r.json()["prompt_id"]
    for _ in range(1200):                      # ≤ 10 min
        await asyncio.sleep(0.5)
        try:
            entry = (await c.get(f"{COMFY_URL}/history/{pid}")).json().get(pid)
        except (httpx.HTTPError, ValueError):
            continue
        if not entry:
            continue
        if entry.get("status", {}).get("status_str") == "error":
            raise HTTPException(502, f"ComfyUI error: {entry['status'].get('messages', [])[-2:]}")
        for out in entry.get("outputs", {}).values():
            for im in out.get("images") or []:
                v = await c.get(f"{COMFY_URL}/view", params={
                    "filename": im["filename"], "subfolder": im.get("subfolder", ""), "type": im.get("type", "output")})
                v.raise_for_status()
                return v.content
    raise HTTPException(504, "ComfyUI generation timed out")


# ---------- the request path ----------

async def fetch_input(c: httpx.AsyncClient, req: ImageRequest) -> Image.Image | None:
    if req.image_b64:
        try:
            raw = base64.b64decode(req.image_b64.split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(400, "image_b64 is not valid base64")
    elif req.image_url:
        try:
            r = await c.get(req.image_url, follow_redirects=True)
        except httpx.HTTPError as e:
            raise HTTPException(400, f"image_url fetch failed: {type(e).__name__}")
        if r.status_code != 200:
            raise HTTPException(400, f"image_url fetch failed: HTTP {r.status_code}")
        raw = r.content
    else:
        return None
    if len(raw) > FETCH_LIMIT:
        raise HTTPException(413, "input image larger than 25 MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:  # noqa: BLE001 — any decode failure is the caller's input
        raise HTTPException(400, "input is not a readable image")
    return img


async def render(req: ImageRequest, photo: Image.Image | None) -> list[tuple[bytes, int]]:
    if not 1 <= req.n <= MAX_N:
        raise HTTPException(400, f"n must be 1..{MAX_N}")
    size = parse_size(req.size)
    template = json.loads(WORKFLOW.read_text())
    if photo is None:
        w, h = size or (1024, 1024)
        ref, prompt, exact = Image.new("RGB", (w, h), (255, 255, 255)), \
            f"Replace the blank white canvas with: {req.prompt}", (w, h)
    elif size:
        ref = to_size(photo, *size) if req.fit == "cover" else fit_contain(photo, *size)
        prompt, exact = req.prompt, size
    else:
        ref, prompt, exact = ImageOps.exif_transpose(photo).convert("RGB"), req.prompt, None
    out: list[tuple[bytes, int]] = []
    async with httpx.AsyncClient(timeout=60) as c:
        ref_name = await comfy_upload(c, png(ref))
        for i in range(req.n):
            seed = (req.seed + i) if req.seed is not None else random.randint(0, 2**31 - 1)
            data = await comfy_run(c, build_workflow(template, prompt, ref_name, seed, req.steps))
            if exact:   # the edit model snaps to its own resolutions; hand back the frame asked for
                data = png(to_size(Image.open(io.BytesIO(data)), *exact))
            out.append((data, seed))
    return out


async def respond(req: ImageRequest, photo: Image.Image | None) -> dict:
    urls = output_urls(req)
    if req.response_format == "url" and not urls:
        raise HTTPException(400, "response_format=url needs output_put_url(s): this service stores nothing")
    t0 = time.time()
    _state["last_use"] = t0
    try:
        results = await render(req, photo)
    except HTTPException:
        _state["errors"] += 1
        raise
    data = []
    async with httpx.AsyncClient(timeout=120) as c:
        for i, (img, seed) in enumerate(results):
            if urls:
                r = await c.put(urls[i], content=img, headers={"content-type": "image/png"})
                if r.status_code not in (200, 201, 204):
                    raise HTTPException(502, f"output upload failed: HTTP {r.status_code} {r.text[:200]}")
                data.append({"url": public_url(urls[i]), "revised_prompt": req.prompt})
            else:
                data.append({"b64_json": base64.b64encode(img).decode(), "revised_prompt": req.prompt})
    _state["renders"] += len(results)
    _state["loaded"] = True
    print(json.dumps({"event": "render", "n": len(results), "edit": photo is not None,
                      "size": req.size, "elapsed_s": round(time.time() - t0, 1)}), flush=True)
    return {"created": int(t0), "data": data}


@app.post("/v1/images/generations")
async def generations(req: ImageRequest):
    async with httpx.AsyncClient(timeout=60) as c:
        photo = await fetch_input(c, req)
    return await respond(req, photo)


@app.post("/v1/images/edits")
async def edits(prompt: str = Form(...), image: UploadFile = File(...), n: int = Form(1),
                size: str | None = Form(None), seed: int | None = Form(None),
                response_format: str | None = Form(None), output_put_url: str | None = Form(None)):
    req = ImageRequest(prompt=prompt, n=n, size=size, seed=seed, response_format=response_format,
                       output_put_url=output_put_url,
                       image_b64=base64.b64encode(await image.read()).decode())
    async with httpx.AsyncClient(timeout=60) as c:
        photo = await fetch_input(c, req)
    return await respond(req, photo)


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "inference_system"}]}


@app.get("/health")
async def health():
    comfy = False
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            comfy = (await c.get(f"{COMFY_URL}/system_stats")).status_code == 200
    except httpx.HTTPError:
        pass
    return {"ok": comfy, "loaded": _state["loaded"] and comfy, "model": MODEL_NAME, "comfy": comfy,
            "renders": _state["renders"], "errors": _state["errors"]}


async def _keep_warm() -> None:
    """Render a 256² blank once at start and again whenever idle for KEEP_WARM_S, so the first real
    request doesn't pay the ~1 min weight load after ComfyUI drops the model."""
    while True:
        if time.time() - _state["last_use"] >= KEEP_WARM_S:
            try:
                await respond(ImageRequest(prompt="warm-up", size="256x256", seed=1, steps=1), None)
            except Exception as e:  # noqa: BLE001 — ComfyUI still booting: try again shortly
                print(json.dumps({"event": "warm_failed", "error": f"{type(e).__name__}: {e}"[:200]}), flush=True)
                await asyncio.sleep(20)
                continue
        await asyncio.sleep(30)


@app.on_event("startup")
async def _start() -> None:
    if KEEP_WARM_S > 0:
        asyncio.create_task(_keep_warm())
