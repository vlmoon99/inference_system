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

IMAGE_BACKEND=comfy_cloud renders on Comfy Cloud (cloud.comfy.org) instead of a ComfyUI next to this
service: the same calls with an `X-API-Key` header, the job's state from /job/{id}/status, its files from
/jobs/{id}, and /view answers with a redirect to a signed link that must be fetched WITHOUT the key. The
cloud has the stock model files, so it gets its own workflow (base 2511 + the 4-step Lightning LoRA). No
warm-up there: every render is paid for. Default is `local`, where nothing changes.

Quality (the OpenAI `quality` field, or IMAGE_QUALITY when the caller sends none): `high` paints the frame
that was asked for, `medium` paints it at 0.75 of each side and `low` at 0.5, then the result is resized up
to the requested frame. Fewer pixels is less GPU time, which is what the cloud bills. This needs a workflow
that paints at the size of the picture it is given (the cloud one); the local workflow snaps every input to
about one megapixel, so there the setting changes nothing.
Env: IMAGE_BACKEND, IMAGE_QUALITY, COMFY_URL, COMFY_API_KEY, MODEL_NAME, WORKFLOW, KEEP_WARM_S, API_KEY, BIND, PORT.
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

IMAGE_BACKEND = os.environ.get("IMAGE_BACKEND") or "local"   # local | comfy_cloud
if IMAGE_BACKEND not in ("local", "comfy_cloud"):
    raise SystemExit(f"IMAGE_BACKEND must be local or comfy_cloud, got {IMAGE_BACKEND!r}")
CLOUD = IMAGE_BACKEND == "comfy_cloud"
COMFY_API_KEY = os.environ.get("COMFY_API_KEY", "")
if CLOUD and not COMFY_API_KEY:
    raise SystemExit("IMAGE_BACKEND=comfy_cloud needs COMFY_API_KEY")
COMFY_URL = (os.environ.get("COMFY_URL") or ("https://cloud.comfy.org/api" if CLOUD else "http://127.0.0.1:8188")).rstrip("/")
COMFY_HEADERS = {"X-API-Key": COMFY_API_KEY} if CLOUD else {}
MODEL_NAME = os.environ.get("MODEL_NAME", "qwen-image-edit")
WORKFLOW = Path(os.environ.get("WORKFLOW") or str(Path(__file__).parent / "workflows" / (
    "qwen_image_edit_2511_cloud_api.json" if CLOUD else "qwen_image_edit_2511_api.json")))
KEEP_WARM_S = 0 if CLOUD else int(os.environ.get("KEEP_WARM_S", "900"))   # 0 = off; never on the cloud (paid)
QUALITY_SCALE = {"low": 0.5, "medium": 0.75, "high": 1.0}
IMAGE_QUALITY = os.environ.get("IMAGE_QUALITY") or "high"
if IMAGE_QUALITY not in QUALITY_SCALE:
    raise SystemExit(f"IMAGE_QUALITY must be low, medium or high, got {IMAGE_QUALITY!r}")
CLOUD_DONE = {"success", "error", "non_retryable_error", "lost", "cancelled"}
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
    quality: str | None = None           # "low" | "medium" | "high"; absent or anything else → IMAGE_QUALITY
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


def paint_size(w: int, h: int, quality: str | None) -> tuple[int, int]:
    """The size the model paints for a w×h frame at this quality, on the 16 px grid."""
    scale = QUALITY_SCALE.get(quality or "", QUALITY_SCALE[IMAGE_QUALITY])
    if scale >= 1:
        return w, h
    pw, ph = max(256, int(w * scale)), max(256, int(h * scale))
    return pw - pw % 16, ph - ph % 16


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
                     data={"overwrite": "true", "type": "input"}, headers=COMFY_HEADERS)
    if r.status_code != 200:
        raise HTTPException(502, f"ComfyUI upload failed: {r.status_code} {r.text[:200]}")
    j = r.json()
    return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]


def cloud_failure(status: int, text: str) -> HTTPException:
    """Comfy Cloud's answers that the owner has to act on, said plainly."""
    why = {401: "the Comfy Cloud key is wrong", 402: "Comfy Cloud is out of credits",
           429: "the Comfy Cloud subscription is not active"}.get(status)
    return HTTPException(502, f"Comfy Cloud: {why or f'HTTP {status}'} {text[:200]}".strip())


def first_image(outputs: dict) -> dict | None:
    for out in (outputs or {}).values():
        for im in out.get("images") or []:
            return im
    return None


async def cloud_run(c: httpx.AsyncClient, wf: dict) -> bytes:
    r = await c.post(f"{COMFY_URL}/prompt", json={"prompt": wf}, headers=COMFY_HEADERS)
    if r.status_code != 200 or "prompt_id" not in r.json():
        raise cloud_failure(r.status_code, r.text)
    pid = r.json()["prompt_id"]
    for _ in range(300):                       # ≤ 10 min
        await asyncio.sleep(2)
        try:
            status = (await c.get(f"{COMFY_URL}/job/{pid}/status", headers=COMFY_HEADERS)).json().get("status")
        except (httpx.HTTPError, ValueError):
            continue
        if status not in CLOUD_DONE:
            continue
        if status != "success":
            raise HTTPException(502, f"Comfy Cloud job {pid} ended as {status}")
        job = await c.get(f"{COMFY_URL}/jobs/{pid}", headers=COMFY_HEADERS)
        im = first_image(job.json().get("outputs")) if job.status_code == 200 else None
        if not im:
            raise HTTPException(502, f"Comfy Cloud job {pid} has no picture")
        v = await c.get(f"{COMFY_URL}/view", headers=COMFY_HEADERS, follow_redirects=False, params={
            "filename": im["filename"], "subfolder": im.get("subfolder", ""), "type": im.get("type", "output")})
        if v.status_code not in (301, 302, 303, 307, 308) or "location" not in v.headers:
            raise cloud_failure(v.status_code, v.text)
        f = await c.get(v.headers["location"])          # the signed link: no key goes to the storage host
        f.raise_for_status()
        return f.content
    raise HTTPException(504, "Comfy Cloud generation timed out")


async def comfy_run(c: httpx.AsyncClient, wf: dict) -> bytes:
    if CLOUD:
        return await cloud_run(c, wf)
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
        ref, prompt, exact = Image.new("RGB", paint_size(w, h, req.quality), (255, 255, 255)), \
            f"Replace the blank white canvas with: {req.prompt}", (w, h)
    elif size:
        paint = paint_size(*size, req.quality)
        ref = to_size(photo, *paint) if req.fit == "cover" else fit_contain(photo, *paint)
        prompt, exact = req.prompt, size
    else:
        ref, prompt, exact = ImageOps.exif_transpose(photo).convert("RGB"), req.prompt, None
        paint = paint_size(*ref.size, req.quality)
        if paint != ref.size:
            ref, exact = to_size(ref, *paint), ref.size
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
                      "size": req.size, "backend": IMAGE_BACKEND,
                      "quality": req.quality if req.quality in QUALITY_SCALE else IMAGE_QUALITY, "elapsed_s": round(time.time() - t0, 1)}), flush=True)
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
            comfy = (await c.get(f"{COMFY_URL}/system_stats", headers=COMFY_HEADERS)).status_code == 200
    except httpx.HTTPError:
        pass
    return {"ok": comfy, "loaded": (CLOUD or _state["loaded"]) and comfy, "model": MODEL_NAME, "comfy": comfy,
            "backend": IMAGE_BACKEND,
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
                _state["last_use"] = 0.0          # respond() stamped it; a failed warm-up must retry soon
                await asyncio.sleep(20)
                continue
        await asyncio.sleep(30)


@app.on_event("startup")
async def _start() -> None:
    if KEEP_WARM_S > 0:
        asyncio.create_task(_keep_warm())
