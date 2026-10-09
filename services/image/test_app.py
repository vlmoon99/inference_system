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
