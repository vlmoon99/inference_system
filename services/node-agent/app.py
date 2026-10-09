"""inf-node-agent — one per machine: what the box is doing + control of its inference containers.

    GET  /system                          GPUs (nvidia-smi), load, RAM; `unified` on GB10-style boxes
    GET  /containers                      the compose project's containers (name, state, health, image, uptime)
    POST /containers/{name}/{action}      start | stop | restart
    GET  /containers/{name}/logs?tail=N   last N log lines

Every request needs `X-Agent-Token: $AGENT_TOKEN`. Bound to the tailnet IP only (BIND). It can touch only
containers labelled with the compose project PROJECT (default "inf"): never anything else on the box.
Env: AGENT_TOKEN, PROJECT, HOST_NAME, BIND, PORT.
"""

import hmac
import os
import shutil
import subprocess
import time

import docker
from docker.errors import NotFound
from fastapi import Depends, FastAPI, Header, HTTPException

TOKEN = os.environ.get("AGENT_TOKEN", "")
PROJECT = os.environ.get("PROJECT", "inf")
HOST_NAME = os.environ.get("HOST_NAME", "")
SYSTEM_TTL_S = 2.0
_FIELDS = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit"
_cache: dict = {"at": 0.0, "value": None}


def auth(x_agent_token: str = Header("")) -> None:
    if not TOKEN or not hmac.compare_digest(x_agent_token, TOKEN):
        raise HTTPException(401, "bad agent token")


app = FastAPI(title="inf-node-agent", dependencies=[Depends(auth)])
_docker = docker.from_env()


def _num(s: str) -> float | None:
    try:
        return float(s.strip().split(" ")[0])
    except ValueError:
        return None          # "[N/A]" on unified-memory GPUs


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
        if len(f) >= 8:
            rows.append({"index": int(f[0]), "name": f[1], "util": _num(f[2]), "mem_used_mb": _num(f[3]),
                         "mem_total_mb": _num(f[4]), "temp_c": _num(f[5]), "power_w": _num(f[6]),
                         "power_limit_w": _num(f[7])})
    return rows


def meminfo() -> dict:
    kb: dict[str, int] = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, v = line.split(":", 1)
            kb[k] = int(v.split()[0])
    gb = lambda k: round(kb.get(k, 0) / 1048576, 1)  # noqa: E731
    return {"total_gb": gb("MemTotal"), "used_gb": round(gb("MemTotal") - gb("MemAvailable"), 1),
            "swap_total_gb": gb("SwapTotal"), "swap_used_gb": round(gb("SwapTotal") - gb("SwapFree"), 1)}


@app.get("/system")
def system() -> dict:
    now = time.monotonic()
    if _cache["value"] is None or now - _cache["at"] >= SYSTEM_TTL_S:
        g = gpus()
        _cache.update(at=now, value={
            "host": HOST_NAME, "gpus": g, "load": list(os.getloadavg()), "cpus": os.cpu_count() or 0,
            "mem": meminfo(), "unified": bool(g) and all(x["mem_total_mb"] is None for x in g)})
    return _cache["value"]


def _ours(name: str):
    try:
        c = _docker.containers.get(name)
    except NotFound:
        raise HTTPException(404, f"no container {name!r}")
    if c.labels.get("com.docker.compose.project") != PROJECT:
        raise HTTPException(403, f"{name!r} is not part of project {PROJECT!r}")
    return c


def _row(c) -> dict:
    st = c.attrs.get("State", {})
    return {"name": c.name, "service": c.labels.get("com.docker.compose.service"), "state": st.get("Status"),
            "health": (st.get("Health") or {}).get("Status"), "image": c.attrs["Config"]["Image"],
            "started_at": st.get("StartedAt"), "restarts": c.attrs.get("RestartCount", 0)}


@app.get("/containers")
def containers() -> list[dict]:
    cs = _docker.containers.list(all=True, filters={"label": f"com.docker.compose.project={PROJECT}"})
    return sorted((_row(c) for c in cs), key=lambda r: r["name"])


@app.post("/containers/{name}/{action}")
def act(name: str, action: str) -> dict:
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "action must be start, stop or restart")
    c = _ours(name)
    getattr(c, action)()
    c.reload()
    print(f'{{"event":"container_{action}","name":"{name}"}}', flush=True)
    return _row(c)


@app.get("/containers/{name}/logs")
def logs(name: str, tail: int = 200) -> dict:
    c = _ours(name)
    text = c.logs(tail=max(1, min(tail, 2000)), timestamps=True).decode("utf-8", "replace")
    return {"name": name, "lines": text.splitlines()}
