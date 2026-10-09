"""Does this box render like the Spark? Run from the Spark (or any tailnet machine):
    .venv/bin/python inference/hosts/rtx3090x3/parity.py [--at 100.64.0.10] [--spark 100.64.0.1] [--video]

1. static: same /health capabilities (image, video), same ComfyUI version, same LLM id.
2. image: the same FLUX request (prompt, seed, size, steps) on both → PSNR between them.
3. --video: the same LTX t2v request on both → PSNR of three frames (a ~2 min render each).
Outputs land in --out for eyeballing. Different GPUs never give bit-identical pixels
(kernel rounding differs), but the same weights + graph + seed give the same picture:
same composition, subject and colours. A different model/quant shows up as a different
image. The PSNR line is a guide, not a calibrated threshold — look at the files."""
import argparse
import asyncio
import io
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

P = "a ceramic coffee mug on a wooden table, soft morning light, product photo"
SEED = 424242


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        b = np.asarray(Image.fromarray(b).resize((a.shape[1], a.shape[0])))
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return float("inf") if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


def verdict(db: float) -> str:
    return "same picture" if db >= 25 else "similar — inspect" if db >= 18 else "DIFFERENT — check models/settings"


async def get_json(c: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await c.get(url, timeout=5)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def static(c: httpx.AsyncClient, spark: str, box: str) -> bool:
    ok = True
    for name, port in (("image", 8102), ("video", 8104)):
        a = await get_json(c, f"http://{spark}:{port}/health")
        b = await get_json(c, f"http://{box}:{port}/health")
        if not a or not b:
            print(f"  {name:<6} skipped (spark {'up' if a else 'DOWN'}, box {'up' if b else 'DOWN'})")
            continue
        same = a.get("capabilities") == b.get("capabilities")
        ok &= same
        print(f"  {name:<6} capabilities {'identical' if same else 'DIFFER'}: spark={a.get('capabilities')} box={b.get('capabilities')}")
    a = await get_json(c, f"http://{spark}:8188/system_stats")
    b = await get_json(c, f"http://{box}:8188/system_stats")
    if a and b:
        va, vb = a["system"].get("comfyui_version"), b["system"].get("comfyui_version")
        ok &= va == vb
        print(f"  comfyui {'identical' if va == vb else 'DIFFER'}: spark={va} box={vb}")
    a = await get_json(c, f"http://{spark}:8010/v1/models")
    b = await get_json(c, f"http://{box}:8010/v1/models")
    if a and b:
        ra = {m.get("root") for m in a.get("data", [])}
        rb = {m.get("root") for m in b.get("data", [])}
        print(f"  llm    weights {'identical' if ra == rb else 'differ'}: spark={sorted(ra)} box={sorted(rb)}"
              + ("" if ra == rb else "  (fine while the Spark's worker pools the Spark's LLM first)"))
    return ok


async def image(c: httpx.AsyncClient, spark: str, box: str, out: Path) -> None:
    body = {"prompt": P, "product_id": "parity", "width": 1024, "height": 1024, "steps": 8, "seed": SEED}
    pics = {}
    for label, host in (("spark", spark), ("box", box)):
        r = await c.post(f"http://{host}:8102/generate", json=body)
        r.raise_for_status()
        path = r.json()["images"][0]["path"]
        data = (await c.get(f"http://{host}:8102/files/{path}")).content
        (out / f"image-{label}.png").write_bytes(data)
        pics[label] = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    db = psnr(pics["spark"], pics["box"])
    print(f"  image  PSNR {db:.1f} dB → {verdict(db)}   ({out}/image-{{spark,box}}.png)")


def frames(data: bytes, at=(0.25, 0.5, 0.75)) -> list[np.ndarray]:
    import av  # PyAV is in the repo .venv (whisper/ffmpeg-free decode)
    with av.open(io.BytesIO(data)) as f:
        all_frames = [fr.to_ndarray(format="rgb24") for fr in f.decode(video=0)]
    return [all_frames[int(len(all_frames) * t)] for t in at]


async def video(c: httpx.AsyncClient, spark: str, box: str, out: Path) -> None:
    """spark/box are host:port of the two video adapters (the box has two: :8104 GPU1, :8114 GPU2)."""
    body = {"prompt": P + ", slow camera push-in", "product_id": "parity", "width": 704, "height": 1280,
            "num_frames": 81, "model": "ltx2", "seed": SEED}
    clips = {}
    for label, host in (("spark", spark), ("box", box)):
        r = await c.post(f"http://{host}/generate", json=body)
        r.raise_for_status()
        path = r.json()["video"]["path"]
        data = (await c.get(f"http://{host}/files/{path}")).content
        (out / f"video-{label}.mp4").write_bytes(data)
        clips[label] = frames(data)
    dbs = [psnr(a, b) for a, b in zip(clips["spark"], clips["box"])]
    print(f"  video  PSNR at 25/50/75 %: {' / '.join(f'{d:.1f}' for d in dbs)} dB → {verdict(min(dbs))}"
          f"   ({out}/video-{{spark,box}}.mp4)")


async def main(a: argparse.Namespace) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=1800) as c:
        print("static")
        same = await static(c, a.spark, a.at)
        print("renders (same prompt, same seed)")
        await image(c, a.spark, a.at, out)
        if a.video:
            await video(c, a.ref_video or f"{a.spark}:8104", f"{a.at}:{a.video_port}", out)
    print("static check:", "PASS" if same else "FAIL — the boxes do not serve the same thing")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="100.64.0.10", help="the 3090 box")
    ap.add_argument("--spark", default="100.64.0.1")
    ap.add_argument("--video", action="store_true", help="also render one LTX clip on each (~2 min apiece)")
    ap.add_argument("--video-port", type=int, default=8104, help="the box's video adapter: 8104 (GPU1) or 8114 (GPU2)")
    ap.add_argument("--ref-video", default=None, help="reference video adapter host:port (default <spark>:8104); "
                    "100.64.0.10:8104 compares the box's GPU2 against its GPU1 — same hardware, same kernels")
    ap.add_argument("--out", default="logs/parity")
    asyncio.run(main(ap.parse_args()))
