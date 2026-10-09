"""Keep an adapter's default model resident on its GPU, 24/7.

A cold first render pays the whole weight load: LTX-2.5 on an RTX 3090 took
151 s for a 9-frame clip cold and 14 s warm (2026-09-30). ComfyUI keeps a model
in VRAM after a render until something evicts it (a restart, /free, another
model), so one tiny render is enough — this module does that render for the
customer, in the background:

  KEEP_WARM=once   one warm-up render when the adapter starts (the Spark: its
                   unified memory makes a VRAM reading meaningless)
  KEEP_WARM=vram   warm at start, then every KEEP_WARM_EVERY_S check the GPU and
                   render again when less than KEEP_WARM_MIN_GB is in use — the
                   model was dropped (discrete GPUs: the rtx3090x3 box)
  unset / other    off

  KEEP_WARM_GAUGE=process  (with vram) read ComfyUI's own torch_vram_total instead of
                   the device total — the one number that still means "this process
                   holds its models" on unified memory (dgx-spark-2: one ComfyUI per
                   role, the page cache would otherwise read as used memory)

A warm-up only runs while ComfyUI's queue is empty, so it never delays a real
job; a failed one is retried on the next tick. State (warm, last warm-up and how
long it took) is exposed for /health. Stdlib + httpx + fastapi only — adapters
never import `ads`.
"""

import asyncio
import os
import time
from typing import Awaitable, Callable

import httpx
from fastapi import FastAPI

MODE = os.environ.get("KEEP_WARM", "").strip().lower()
EVERY_S = float(os.environ.get("KEEP_WARM_EVERY_S", "120"))
MIN_GB = float(os.environ.get("KEEP_WARM_MIN_GB", "12"))
GAUGE = os.environ.get("KEEP_WARM_GAUGE", "device").strip().lower()  # device | process
FIRST_DELAY_S = 5.0

state: dict = {"mode": MODE or "off", "warm": False, "at": None, "took_s": None, "error": ""}


async def _idle(c: httpx.AsyncClient, comfy: str) -> bool:
    q = (await c.get(f"{comfy}/queue")).json()
    return not q.get("queue_running") and not q.get("queue_pending")


async def _vram_used_gb(c: httpx.AsyncClient, comfy: str) -> float | None:
    devices = (await c.get(f"{comfy}/system_stats")).json().get("devices") or []
    if not devices:
        return None
    d = devices[0]
    if GAUGE == "process":
        return d.get("torch_vram_total", 0) / 2**30
    return (d.get("vram_total", 0) - d.get("vram_free", 0)) / 2**30


async def _tick(comfy: str, warm: Callable[[], Awaitable[object]]) -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        if state["warm"] and MODE == "vram":
            used = await _vram_used_gb(c, comfy)
            if used is not None and used < MIN_GB:
                print(f"[keepwarm] only {used:.1f} GB on the GPU — the model was dropped", flush=True)
                state["warm"] = False
        if state["warm"] or not await _idle(c, comfy):
            return
    t0 = time.monotonic()
    await warm()
    state.update(warm=True, at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 took_s=round(time.monotonic() - t0, 1), error="")
    print(f"[keepwarm] warm in {state['took_s']} s", flush=True)


async def _loop(comfy: str, warm: Callable[[], Awaitable[object]]) -> None:
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            await _tick(comfy, warm)
        except Exception as e:  # noqa: BLE001 — ComfyUI still starting, a bad render: next tick
            state["error"] = f"{type(e).__name__}: {e}"[:200]
            print(f"[keepwarm] warm-up failed ({state['error']}) — retrying", flush=True)
        if MODE == "once" and state["warm"]:
            return
        await asyncio.sleep(EVERY_S)


def start(app: FastAPI, comfy_url: str, warm: Callable[[], Awaitable[object]]) -> None:
    """Register the background warm-up on `app` (a no-op unless KEEP_WARM is set)."""
    if MODE not in ("once", "vram"):
        return

    async def _on_startup() -> None:
        app.state.keepwarm_task = asyncio.create_task(_loop(comfy_url, warm))

    app.router.add_event_handler("startup", _on_startup)
