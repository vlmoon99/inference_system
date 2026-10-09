"""Timing table for this box, run from any tailnet machine:
    .venv/bin/python inference/hosts/rtx3090x3/bench.py [--at 100.64.0.10] [--video-ports 8104,8114]
Fresh seeds every run: a repeated (seed, prompt) is a ComfyUI cache hit, not a timing."""
import argparse, asyncio, random, time
import httpx

P = "a ceramic coffee mug on a wooden table, soft morning light, product photo"
MODEL = "nvidia/Qwen3.6-35B-A3B-NVFP4"


def ltx(rnd: random.Random) -> dict:
    return {"prompt": P + ", slow camera push-in", "width": 704, "height": 1280, "num_frames": 81,
            "model": "ltx2", "seed": rnd.randrange(1 << 30)}


async def main(host: str, video_ports: list[int]) -> None:
    h = f"http://{host}"
    rnd = random.Random()
    async with httpx.AsyncClient(timeout=1800) as c:
        for i in range(2):
            t = time.time()
            (await c.post(f"{h}:8102/generate", json={"prompt": P, "width": 1024, "height": 1024, "steps": 8, "seed": rnd.randrange(1 << 30)})).raise_for_status()
            print(f"FLUX 1024² 8 steps run{i + 1}: {time.time() - t:.1f}s", flush=True)
        body = {"model": MODEL, "messages": [{"role": "user", "content": "Write a detailed 400-word product description for a smartwatch."}], "max_tokens": 512, "temperature": 0.7}
        try:  # the local vLLM is a fallback and often stopped — its row is optional
            t = time.time()
            rs = await asyncio.gather(*[c.post(f"{h}:8010/v1/chat/completions", json=body) for _ in range(8)])
            dt = time.time() - t
            toks = sum(r.json()["usage"]["completion_tokens"] for r in rs)
            print(f"vLLM 8 concurrent × 512 tok: {dt:.1f}s, {toks} tokens, {toks / dt:.0f} tok/s aggregate", flush=True)
        except httpx.ConnectError:
            print("vLLM :8010 not running — skipped", flush=True)
        for port in video_ports:
            for lab in ("first", "warm"):
                t = time.time()
                (await c.post(f"{h}:{port}/generate", json=ltx(rnd))).raise_for_status()
                print(f"LTX :{port} 704×1280×81 {lab}: {time.time() - t:.1f}s", flush=True)
        if len(video_ports) > 1:  # both renderers at once — what two video workers do
            t = time.time()
            await asyncio.gather(*[c.post(f"{h}:{port}/generate", json=ltx(rnd)) for port in video_ports])
            print(f"LTX {len(video_ports)} renderers in parallel: {time.time() - t:.1f}s wall", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="100.64.0.10")
    ap.add_argument("--video-ports", default="8104,8114", help="video adapters to time (8114 = GPU2, skipped if down)")
    a = ap.parse_args()
    ports = []
    for p in (int(x) for x in a.video_ports.split(",") if x):
        try:
            httpx.get(f"http://{a.at}:{p}/health", timeout=5).raise_for_status(); ports.append(p)
        except httpx.HTTPError:
            print(f"video :{p} not answering — skipped", flush=True)
    asyncio.run(main(a.at, ports))
