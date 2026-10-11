"""2단계 브라우저 — 진짜 헤드리스 크롬을 띄워 CDP 경로를 끝까지 왕복한다(크롬 없으면 건너뜀).
프로필·장부는 tmp 로 돌리고, 페이지는 로컬 http 서버라 바깥으로 안 나간다."""
import asyncio
import http.server
import json
import os
import threading

import pytest
from aiohttp import WSMsgType

from bridge import browser, config, server
from bridge.tests.test_server import GOOD

pytestmark = pytest.mark.skipif(not os.path.exists(browser.CHROME_BIN), reason="구글 크롬 없음")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")
    yield tmp_path
    browser.stop_chrome("frogtest")                 # 남는 크롬 없이


@pytest.fixture
def site():
    class H(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            body = f"<title>frog {self.path}</title><h1 style='font-size:80px'>FROG {self.path}</h1><a id=l href='/two'>two</a>".encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_크롬을_띄워_이동하고_프레임을_받고_뒤로간다(sandbox, site):
    b = browser.Browsers()
    s = await b.get("frogtest")
    assert (sandbox / "chrome" / "frogtest" / "DevToolsActivePort").exists()
    assert browser.registry_ports() == {s.port}      # preview 가 이 포트를 내주지 않게 장부에 있다
    s.viewers = 1
    await s.start_cast()
    await s.navigate(f"{site}/one")
    for _ in range(100):
        if s.frame and (await s.history())["url"].endswith("/one"):
            break
        await asyncio.sleep(0.1)
    assert s.frame and s.frame[:2] == b"\xff\xd8"    # JPEG
    st = await s.state()
    assert st["url"].endswith("/one") and st["title"] == "frog /one" and st["canBack"] is True
    await s.navigate(f"{site}/two")
    for _ in range(50):
        if (await s.history())["url"].endswith("/two"):
            break
        await asyncio.sleep(0.1)
    await s.go(-1)
    for _ in range(50):
        if (await s.history())["url"].endswith("/one"):
            break
        await asyncio.sleep(0.1)
    assert (await s.history())["url"].endswith("/one")
    await s.set_viewport(500, 400)
    assert s.viewport == (500, 400) and s.casting
    tid = await s.new_tab(f"{site}/three")
    assert s.target_id == tid and len(await s.targets()) == 2
    await s.close_tab(tid)
    assert len(await s.targets()) == 1 and s.target_id != tid
    assert await b.stop("frogtest") is True
    assert browser.load_registry() == {}


async def test_api_와_뷰어_소켓(aiohttp_client, sandbox, site):
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/api/browser/frogtest")).status == 403           # 인증 관문
    res = await client.get("/api/browser/frogtest", headers=GOOD)
    assert (await res.json()) == {"running": False, "browsers": []}
    res = await client.post("/api/browser/frogtest/navigate", headers=GOOD, json={"url": f"{site}/nav"})
    assert res.status == 200                                                    # 안 떠 있으면 띄워서 이동
    res = await client.get("/api/browser/frogtest", headers=GOOD)
    got = await res.json()
    assert got["running"] and got["cdp"].startswith("http://127.0.0.1:")
    res = await client.post("/api/browser/frogtest/navigate", headers=GOOD, json={"url": "javascript:alert(1)"})
    assert res.status == 400
    ws = await client.ws_connect("/ws/browser/frogtest", headers=GOOD)
    seen_frame = seen_state = False
    async with asyncio.timeout(20):
        while not (seen_frame and seen_state):
            msg = await ws.receive()
            if msg.type == WSMsgType.TEXT:
                d = json.loads(msg.data)
                if d["type"] == "state" and d["url"].endswith("/nav"):
                    seen_state = True
                if d["type"] == "frame":
                    binmsg = await ws.receive()
                    assert binmsg.type == WSMsgType.BINARY and binmsg.data[:2] == b"\xff\xd8"
                    seen_frame = True
            else:
                pytest.fail(f"unexpected {msg.type}")
    await ws.send_str(json.dumps({"type": "navigate", "url": f"{site}/via-ws"}))
    async with asyncio.timeout(20):
        while True:
            msg = await ws.receive()
            if msg.type == WSMsgType.TEXT and json.loads(msg.data).get("url", "").endswith("/via-ws"):
                break
    await ws.send_str(json.dumps({"type": "navigate", "url": "file:///etc/passwd"}))
    async with asyncio.timeout(10):
        while True:
            msg = await ws.receive()
            if msg.type == WSMsgType.TEXT and json.loads(msg.data).get("type") == "error":
                break
    res = await client.get("/api/browser/frogtest/screenshot", headers=GOOD)
    assert res.status == 200 and res.headers["Content-Type"] == "image/jpeg"
    await ws.close()
    res = await client.post("/api/browser/frogtest/stop", headers=GOOD)
    assert (await res.json()) == {"stopped": True}


async def test_메모리_정책_유휴10분_종료와_상한(sandbox, monkeypatch):
    """보는 동안엔 안 끄고, 뷰어 없이 10분 넘으면 끈다. 상한 초과는 새로 열 때만 정리."""
    stopped = []
    reg = {}
    # 진짜 stop_chrome 은 장부에서도 지운다 — 가짜도 그래야 '두 번 끄기'가 안 생긴다
    b = browser.Browsers(stop=lambda p: (stopped.append(p), reg.pop(p, None), True)[2])
    monkeypatch.setattr(browser, "load_registry", lambda: dict(reg))
    monkeypatch.setattr(browser, "save_registry", lambda r: (reg.clear(), reg.update(r)))
    def fake(project, active, viewers):
        s = browser.PageSession(project, 1); s.last_active = active; s.viewers = viewers
        b.sessions[project] = s
        reg[project] = {"pid": os.getpid(), "port": 1, "started": active}
    assert browser.IDLE_STOP_S == 600
    fake("a", active=1000, viewers=0)          # 논 지 오래
    fake("b", active=1000, viewers=1)          # 지금 보는 중 — 아무리 오래돼도 안 끈다
    fake("c", active=9000, viewers=0)          # 논 지 얼마 안 됨
    now = 1000 + browser.IDLE_STOP_S + 1
    assert await b.reap(now=now) == ["a"]
    assert stopped == ["a"] and "a" not in b.sessions
    assert await b.reap(now=now) == []                                        # 두 번 불러도 더 끄지 않는다
    reg["ghost"] = {"pid": 999999, "port": 9, "started": now}                  # 밖에서 죽은 크롬
    assert await b.reap(now=now) == ["ghost"] and "ghost" not in reg
    monkeypatch.setattr(browser, "MAX_BROWSERS", 2)
    await b.stop("c"); stopped.clear()                                         # 남은 건 b(보는 중) 하나
    fake("a", active=5000, viewers=0)                                          # a(노는 중) + b = 상한 2개
    assert await b.make_room("d") == ["a"]                                     # 새로 열 자리를 '노는' a 로 만든다
    fake("a", active=9000, viewers=1)
    assert await b.make_room("d") == []                                        # 전부 보는 중이면 끊지 않는다


async def test_prune_은_죽은_장부만_지운다(sandbox, monkeypatch):
    b = browser.Browsers(stop=lambda p: True)
    browser.save_registry({"live": {"pid": os.getpid(), "port": 1, "started": 0},
                           "dead": {"pid": 999999, "port": 2, "started": 0}})
    assert await b.prune() == ["dead"]
    assert list(browser.load_registry()) == ["live"]                          # 살아 있는 것은 그대로


async def test_뷰어는_기본으로_크롬을_띄우지_않는다(aiohttp_client, sandbox):
    """안 연 프로젝트 패널이 저절로 붙어도 크롬이 서면 안 된다 — start=1 일 때만 띄운다."""
    client = await aiohttp_client(server.create_app())
    ws = await client.ws_connect("/ws/browser/frogtest", headers=GOOD)        # start 없음 = 0
    msg = await ws.receive()
    assert msg.type == WSMsgType.TEXT and json.loads(msg.data)["type"] == "closed"
    await ws.close()
    assert browser.load_registry() == {}                                       # 크롬이 서지 않았다
    ws = await client.ws_connect("/ws/browser/frogtest?start=1", headers=GOOD)
    async with asyncio.timeout(30):
        while True:
            msg = await ws.receive()
            if msg.type == WSMsgType.TEXT and json.loads(msg.data)["type"] == "state":
                break
    assert list(browser.load_registry()) == ["frogtest"]                        # 이제야 띄운다
    await ws.close()
