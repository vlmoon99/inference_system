"""Conformance — does this host speak the contract?

    python -m inference.conformance --host rtx3090x3            # from its host.yaml (localhost ports)
    python -m inference.conformance --host rtx3090x3 --at 100.64.0.10
    python -m inference.conformance --image http://x:8102 --video http://x:8104 --tts http://x:8105 --llm http://x:8010/v1
    python -m inference.conformance ... --quick                 # health + shape only, no renders

Per service: /health shape + capabilities block, a tiny real /generate, then
/files/<path> round-trip. Exit 0 = supported hardware. Legacy hosts (no
capabilities block) pass with a warning so an un-upgraded adapter is visible,
not blocking.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import httpx

OK, WARN, FAIL = "ok", "warn", "FAIL"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, service: str, level: str, msg: str) -> None:
        self.rows.append((service, level, msg))
        print(f"  [{level:4s}] {service:6s} {msg}", flush=True)

    @property
    def failed(self) -> bool:
        return any(l == FAIL for _, l, _ in self.rows)


async def check_health(c: httpx.AsyncClient, kind: str, base: str, rep: Report) -> dict | None:
    try:
        r = await c.get(f"{base}/health")
    except httpx.HTTPError as e:
        rep.add(kind, FAIL, f"/health unreachable: {e}")
        return None
    if r.status_code != 200:
        rep.add(kind, FAIL, f"/health -> {r.status_code}")
        return None
    body = r.json()
    if "loaded" not in body:
        rep.add(kind, FAIL, "/health lacks `loaded`")
        return None
    if not body["loaded"]:
        rep.add(kind, FAIL, f"/health loaded=false ({body.get('error', 'model not ready')})")
        return None
    caps = body.get("capabilities")
    if not isinstance(caps, dict) or caps.get("kind") != kind:
        rep.add(kind, WARN, "legacy host: no capabilities block (worker assumes full capability)")
    else:
        detail = caps.get("models") or caps.get("langs")
        rep.add(kind, OK, f"/health host={body.get('host', '?')} engine={body.get('engine', '?')} "
                          f"device={body.get('device', '?')} {kind}={detail}")
    return body


def result_paths(kind: str, body: dict) -> list[str]:
    """Contract v1 response shapes: image {"images":[{"path"}]}, video {"video":{"path"}},
    tts {"audio":{"path"}} (inference/CONTRACT.md)."""
    if kind == "image":
        return [i["path"] for i in body.get("images", []) if isinstance(i, dict) and i.get("path")]
    obj = body.get(kind if kind == "video" else "audio")
    return [obj["path"]] if isinstance(obj, dict) and obj.get("path") else []


async def roundtrip_file(c: httpx.AsyncClient, kind: str, base: str, rel: str, rep: Report) -> None:
    r = await c.get(f"{base}/files/{rel}")
    if r.status_code != 200 or not r.content:
        rep.add(kind, FAIL, f"/files/{rel} -> {r.status_code}")
        return
    rep.add(kind, OK, f"/files round-trip {len(r.content)} bytes")
    # traversal must be refused
    r = await c.get(f"{base}/files/../../etc/passwd")
    if r.status_code == 200:
        rep.add(kind, FAIL, "/files served a path outside ASSETS_DIR")


async def check_image(c, base, rep, quick):
    body = await check_health(c, "image", base, rep)
    if body is None or quick:
        return
    t0 = time.time()
    r = await c.post(f"{base}/generate", json={"prompt": "conformance swatch, plain grey", "product_id": "conformance",
                                                "width": 256, "height": 256, "steps": 1, "n": 1})
    paths = result_paths("image", r.json()) if r.status_code == 200 else []
    if not paths:
        rep.add("image", FAIL, f"/generate -> {r.status_code} {r.text[:120]}")
        return
    rep.add("image", OK, f"/generate 256² in {time.time() - t0:.1f}s")
    await roundtrip_file(c, "image", base, paths[0], rep)


async def check_video(c, base, rep, quick):
    body = await check_health(c, "video", base, rep)
    if body is None or quick:
        return
    caps = body.get("capabilities") or {}
    models = caps.get("models") or []
    model = "5b" if "5b" in models else (models[0] if models else None)
    payload = {"prompt": "conformance: a grey wall", "product_id": "conformance", "width": 256, "height": 448,
               "num_frames": 9}
    if model:
        payload["model"] = model
    t0 = time.time()
    r = await c.post(f"{base}/generate", json=payload)
    paths = result_paths("video", r.json()) if r.status_code == 200 else []
    if not paths:
        rep.add("video", FAIL, f"/generate({model or 'default'}) -> {r.status_code} {r.text[:120]}")
        return
    rep.add("video", OK, f"/generate {model or 'default'} 9 frames in {time.time() - t0:.1f}s")
    await roundtrip_file(c, "video", base, paths[0], rep)
    if models:
        r = await c.post(f"{base}/generate", json={**payload, "model": "no-such-model"})
        if r.status_code != 422:
            rep.add("video", WARN, f"unknown model -> {r.status_code}, expected 422")


async def check_tts(c, base, rep, quick):
    body = await check_health(c, "tts", base, rep)
    if body is None or quick:
        return
    langs = (body.get("capabilities") or {}).get("langs") or ["en"]
    samples = {"en": "Conformance check.", "uk": "Перевірка відповідності.", "ru": "Проверка соответствия."}
    for lang in langs:
        t0 = time.time()
        r = await c.post(f"{base}/generate", json={"text": samples.get(lang, "Conformance check."), "product_id": "conformance", "lang": lang})
        paths = result_paths("tts", r.json()) if r.status_code == 200 else []
        if not paths:
            rep.add("tts", FAIL, f"/generate lang={lang} -> {r.status_code} {r.text[:120]}")
            continue
        rep.add("tts", OK, f"/generate lang={lang} in {time.time() - t0:.1f}s")
        await roundtrip_file(c, "tts", base, paths[0], rep)


async def check_llm(c, base, rep, quick):
    root = base[:-3] if base.endswith("/v1") else base
    try:
        r = await c.get(f"{root}/v1/models")
    except httpx.HTTPError as e:
        rep.add("llm", FAIL, f"/v1/models unreachable: {e}")
        return
    if r.status_code != 200:
        rep.add("llm", FAIL, f"/v1/models -> {r.status_code}")
        return
    ids = [m.get("id") for m in r.json().get("data", [])]
    rep.add("llm", OK, f"/v1/models {ids}")
    if quick or not ids:
        return
    r = await c.post(f"{root}/v1/chat/completions", json={
        "model": ids[0], "max_tokens": 8,
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
    })
    if r.status_code != 200:
        rep.add("llm", FAIL, f"/v1/chat/completions -> {r.status_code} {r.text[:120]}")
        return
    rep.add("llm", OK, "/v1/chat/completions answered")


def urls_from_host(name: str, at: str) -> dict[str, str]:
    from inference.hostspec import load
    p = load(name)
    env = p.service_urls(at)
    return {"llm": env.get("LLM_BASE_URL", ""), "image": env.get("GEN_IMAGE_URL", ""),
            "video": env.get("GEN_VIDEO_URL", ""), "tts": env.get("GEN_TTS_URL", "")}


async def run(urls: dict[str, str], quick: bool) -> int:
    rep = Report()
    timeout = httpx.Timeout(60.0 if quick else 1800.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as c:
        checks = {"llm": check_llm, "image": check_image, "video": check_video, "tts": check_tts}
        for kind, fn in checks.items():
            if urls.get(kind):
                print(f"{kind} @ {urls[kind]}")
                await fn(c, urls[kind].rstrip("/"), rep, quick)
            else:
                print(f"{kind}: not configured on this host — skipped")
    print()
    print("RESULT:", "FAIL" if rep.failed else "PASS",
          f"({sum(l == WARN for _, l, _ in rep.rows)} warning(s))")
    return 1 if rep.failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="host.yaml name under inference/hosts/")
    ap.add_argument("--at", default="localhost", help="address the host's ports are reached at")
    ap.add_argument("--llm"), ap.add_argument("--image"), ap.add_argument("--video"), ap.add_argument("--tts")
    ap.add_argument("--quick", action="store_true", help="health + shape only, no renders")
    a = ap.parse_args()
    urls = urls_from_host(a.host, a.at) if a.host else {}
    for k in ("llm", "image", "video", "tts"):
        if getattr(a, k):
            urls[k] = getattr(a, k)
    if not any(urls.values()):
        ap.error("give --host <name> or at least one service URL")
    sys.exit(asyncio.run(run(urls, a.quick)))


if __name__ == "__main__":
    main()
