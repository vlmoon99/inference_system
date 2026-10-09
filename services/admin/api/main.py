"""inf-admin: the operator console for inference_system, tailnet-only.

Login: the first visit (from any headscale device) sets the password, stored as an scrypt hash in
$ADMIN_DATA/admin.json. After that, password → signed session cookie. Reset = `python -m reset` in the
container (deletes the hash, so the next visit sets a new one).

    /api/auth/{state,setup,login,logout}
    /api/nodes                                     every node-agent: /system + /containers
    /api/nodes/{node}/containers/{name}/{action}   start | stop | restart
    /api/nodes/{node}/containers/{name}/logs
    /api/models                                    LiteLLM model list
    /api/projects  (GET, POST)                     one LiteLLM virtual key per project
    /api/projects/{token}/revoke
    /api/usage?days=N                              per project / day / model, from LiteLLM_SpendLogs
    /api/play/{chat,image}                         playground through the gateway (master key)
Static web build is served at /.
Env: ADMIN_DATA, LITELLM_URL, LITELLM_MASTER_KEY, LITELLM_DB_URL, NODES ("name=url,…"), AGENT_TOKEN.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA = Path(os.environ.get("ADMIN_DATA", "/data"))
STATE_FILE = DATA / "admin.json"
LITELLM_URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:8000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
DB_URL = os.environ.get("LITELLM_DB_URL", "")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
NODES = dict(p.split("=", 1) for p in os.environ.get("NODES", "").split(",") if "=" in p)
WEB = Path(__file__).parent / "web"
COOKIE = "inf_admin"
SESSION_S = 7 * 24 * 3600
MIN_PASSWORD = 10

app = FastAPI(title="inf-admin", docs_url=None, redoc_url=None)


# ---------- password + session (stdlib only) ----------

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, _ = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(pw, base64.b64decode(salt)), stored)


def sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(st: dict) -> str:
    exp = str(int(time.time()) + SESSION_S)
    return f"{exp}.{sign(exp, st['session_secret'])}"


def valid_session(token: str | None, st: dict) -> bool:
    if not token or "session_secret" not in st or "." not in token:
        return False
    exp, mac = token.split(".", 1)
    return hmac.compare_digest(mac, sign(exp, st["session_secret"])) and exp.isdigit() and int(exp) > time.time()


def require_login(request: Request) -> None:
    if not valid_session(request.cookies.get(COOKIE), load_state()):
        raise HTTPException(401, "login required")


class Password(BaseModel):
    password: str


_setup_lock = asyncio.Lock()


@app.get("/api/auth/state")
def auth_state(request: Request):
    st = load_state()
    return {"setup_needed": "password_hash" not in st, "logged_in": valid_session(request.cookies.get(COOKIE), st)}


def _set_cookie(resp: Response, st: dict) -> None:
    resp.set_cookie(COOKIE, make_session(st), max_age=SESSION_S, httponly=True, samesite="strict")


@app.post("/api/auth/setup")
async def setup(body: Password, response: Response):
    async with _setup_lock:                      # first come, first served, exactly once
        st = load_state()
        if "password_hash" in st:
            raise HTTPException(409, "password already set")
        if len(body.password) < MIN_PASSWORD:
            raise HTTPException(400, f"password must be at least {MIN_PASSWORD} characters")
        st = {"password_hash": hash_password(body.password), "session_secret": secrets.token_hex(32),
              "set_at": int(time.time())}
        save_state(st)
    _set_cookie(response, st)
    print('{"event":"admin_password_set"}', flush=True)
    return {"ok": True}


_fails: dict[str, list[float]] = {}


@app.post("/api/auth/login")
async def login(body: Password, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    recent = [t for t in _fails.get(ip, []) if time.time() - t < 300]
    if len(recent) >= 10:
        raise HTTPException(429, "too many attempts; wait 5 minutes")
    st = load_state()
    if "password_hash" not in st or not await asyncio.to_thread(check_password, body.password, st["password_hash"]):
        _fails[ip] = recent + [time.time()]
        await asyncio.sleep(1)
        raise HTTPException(401, "wrong password")
    _fails.pop(ip, None)
    _set_cookie(response, st)
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


# ---------- nodes ----------

async def agent(c: httpx.AsyncClient, node: str, method: str, path: str, **kw):
    if node not in NODES:
        raise HTTPException(404, f"unknown node {node!r}")
    r = await c.request(method, NODES[node].rstrip("/") + path, headers={"X-Agent-Token": AGENT_TOKEN}, **kw)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:300])
    return r.json()


@app.get("/api/nodes", dependencies=[Depends(require_login)])
async def nodes():
    async with httpx.AsyncClient(timeout=6) as c:
        async def one(name: str) -> dict:
            try:
                system, containers = await asyncio.gather(agent(c, name, "GET", "/system"),
                                                          agent(c, name, "GET", "/containers"))
                return {"name": name, "ok": True, "system": system, "containers": containers}
            except (httpx.HTTPError, HTTPException) as e:
                return {"name": name, "ok": False, "error": f"{type(e).__name__}: {getattr(e, 'detail', e)}"[:300]}
        return await asyncio.gather(*(one(n) for n in NODES))


@app.post("/api/nodes/{node}/containers/{name}/{action}", dependencies=[Depends(require_login)])
async def container_action(node: str, name: str, action: str):
    async with httpx.AsyncClient(timeout=120) as c:
        return await agent(c, node, "POST", f"/containers/{name}/{action}")


@app.get("/api/nodes/{node}/containers/{name}/logs", dependencies=[Depends(require_login)])
async def container_logs(node: str, name: str, tail: int = 300):
    async with httpx.AsyncClient(timeout=20) as c:
        return await agent(c, node, "GET", f"/containers/{name}/logs", params={"tail": tail})


# ---------- gateway: models, projects (keys), usage ----------

async def litellm(method: str, path: str, timeout: float = 30, **kw):
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.request(method, LITELLM_URL + path, headers={"Authorization": f"Bearer {MASTER_KEY}"}, **kw)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"gateway: {r.text[:300]}")
    return r.json()


@app.get("/api/models", dependencies=[Depends(require_login)])
async def models():
    info = await litellm("GET", "/v1/model/info")
    return [{"name": m["model_name"], "mode": (m.get("model_info") or {}).get("mode"),
             "backend": m["litellm_params"].get("model"), "api_base": m["litellm_params"].get("api_base")}
            for m in info.get("data", [])]


def q(sql: str, *args) -> list[dict]:
    with psycopg.connect(DB_URL, connect_timeout=5) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, args)
        return cur.fetchall()


@app.get("/api/projects", dependencies=[Depends(require_login)])
async def projects():
    return await asyncio.to_thread(q, """
        select token, key_alias as project, key_name as key_hint, created_at, expires, blocked, spend,
               models, metadata
        from "LiteLLM_VerificationToken" order by created_at desc nulls last""")


class NewProject(BaseModel):
    name: str


@app.post("/api/projects", dependencies=[Depends(require_login)])
async def create_project(body: NewProject):
    name = body.name.strip()
    if not name or len(name) > 64:
        raise HTTPException(400, "project name must be 1..64 characters")
    out = await litellm("POST", "/key/generate", json={"key_alias": name, "metadata": {"project": name}})
    print(json.dumps({"event": "project_key_created", "project": name}), flush=True)
    return {"project": name, "key": out["key"]}       # shown once; only the hash stays in the DB


@app.post("/api/projects/{token}/revoke", dependencies=[Depends(require_login)])
async def revoke(token: str):
    await litellm("POST", "/key/delete", json={"keys": [token]})
    print(json.dumps({"event": "project_key_revoked", "token": token[:12]}), flush=True)
    return {"ok": True}


@app.get("/api/usage", dependencies=[Depends(require_login)])
async def usage(days: int = 30):
    return await asyncio.to_thread(q, """
        select date_trunc('day', s."startTime")::date as day,
               coalesce(t.key_alias, case when s.api_key = '' then '(master)' else left(s.api_key, 10) end) as project,
               coalesce(nullif(s.model_group, ''), s.model) as model, s.call_type,
               count(*) as requests, sum(s.prompt_tokens) as prompt_tokens,
               sum(s.completion_tokens) as completion_tokens,
               round(avg(s.request_duration_ms)) as avg_ms,
               count(*) filter (where s.status = 'failure') as failures
        from "LiteLLM_SpendLogs" s left join "LiteLLM_VerificationToken" t on t.token = s.api_key
        where s."startTime" > now() - make_interval(days => %s)
        group by 1, 2, 3, 4 order by 1 desc, 2, 3""", max(1, min(days, 365)))


# ---------- playground ----------

class Chat(BaseModel):
    model: str
    messages: list[dict]


class ImageGen(BaseModel):
    model: str
    prompt: str
    size: str = "1024x1024"
    image_b64: str | None = None


@app.post("/api/play/chat", dependencies=[Depends(require_login)])
async def play_chat(body: Chat):
    return await litellm("POST", "/v1/chat/completions", timeout=300,
                         json={"model": body.model, "messages": body.messages, "max_tokens": 1024})


@app.post("/api/play/image", dependencies=[Depends(require_login)])
async def play_image(body: ImageGen):
    payload = {"model": body.model, "prompt": body.prompt, "size": body.size}
    if body.image_b64:
        payload["image_b64"] = body.image_b64
    return await litellm("POST", "/v1/images/generations", timeout=600, json=payload)


# ---------- static web ----------

if (WEB / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    index = WEB / "index.html"
    if not index.is_file():
        raise HTTPException(404, "web build missing")
    return FileResponse(index)
