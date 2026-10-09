import time

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA", tmp_path)
    monkeypatch.setattr(main, "STATE_FILE", tmp_path / "admin.json")
    main._fails.clear()
    return TestClient(main.app)


def test_password_hash_roundtrip():
    h = main.hash_password("correct horse battery")
    assert main.check_password("correct horse battery", h)
    assert not main.check_password("wrong", h)


def test_first_visit_sets_password_exactly_once(client):
    assert client.get("/api/auth/state").json() == {"setup_needed": True, "logged_in": False}
    assert client.post("/api/auth/setup", json={"password": "short"}).status_code == 400
    r = client.post("/api/auth/setup", json={"password": "a-long-password-1"})
    assert r.status_code == 200 and main.COOKIE in r.cookies
    assert client.get("/api/auth/state").json() == {"setup_needed": False, "logged_in": True}
    # a second device can't take over
    other = TestClient(main.app)
    assert other.post("/api/auth/setup", json={"password": "attacker-password"}).status_code == 409
    assert other.get("/api/nodes").status_code == 401


def test_login_and_logout(client):
    client.post("/api/auth/setup", json={"password": "a-long-password-1"})
    fresh = TestClient(main.app)
    assert fresh.post("/api/auth/login", json={"password": "nope-nope-nope"}).status_code == 401
    assert fresh.post("/api/auth/login", json={"password": "a-long-password-1"}).status_code == 200
    assert fresh.get("/api/auth/state").json()["logged_in"] is True


def test_session_expiry_and_tamper():
    st = {"session_secret": "s" * 64}
    tok = main.make_session(st)
    assert main.valid_session(tok, st)
    exp, mac = tok.split(".")
    assert not main.valid_session(f"{int(exp) + 999}.{mac}", st)          # extended expiry → bad mac
    old = str(int(time.time()) - 1)
    assert not main.valid_session(f"{old}.{main.sign(old, st['session_secret'])}", st)


def test_reset_forces_setup_again(client, monkeypatch):
    client.post("/api/auth/setup", json={"password": "a-long-password-1"})
    import importlib
    import reset  # noqa: F401 — runs the reset
    importlib.reload(reset)
    assert client.get("/api/auth/state").json() == {"setup_needed": True, "logged_in": False}
