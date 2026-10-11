import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

import app

TEMPLATE = json.loads((Path(__file__).parent / "workflows" / "qwen_image_edit_2511_api.json").read_text())


def test_parse_size():
    assert app.parse_size(None) is None
    assert app.parse_size("auto") is None
    assert app.parse_size("1024x1280") == (1024, 1280)
    assert app.parse_size("1000x1000") == (992, 992)          # snapped to /16
    for bad in ("big", "10x10", "4096x1024"):
        with pytest.raises(HTTPException):
            app.parse_size(bad)


def test_public_url_strips_signature():
    u = "https://media.example/bucket/a/b.png?X-Amz-Signature=abc&X-Amz-Date=1"
    assert app.public_url(u) == "https://media.example/bucket/a/b.png"


def test_output_urls_count_must_match_n():
    assert app.output_urls(app.ImageRequest(prompt="p")) is None
    assert app.output_urls(app.ImageRequest(prompt="p", output_put_url="u")) == ["u"]
    with pytest.raises(HTTPException):
        app.output_urls(app.ImageRequest(prompt="p", n=2, output_put_url="u"))


def test_build_workflow_patches_by_class_type():
    wf = app.build_workflow(TEMPLATE, "make it autumn", "inf-1.png", 42, 6)
    by = {n["class_type"]: n["inputs"] for n in wf.values()}
    prompts = [n["inputs"]["prompt"] for n in wf.values() if n["class_type"] == "TextEncodeQwenImageEditPlus"]
    assert "make it autumn" in prompts and "" in prompts      # negative stays empty
    assert by["LoadImage"]["image"] == "inf-1.png"
    assert by["KSampler"]["seed"] == 42 and by["KSampler"]["steps"] == 6
    assert "__PROMPT__" not in json.dumps(TEMPLATE) or "__PROMPT__" not in json.dumps(wf)


def test_fit_contain_keeps_whole_photo():
    tall = Image.new("RGB", (100, 400), (255, 0, 0))
    out = app.fit_contain(tall, 512, 512)
    assert out.size == (512, 512)
    assert out.getpixel((256, 256)) == (255, 0, 0)            # photo centred, not cropped away


def test_api_key_guards_v1_routes(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(app, "API_KEY", "secret-1")
    c = TestClient(app.app)
    assert c.get("/v1/models").status_code == 401
    assert c.get("/v1/models", headers={"authorization": "Bearer nope"}).status_code == 401
    assert c.get("/v1/models", headers={"authorization": "Bearer secret-1"}).status_code == 200


def test_paint_size_follows_quality(monkeypatch):
    assert app.paint_size(1024, 1280, "high") == (1024, 1280)
    assert app.paint_size(1024, 1280, "medium") == (768, 960)
    assert app.paint_size(1024, 1280, "low") == (512, 640)
    assert app.paint_size(300, 300, "low") == (256, 256)          # never below the model's floor
    assert app.paint_size(1024, 1280, None) == (1024, 1280)       # no word from the caller: IMAGE_QUALITY (high)
    monkeypatch.setattr(app, "IMAGE_QUALITY", "low")
    assert app.paint_size(1024, 1280, None) == (512, 640)
    assert app.paint_size(1024, 1280, "hd") == (512, 640)         # an unknown word is the default, not an error
    assert app.paint_size(1024, 1280, "high") == (1024, 1280)     # the caller's word wins


def test_cloud_workflow_paints_at_the_input_size():
    cloud = json.loads((Path(__file__).parent / "workflows" / "qwen_image_edit_2511_cloud_api.json").read_text())
    kinds = {n["class_type"] for n in cloud.values()}
    assert "FluxKontextImageScale" not in kinds                   # that node snaps every input to ~1 MP
    assert "LoraLoaderModelOnly" in kinds                         # 4 steps need the Lightning LoRA on the stock model
    wf = app.build_workflow(cloud, "a cup", "ref.png", 7, None)
    assert [n["inputs"]["image"] for n in wf.values() if n["class_type"] == "LoadImage"] == ["ref.png"]
    assert [n["inputs"]["prompt"] for n in wf.values() if n["class_type"] == "TextEncodeQwenImageEditPlus"] == ["a cup", ""]
    for node in wf.values():                                      # every link points at a node that exists
        for v in node["inputs"].values():
            if isinstance(v, list):
                assert v[0] in wf


def test_cloud_run_reads_the_job_and_fetches_the_signed_link_without_the_key(monkeypatch):
    import asyncio
    import httpx
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path, request.headers.get("x-api-key")))
        if request.url.path.endswith("/prompt"):
            return httpx.Response(200, json={"prompt_id": "j1"})
        if request.url.path.endswith("/job/j1/status"):
            return httpx.Response(200, json={"status": "success"})
        if request.url.path.endswith("/jobs/j1"):
            return httpx.Response(200, json={"outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}})
        if request.url.path.endswith("/view"):
            return httpx.Response(302, headers={"location": "https://storage.example/a.png?sig=1"})
        return httpx.Response(200, content=b"PNG")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(app, "COMFY_URL", "https://cloud.example/api")
    monkeypatch.setattr(app, "COMFY_HEADERS", {"X-API-Key": "k"})
    monkeypatch.setattr(app.asyncio, "sleep", no_sleep)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await app.cloud_run(c, {})

    assert asyncio.run(run()) == b"PNG"
    assert [s for s in seen if s[0] == "storage.example"] == [("storage.example", "/a.png", None)]
    assert all(key == "k" for host, _, key in seen if host == "cloud.example")


def test_cloud_failures_are_said_plainly():
    assert "out of credits" in app.cloud_failure(402, "").detail
    assert "key is wrong" in app.cloud_failure(401, "").detail
    assert "HTTP 500" in app.cloud_failure(500, "boom").detail
