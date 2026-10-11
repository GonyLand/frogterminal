"""/api/preview/* — 인증 관문·본문 검사·감사로그. preview 본체는 가짜로 바꿔 tailscale 을 안 부른다."""
import pytest

from bridge import preview, server
from bridge.tests.test_server import GOOD


@pytest.fixture
def fake(monkeypatch, tmp_path):
    from bridge import config
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")   # 진짜 감사로그를 더럽히지 않게
    svc = [{"id": "549:4567", "port": 4567, "pid": 549, "cmd": "node", "cwd": "/x", "project": "alpha", "http": 200}]
    monkeypatch.setattr(preview, "list_services", lambda root=None, probe=None: list(svc))
    monkeypatch.setattr(preview, "serve_status", lambda: {"TCP": {}, "Web": {}, "AllowFunnel": {}})
    calls = []
    monkeypatch.setattr(preview, "_serve", lambda args: (calls.append(args), (True, ""))[1])
    return calls


async def test_preview_는_인증_없이는_403(aiohttp_client, fake):
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/api/preview/services")).status == 403
    assert (await client.post("/api/preview/open", json={"id": "549:4567", "project": "alpha"})).status == 403


async def test_preview_열고_갱신하고_닫기(aiohttp_client, fake):
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/preview/services?project=alpha", headers=GOOD)
    assert res.status == 200
    assert (await res.json())["services"][0]["id"] == "549:4567"
    res = await client.post("/api/preview/open", headers=GOOD, json={"id": "549:4567", "project": "alpha"})
    assert res.status == 200
    got = await res.json()
    assert got["port"] == 4567 and got["dest"] == "http://127.0.0.1:4567"
    assert fake == [["--bg", "--https=4567", "http://127.0.0.1:4567"]]
    assert (await client.post("/api/preview/renew", headers=GOOD, json={"port": 4567})).status == 200
    assert (await client.post("/api/preview/renew", headers=GOOD, json={"port": 9999})).status == 404
    active = (await (await client.get("/api/preview/services", headers=GOOD)).json())["active"]
    assert active == {"4567": {"dest": "http://127.0.0.1:4567", "project": "alpha"}}
    res = await client.post("/api/preview/close", headers=GOOD, json={"port": 4567})
    assert (await res.json()) == {"closed": True} and fake[-1] == ["--https=4567", "off"]


async def test_preview_open_은_목록_밖_ID_와_이상한_본문을_거절(aiohttp_client, fake):
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/preview/open", headers=GOOD, json={"id": "1:7788", "project": "alpha"})
    assert res.status == 409 and "LISTEN" in (await res.json())["error"]
    assert (await client.post("/api/preview/open", headers=GOOD, json={"project": "x"})).status == 400
    assert (await client.post("/api/preview/open", headers=GOOD,
                              json={"id": "549:4567", "project": "../etc"})).status == 400
    assert (await client.post("/api/preview/renew", headers=GOOD, json={"port": "abc"})).status == 400
    assert fake == []
