"""미리보기(브라우저 패널) — lsof 파싱·외부 포트 배정·Funnel 판정·장부 수명. 실제 tailscale·lsof 는 안 부른다."""
import json

import pytest

from bridge import config, preview

LSOF_LISTEN = "\n".join([
    "p545", "cpython3.11", "f7", "n127.0.0.1:9118",
    "p549", "cnode", "f12", "n127.0.0.1:4567",
    "p7788", "cPython", "f4", "n127.0.0.1:7788",          # 브릿지 자신 — 절대 목록에 안 나온다
    "p600", "cnode", "f3", "n127.0.0.1:6000",             # 브라우저 금지 포트 — 목록에서 뺀다
    "p549", "cnode", "f13", "n127.0.0.1:4567",            # 같은 포트 중복 — 한 번만
])


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "PORT", 7788)
    monkeypatch.setattr(preview, "RESERVED", frozenset({7788}))
    return tmp_path / "previews.json"


def test_lsof_listen_파싱은_루프백_LISTEN_만():
    got = preview.parse_lsof_listen(LSOF_LISTEN)
    assert got[0] == {"pid": 545, "cmd": "python3.11", "port": 9118}
    assert [g["port"] for g in got] == [9118, 4567, 7788, 6000, 4567]


def test_list_services_는_브릿지_금지포트를_빼고_프로젝트를_cwd_로_매긴다(tmp_path, monkeypatch):
    root = tmp_path / "work"
    (root / "alpha" / "src").mkdir(parents=True)
    calls = []
    def fake_lsof(args):
        calls.append(args)
        if args[0].startswith("-i4TCP"):
            return LSOF_LISTEN
        pid = args[1]
        return f"p{pid}\nfcwd\nn{root / 'alpha' / 'src'}\n" if pid == "549" else f"p{pid}\nfcwd\nn/opt/x\n"
    monkeypatch.setattr(preview, "_lsof", fake_lsof)
    got = preview.list_services(root, probe=lambda port: 200 if port == 4567 else None)
    assert [(s["port"], s["project"]) for s in got] == [(4567, "alpha"), (9118, None)]   # 프로젝트 것 먼저
    assert got[0]["id"] == "549:4567" and got[0]["cmd"] == "node"
    assert got[0]["http"] == 200 and got[1]["http"] is None       # 평문 HTTP 에 답하는지가 실린다
    assert all(a[0] != "-p" or a[1] in ("545", "549") for a in calls)   # 7788·6000 은 cwd 조회도 안 한다


STATUS = {
    "TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
    "Web": {"bridge.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:7788"}}},
            "bridge.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8788"}}}},
    "AllowFunnel": {"bridge.example.ts.net:8443": True},
}


def test_serve_mappings_와_funnel_판정():
    assert preview.serve_mappings(STATUS) == {443: "http://127.0.0.1:7788", 8443: "http://127.0.0.1:8788"}
    assert preview.funnel_reaches(STATUS, 3000, "http://127.0.0.1:3000") is False
    assert preview.funnel_reaches(STATUS, 8443, "http://127.0.0.1:3000") is True     # 그 포트가 Funnel
    assert preview.funnel_reaches(STATUS, 13000, "http://127.0.0.1:8788") is True    # 목적지가 Funnel 로 새는 중
    assert preview.funnel_reaches({"AllowFunnel": "?"}, 3000, "x") is None          # 못 읽으면 판정불가


def test_pick_ext_port_규칙():
    maps = preview.serve_mappings(STATUS)
    assert preview.pick_ext_port(3000, maps, "http://127.0.0.1:3000") == 3000        # 그대로
    assert preview.pick_ext_port(6000, maps, "http://127.0.0.1:6000") == 13000       # 금지 포트 → 대역
    assert preview.pick_ext_port(443, maps, "http://127.0.0.1:443") == 13000         # 이미 Serve 가 씀 → 대역
    assert preview.pick_ext_port(8788, maps, "http://127.0.0.1:8788") == 8443        # 같은 목적지는 재사용
    assert preview.pick_ext_port(3000, {13000: "a"}, "http://127.0.0.1:3000") == 3000
    assert preview.pick_ext_port(13000, {}, "http://127.0.0.1:13000") == 13000       # 대역 안 포트도 빈 것부터


SERVICES = [{"id": "549:4567", "port": 4567, "pid": 549, "cmd": "node", "cwd": "/x", "project": "alpha"},
            {"id": "600:6000", "port": 6000, "pid": 600, "cmd": "node", "cwd": "/x", "project": "alpha"}]


def test_open_preview_는_serve_를_등록하고_장부에_적는다(reg, monkeypatch):
    calls = []
    states = [dict(STATUS), {**STATUS, "Web": {**STATUS["Web"],
              "bridge.example.ts.net:4567": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:4567"}}}}}]
    monkeypatch.setattr(preview, "serve_status", lambda: states.pop(0))
    monkeypatch.setattr(preview, "_serve", lambda args: (calls.append(args), (True, ""))[1])
    got = preview.open_preview("549:4567", "alpha", "me@x", services=SERVICES, now=1000)
    assert got == {"port": 4567, "dest": "http://127.0.0.1:4567", "expires": 1000 + preview.TTL_S}
    assert calls == [["--bg", "--https=4567", "http://127.0.0.1:4567"]]
    saved = json.loads(reg.read_text())["4567"]
    assert saved["project"] == "alpha" and saved["owner"] == "me@x" and saved["pid"] == 549


def test_open_preview_는_목록_밖_ID_와_금지포트를_거절한다(reg, monkeypatch):
    monkeypatch.setattr(preview, "_serve", lambda args: pytest.fail("serve 를 부르면 안 된다"))
    with pytest.raises(preview.PreviewError):
        preview.open_preview("1:7788", "p", "me", services=SERVICES)
    with pytest.raises(preview.PreviewError):
        preview.open_preview("600:6000", "p", "me", services=[{**SERVICES[1], "port": 7788}])


def test_open_preview_는_funnel_이면_열지_않고_판정불가도_거절(reg, monkeypatch):
    monkeypatch.setattr(preview, "_serve", lambda args: pytest.fail("serve 를 부르면 안 된다"))
    monkeypatch.setattr(preview, "serve_status", lambda: STATUS)
    svc = [{"id": "1:8788", "port": 8788, "pid": 1, "cmd": "x", "cwd": "/", "project": "p"}]
    with pytest.raises(preview.PreviewError, match="Funnel"):
        preview.open_preview("1:8788", "p", "me", services=svc)          # 8788 은 8443 Funnel 로 새는 중
    monkeypatch.setattr(preview, "serve_status", lambda: None)
    with pytest.raises(preview.PreviewError, match="판정불가"):
        preview.open_preview("549:4567", "p", "me", services=SERVICES)


def test_open_preview_는_연_뒤_판정이_바뀌면_되돌린다(reg, monkeypatch):
    calls = []
    bad_after = {**STATUS, "AllowFunnel": {"bridge.example.ts.net:4567": True}}
    states = [dict(STATUS), bad_after]
    monkeypatch.setattr(preview, "serve_status", lambda: states.pop(0))
    monkeypatch.setattr(preview, "_serve", lambda args: (calls.append(args), (True, ""))[1])
    with pytest.raises(preview.PreviewError, match="되돌렸"):
        preview.open_preview("549:4567", "p", "me", services=SERVICES)
    assert calls[-1] == ["--https=4567", "off"]
    assert not reg.exists()


def test_renew_close_reap_수명(reg, monkeypatch):
    calls = []
    monkeypatch.setattr(preview, "serve_status", lambda: STATUS)
    monkeypatch.setattr(preview, "_serve", lambda args: (calls.append(args), (True, ""))[1])
    preview.open_preview("549:4567", "p", "me", services=SERVICES, now=1000)
    assert preview.renew_preview(4567, now=2000) is True
    assert preview.active(now=2000 + preview.TTL_S - 1) and not preview.active(now=2000 + preview.TTL_S + 1)
    assert preview.renew_preview(9999, now=0) is False              # 장부에 없는 건 안 만진다
    assert preview.close_preview(8443) is False                     # 남의 매핑은 절대 안 거둔다
    assert ["--https=8443", "off"] not in calls
    assert preview.reap(now=2000 + preview.TTL_S + 1) == [4567]     # 만료 → 거둔다
    assert calls[-1] == ["--https=4567", "off"] and preview.load_registry() == {}
    preview.open_preview("549:4567", "p", "me", services=SERVICES, now=1000)
    assert preview.close_preview(4567) is True and preview.load_registry() == {}


def test_http_status_는_답하는_포트만_코드를_준다():
    import socket, threading
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    def serve():
        c, _ = srv.accept(); c.recv(1024)
        c.sendall(b"HTTP/1.0 204 No Content\r\nContent-Length: 0\r\n\r\n"); c.close()
    threading.Thread(target=serve, daemon=True).start()
    assert preview.http_status(port) == 204
    srv.close()
    quiet = socket.socket(); quiet.bind(("127.0.0.1", 0)); quiet.listen(1)   # 듣기만 하고 답 없음 → None
    assert preview.http_status(quiet.getsockname()[1]) is None
    quiet.close()
