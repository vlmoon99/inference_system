"""GET /system — what the box an adapter runs on is doing right now (the admin
Infra page's nvtop): every GPU from nvidia-smi, CPU load, RAM. Shared by the
gen-* adapters; the machine is the unit, so each adapter on a box reports the
same GPUs and the control plane de-duplicates by host name.

Unified-memory boxes (the Spark's GB10) report GPU memory as [N/A]; there the
GPU's memory is the system RAM, so `unified` is true and RAM is the number to watch.
Cached SYSTEM_TTL_S so a page polling several adapters forks nvidia-smi rarely.
Adapters never import `ads`; this module imports only the stdlib + fastapi.
"""

import os
import shutil
import subprocess
import time

from fastapi import APIRouter

SYSTEM_TTL_S = 2.0
_FIELDS = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit"
_cache: dict = {"at": 0.0, "value": None}

router = APIRouter()


def _num(s: str) -> float | None:
    s = s.strip().split(" ")[0]
    try:
        return float(s)
    except ValueError:
        return None  # "[N/A]", "[Not Supported]"


def gpus() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={_FIELDS}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in out.strip().splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 8:
            continue
        rows.append({"index": int(f[0]), "name": f[1], "util": _num(f[2]),
                     "mem_used_mb": _num(f[3]), "mem_total_mb": _num(f[4]),
                     "temp_c": _num(f[5]), "power_w": _num(f[6]), "power_limit_w": _num(f[7])})
    return rows


def _meminfo() -> dict:
    kb: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, v = line.split(":", 1)
                kb[k] = int(v.split()[0])
    except (OSError, ValueError):
        return {}
    gb = lambda k: round(kb.get(k, 0) / 1048576, 1)  # noqa: E731
    return {"total_gb": gb("MemTotal"), "used_gb": round(gb("MemTotal") - gb("MemAvailable"), 1),
            "swap_total_gb": gb("SwapTotal"), "swap_used_gb": round(gb("SwapTotal") - gb("SwapFree"), 1)}


def snapshot() -> dict:
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < SYSTEM_TTL_S:
        return _cache["value"]
    g = gpus()
    try:
        load = list(os.getloadavg())
    except OSError:
        load = []
    value = {"host": os.environ.get("HOST_NAME", ""), "gpus": g, "load": load,
             "cpus": os.cpu_count() or 0, "mem": _meminfo(),
             "unified": bool(g) and all(x["mem_total_mb"] is None for x in g)}
    _cache.update(at=now, value=value)
    return value


@router.get("/system")
def system() -> dict:
    return snapshot()
