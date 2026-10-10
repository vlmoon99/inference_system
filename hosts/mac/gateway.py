"""inf-mac: the gateway of this repo on ONE Apple Silicon Mac, with no containers and no cluster.

Docker on macOS cannot reach the GPU, so the engines run natively and this one process stands where
LiteLLM + balancer + inf-image stand on the Sparks. It serves the same three public model ids with the
same request shapes, so a project (BoostContent) only changes its base URL and key:

    GET  /health                     {ok, llm, image_loaded, renders}
    GET  /v1/models
    POST /v1/chat/completions        → MTPLX (Qwen 3.5 4B, 4-bit), OpenAI shape in and out
    POST /v1/embeddings              → MTPLX (Qwen3-Embedding 0.6B, 1024-dim)
    POST /v1/images/generations      → mflux (MLX) FLUX.2 klein 4B, 4-bit, 4 steps, in this process;
                                       same URL contract as services/image (image_url, output_put_url)

The Sparks do not run this file: it lives only in hosts/mac and nothing else in the repo refers to it.
Not here: project keys and usage logs (one key, API_KEY), streaming, /v1/images/edits, retries.
Env: API_KEY, LLM_URL, LLM_KEY, LLM_MODEL, EMBED_MODEL, LLM_VISION, LLM_FREQUENCY_PENALTY, LLM_PRESENCE_PENALTY, IMAGE_MODEL_PATH, IMAGE_STEPS,
     IMAGE_RENDER_SCALE, IMAGE_IDLE_UNLOAD_S, BIND, PORT.
"""

import asyncio
import base64
import gc
import hmac
import io
import os
import random
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel

API_KEY = os.environ.get("API_KEY", "")
LLM_URL = os.environ.get("LLM_URL", "http://127.0.0.1:8001").rstrip("/")
LLM_KEY = os.environ.get("LLM_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen3.5-4b")                      # the id MTPLX serves (--model-id)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "qwen3-embedding-0.6b")
LLM_FREQUENCY_PENALTY = float(os.environ.get("LLM_FREQUENCY_PENALTY", "0.6"))
LLM_PRESENCE_PENALTY = float(os.environ.get("LLM_PRESENCE_PENALTY", "0.3"))
LLM_VISION = os.environ.get("LLM_VISION", "0") == "1"                      # 0: photos are dropped before the LLM
LLM_IMAGE_SIDE = int(os.environ.get("LLM_IMAGE_SIDE", "896"))             # photos are shrunk before the LLM sees them
IMAGE_MODEL_PATH = os.environ.get("IMAGE_MODEL_PATH", "mflux-community/flux2-klein-4b-mflux-q4")
IMAGE_STEPS = int(os.environ.get("IMAGE_STEPS", "4"))
IMAGE_RENDER_SCALE = float(os.environ.get("IMAGE_RENDER_SCALE", "1.0"))   # <1: render smaller, resize up
IMAGE_IDLE_UNLOAD_S = int(os.environ.get("IMAGE_IDLE_UNLOAD_S", "0"))    # 0 = keep the model in memory

# public id → what answers it here
CHAT_IDS = {"qwen3.6-35b", LLM_MODEL}
EMBED_IDS = {"qwen3-embedding-0.6b", EMBED_MODEL}
IMAGE_IDS = {"qwen-image-edit", "flux2-klein-4b"}
MAX_SIDE = 2048
FETCH_LIMIT = 25 * 1024 * 1024

app = FastAPI(title="inf-mac")
_state = {"image_loaded": False, "last_render": 0.0, "renders": 0, "errors": 0, "last_render_s": None}
_mlx = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")   # MLX streams are per thread: one thread, always
_render_lock = asyncio.Lock()
_model = None


@app.middleware("http")
async def _auth(request: Request, call_next):
    if API_KEY and request.url.path.startswith("/v1/"):
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given, API_KEY):
            return JSONResponse({"error": {"message": "bad api key"}}, status_code=401)
    return await call_next(request)


# ---------- pure helpers ----------

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


def render_size(w: int, h: int, scale: float) -> tuple[int, int]:
    """The size the model actually paints: the requested one times `scale`, on the 16 px grid."""
    if scale >= 1:
        return w, h
    rw, rh = max(256, int(w * scale)), max(256, int(h * scale))
    return rw - rw % 16, rh - rh % 16


def public_url(put_url: str) -> str:
    p = urlsplit(put_url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


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


def shrink_jpeg(data: bytes, side: int) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def split_content(content) -> tuple[str, list[str]]:
    """OpenAI message content → (text, image references). References are URLs or data: URLs."""
    if isinstance(content, str) or content is None:
        return content or "", []
    texts, images = [], []
    for part in content:
        if part.get("type") == "text":
            texts.append(part.get("text", ""))
        elif part.get("type") == "image_url":
            ref = part.get("image_url")
            images.append(ref["url"] if isinstance(ref, dict) else ref)
    return "\n".join(texts), images


def llm_body(body: dict, messages: list) -> dict:
    """The caller's request as MTPLX gets it: its own model id, no streaming, thinking off unless asked."""
    out = {k: v for k, v in body.items() if k not in ("model", "messages", "stream", "chat_template_kwargs")}
    kw = body.get("chat_template_kwargs") or {}
    out.update(model=LLM_MODEL, messages=messages, stream=False,
               chat_template_kwargs={**kw, "enable_thinking": bool(kw.get("enable_thinking", False))})
    # A 4-bit 4B model loops on one word in Ukrainian and Russian without these (measured 2026-10-10).
    out.setdefault("frequency_penalty", LLM_FREQUENCY_PENALTY)
    out.setdefault("presence_penalty", LLM_PRESENCE_PENALTY)
    return out


def strip_fences(text: str) -> str:
    """A small model likes to wrap JSON in ```json fences; callers asked for bare JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    return t.strip()


# ---------- fetching ----------

async def fetch_bytes(c: httpx.AsyncClient, ref: str) -> bytes:
    if ref.startswith("data:"):
        return base64.b64decode(ref.split(",", 1)[1])
    r = await c.get(ref, follow_redirects=True)
    if r.status_code != 200:
        raise HTTPException(400, f"could not fetch the image: HTTP {r.status_code}")
    if len(r.content) > FETCH_LIMIT:
        raise HTTPException(400, "image too large")
    return r.content


# ---------- chat + embeddings (MTPLX) ----------

def _llm_headers() -> dict:
    return {"authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    if body.get("model") not in CHAT_IDS:
        raise HTTPException(404, f"unknown model {body.get('model')!r}")
    if body.get("stream"):
        raise HTTPException(400, "streaming is not served on this host")
    async with httpx.AsyncClient(timeout=600) as c:
        messages = []
        for m in body.get("messages", []):
            text, refs = split_content(m.get("content"))
            if refs and LLM_VISION:
                raw = await asyncio.gather(*(fetch_bytes(c, r) for r in refs))
                parts = [{"type": "text", "text": text}] + [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,"
                                                        + base64.b64encode(shrink_jpeg(b, LLM_IMAGE_SIDE)).decode()}}
                    for b in raw]
                messages.append({**m, "content": parts})
            else:
                messages.append({**m, "content": text})
        r = await c.post(f"{LLM_URL}/v1/chat/completions", json=llm_body(body, messages), headers=_llm_headers())
    if r.status_code != 200:
        raise HTTPException(502, f"llm: HTTP {r.status_code} {r.text[:300]}")
    out = r.json()
    out["model"] = body["model"]
    if (body.get("response_format") or {}).get("type") in ("json_object", "json_schema"):
        for ch in out.get("choices", []):
            msg = ch.get("message") or {}
            if isinstance(msg.get("content"), str):
                msg["content"] = strip_fences(msg["content"])
    return out


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    if body.get("model") not in EMBED_IDS:
        raise HTTPException(404, f"unknown model {body.get('model')!r}")
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(f"{LLM_URL}/v1/embeddings", json={**body, "model": EMBED_MODEL}, headers=_llm_headers())
    if r.status_code != 200:
        raise HTTPException(502, f"llm: HTTP {r.status_code} {r.text[:300]}")
    out = r.json()
    out["model"] = body["model"]
    return out


# ---------- images (mflux, in this process, on the one MLX thread) ----------

class ImageRequest(BaseModel):
    prompt: str
    model: str | None = None
    n: int = 1
    size: str | None = None
    response_format: str | None = None
    seed: int | None = None
    steps: int | None = None
    image_url: str | None = None
    image_b64: str | None = None
    fit: str | None = None
    output_put_url: str | None = None


def _load():
    global _model
    if _model is None:
        from mflux.models.flux2.variants.edit.flux2_klein_edit import Flux2KleinEdit
        t0 = time.time()
        _model = Flux2KleinEdit(model_path=IMAGE_MODEL_PATH)
        _state["image_loaded"] = True
        print(f"image model {IMAGE_MODEL_PATH} loaded in {time.time() - t0:.0f}s", flush=True)
    return _model


def _unload():
    global _model
    _model = None
    _state["image_loaded"] = False
    gc.collect()
    import mlx.core as mx
    mx.clear_cache()


def _render(ref: Image.Image | None, w: int, h: int, prompt: str, seed: int, steps: int) -> Image.Image:
    model = _load()
    with tempfile.TemporaryDirectory() as tmp:
        paths = None
        if ref is not None:
            paths = [str(Path(tmp) / "ref.png")]
            ref.save(paths[0])
        out = model.generate_image(seed=seed, prompt=prompt, image_paths=paths, width=w, height=h,
                                   num_inference_steps=steps, guidance=1.0)
    return out.image.convert("RGB")


@app.post("/v1/images/generations")
async def generations(req: ImageRequest):
    if req.model and req.model not in IMAGE_IDS:
        raise HTTPException(404, f"unknown model {req.model!r}")
    if req.n != 1:
        raise HTTPException(400, "n must be 1 on this host")
    size = parse_size(req.size)
    async with httpx.AsyncClient(timeout=120) as c:
        src = None
        if req.image_url or req.image_b64:
            data = await fetch_bytes(c, req.image_url) if req.image_url else base64.b64decode(req.image_b64)
            src = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        if size is None:
            size = (src.width - src.width % 16, src.height - src.height % 16) if src else (1024, 1024)
        w, h = size
        rw, rh = render_size(w, h, IMAGE_RENDER_SCALE)
        if src is None:
            ref = None                                                 # no photo: plain text-to-image
        elif req.fit == "cover":
            ref = ImageOps.fit(src, (rw, rh), Image.LANCZOS)
        else:
            ref = fit_contain(src, rw, rh)
        seed = req.seed if req.seed is not None else random.randrange(2**31)
        steps = req.steps or IMAGE_STEPS
        async with _render_lock:                                     # one render at a time: one GPU
            t0 = time.time()
            try:
                img = await asyncio.get_running_loop().run_in_executor(_mlx, _render, ref, rw, rh, req.prompt, seed, steps)
            except Exception as e:
                _state["errors"] += 1
                raise HTTPException(500, f"render failed: {type(e).__name__}: {e}")
            _state.update(renders=_state["renders"] + 1, last_render=time.time(), last_render_s=round(time.time() - t0, 1))
        if img.size != (w, h):
            img = ImageOps.fit(img, (w, h), Image.LANCZOS)
        data = png(img)
        if req.output_put_url:
            r = await c.put(req.output_put_url, content=data, headers={"content-type": "image/png"})
            if r.status_code not in (200, 201, 204):
                raise HTTPException(502, f"could not store the image: HTTP {r.status_code}")
            return {"created": int(time.time()), "data": [{"url": public_url(req.output_put_url)}]}
    return {"created": int(time.time()), "data": [{"b64_json": base64.b64encode(data).decode()}]}


# ---------- service ----------

@app.get("/health")
@app.get("/health/liveliness")
async def health():
    llm = False
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            llm = (await c.get(f"{LLM_URL}/health")).status_code == 200
    except httpx.HTTPError:
        pass
    return {"ok": llm, "llm": llm, **_state}


@app.get("/v1/models")
async def models():
    ids = ["qwen3.6-35b", "qwen3-embedding-0.6b", "qwen-image-edit"]
    return {"object": "list", "data": [{"id": i, "object": "model", "owned_by": "inf-mac"} for i in ids]}


@app.on_event("startup")
async def _idle_unloader():
    if IMAGE_IDLE_UNLOAD_S <= 0:
        return

    async def loop():
        while True:
            await asyncio.sleep(30)
            if _model is not None and not _render_lock.locked() and time.time() - _state["last_render"] > IMAGE_IDLE_UNLOAD_S:
                async with _render_lock:
                    await asyncio.get_running_loop().run_in_executor(_mlx, _unload)
    asyncio.create_task(loop())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("BIND", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")), workers=1)
