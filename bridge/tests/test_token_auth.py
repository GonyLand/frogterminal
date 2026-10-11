"""자체 토큰 인증(S1·S4)·열기 범위(S3)·HUD 설정(S8)·bridge.json 로더(S6) — 공개 배포 점검 2026-10-11."""
import json

import pytest

from bridge import config, security, server
from bridge.tests.conftest import TEST_LOGIN, TEST_ORIGIN, TEST_TOKEN

HEADERS = {"Tailscale-User-Login": TEST_LOGIN, "Origin": TEST_ORIGIN}
LOGIN_ONLY = {"Tailscale-User-Login": TEST_LOGIN}


def cookie(token=TEST_TOKEN):
    return {security.COOKIE: token}


@pytest.fixture(autouse=True)
def audit_to_tmp(tmp_path, monkeypatch):
    """로그인 감사로그가 진짜 작업공간의 frogterminal-audit.log 에 섞이지 않게(2026-10-11 실측: 시험 줄이 운영 로그에 남았다)."""
    monkeypatch.setattr(server, "audit_path", lambda: tmp_path / "audit.log")


# ── 토큰 관문 ────────────────────────────────────────────────────────────────

async def test_쿠키_없으면_화면은_로그인_페이지_API_는_403(aiohttp_client, real_token):
    client = await aiohttp_client(server.create_app())
    page = await client.get("/", headers=LOGIN_ONLY)
    assert page.status == 401
    body = await page.text()
    assert 'action="/login"' in body and "<script" not in body        # 외부 리소스·스크립트 0
    assert (await client.get("/api/sessions", headers=LOGIN_ONLY)).status == 403
    assert (await client.get("/app.js", headers=LOGIN_ONLY)).status == 403   # 정적 파일도 토큰 뒤


async def test_틀린_토큰은_401_이고_맞는_토큰은_쿠키를_심는다(aiohttp_client, real_token):
    client = await aiohttp_client(server.create_app())
    bad = await client.post("/login", data={"token": "wrong-wrong-wrong-wrong"}, headers=LOGIN_ONLY)
    assert bad.status == 401 and "맞지 않습니다" in await bad.text()
    assert security.COOKIE not in bad.cookies

    ok = await client.post("/login", data={"token": TEST_TOKEN}, headers=LOGIN_ONLY, allow_redirects=False)
    assert ok.status == 303 and ok.headers["Location"] == "/"
    morsel = ok.cookies[security.COOKIE]
    assert morsel.value == TEST_TOKEN
    assert morsel["httponly"] and morsel["samesite"] == "Lax" and morsel["path"] == "/"
    assert not morsel["secure"]                                         # 평문 테스트 클라이언트 — Secure 는 https 때만
    # 테스트 클라이언트의 쿠키 항아리에 실렸으니 이제 API 가 열린다
    assert (await client.get("/api/sessions", headers=LOGIN_ONLY)).status == 200


async def test_https_뒤에서는_Secure_쿠키(aiohttp_client, real_token):
    client = await aiohttp_client(server.create_app())
    ok = await client.post("/login", data={"token": TEST_TOKEN}, allow_redirects=False,
                           headers={**LOGIN_ONLY, "X-Forwarded-Proto": "https"})
    assert ok.status == 303 and ok.cookies[security.COOKIE]["secure"]


async def test_주소의_token_으로도_들어오고_주소에서_떼어낸다(aiohttp_client, real_token):
    client = await aiohttp_client(server.create_app())
    res = await client.get("/", params={"token": TEST_TOKEN}, headers=LOGIN_ONLY, allow_redirects=False)
    assert res.status == 303 and res.headers["Location"] == "/"
    assert res.cookies[security.COOKIE].value == TEST_TOKEN
    fresh = await aiohttp_client(server.create_app())                   # 위 303 이 쿠키를 심었으니 새 클라이언트로
    bad = await fresh.get("/", params={"token": "nope-nope-nope-nope"}, headers=LOGIN_ONLY, allow_redirects=False)
    assert bad.status == 401 and security.COOKIE not in bad.cookies


async def test_쿠키가_있어도_Tailscale_신원이_없으면_403(aiohttp_client, real_token):
    """토큰은 '추가' 겹이다 — allowedLogins 가 설정된 설치에서 신원 검사는 면제되지 않는다."""
    client = await aiohttp_client(server.create_app(), cookies=cookie())
    assert (await client.get("/api/sessions")).status == 403
    assert (await client.get("/")).status == 403                          # 로그인 화면조차 안 뜬다
    assert (await client.get("/api/sessions", headers=LOGIN_ONLY)).status == 200


async def test_allowedLogins_가_비면_신원_헤더_없이_토큰만으로_들어온다(aiohttp_client, real_token, monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_LOGINS", frozenset())
    client = await aiohttp_client(server.create_app())
    assert (await client.get("/")).status == 401                          # 토큰은 여전히 필수
    assert (await client.get("/api/sessions")).status == 403
    ok = await client.post("/login", data={"token": TEST_TOKEN}, allow_redirects=False)
    assert ok.status == 303
    assert (await client.get("/api/sessions")).status == 200


async def test_틀린_토큰_10회_뒤에는_429(aiohttp_client, real_token):
    client = await aiohttp_client(server.create_app())
    for _ in range(security.LOGIN_FAIL_LIMIT):
        assert (await client.post("/login", data={"token": "x" * 20}, headers=LOGIN_ONLY)).status == 401
    throttled = await client.post("/login", data={"token": TEST_TOKEN}, headers=LOGIN_ONLY, allow_redirects=False)
    assert throttled.status == 429                                        # 맞는 토큰이어도 창이 닫혀 있다
    assert security.COOKIE not in throttled.cookies


async def test_로그인_POST_도_Origin_검사를_받는다(aiohttp_client, real_token):
    client = await aiohttp_client(server.create_app())
    res = await client.post("/login", data={"token": TEST_TOKEN}, allow_redirects=False,
                            headers={**LOGIN_ONLY, "Origin": "https://evil.example"})
    assert res.status == 403


async def test_웹소켓도_토큰_뒤(aiohttp_client, real_token):
    client = await aiohttp_client(server.create_app())
    with pytest.raises(Exception):                                         # 403 으로 핸드셰이크 거절
        await client.ws_connect("/ws/anything", headers=HEADERS)


def test_token_ok_는_길이가_달라도_False_만_낸다():
    security.__dict__["_real_token_ok"]("short") is False
    assert security.__dict__["_real_token_ok"](None) is False
    assert security.__dict__["_real_token_ok"](TEST_TOKEN) is True


def test_limiter_는_창이_지나면_다시_허용한다():
    clock = [0.0]
    lim = security.LoginLimiter(limit=2, window=10.0, now=lambda: clock[0])
    lim.record_failure(); lim.record_failure()
    assert lim.allowed() is False
    clock[0] = 11.0
    assert lim.allowed() is True


def test_토큰_파일_생성은_0600_이고_다시_읽으면_같다(tmp_path):
    path = tmp_path / "token"
    first = security.load_or_create_token(path)
    assert len(first) >= security.TOKEN_MIN_CHARS and (path.stat().st_mode & 0o777) == 0o600
    assert security.load_or_create_token(path) == first


def test_짧거나_공백_있는_토큰_파일은_기동_거부(tmp_path):
    path = tmp_path / "token"
    path.write_text("short\n")
    with pytest.raises(SystemExit):
        security.load_or_create_token(path)
    path.write_text("has a space inside it yes\n")
    with pytest.raises(SystemExit):
        security.load_or_create_token(path)


# ── 열기 범위(S3) ─────────────────────────────────────────────────────────────

async def test_openAnywhere_가_꺼지면_절대경로_열기는_404(aiohttp_client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OPEN_ANYWHERE", False)
    target = tmp_path / "x.txt"; target.write_text("x")
    called = []
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: called.append(a))
    client = await aiohttp_client(server.create_app())
    res = await client.post("/api/open", json={"abs": str(target)}, headers=HEADERS)
    assert res.status == 404 and called == []                             # open 이 불리지 않았다


async def test_settings_응답에_home_이_실린다(aiohttp_client):
    """웹이 `~/경로` 를 펼칠 때 쓴다 — 예전엔 특정 루트 폴더 이름이 app.js 에 박혀 있었다."""
    from pathlib import Path
    client = await aiohttp_client(server.create_app())
    assert (await (await client.get("/api/settings", headers=HEADERS)).json())["home"] == str(Path.home())


# ── HUD(S8) ──────────────────────────────────────────────────────────────────

async def test_hudScript_없으면_HUD_는_빈_목록(aiohttp_client, monkeypatch):
    monkeypatch.setattr(config, "HUD_SCRIPT", None)
    server._hud_cache.update(at=0.0, lines=["stale"])
    client = await aiohttp_client(server.create_app())
    assert await (await client.get("/api/hud", headers=HEADERS)).json() == {"lines": []}


# ── bridge.json 로더(S6) ─────────────────────────────────────────────────────

def test_bridge_json_없으면_빈_설정(tmp_path):
    assert config.load_bridge_config(tmp_path / "none.json") == {}


def test_bridge_json_정상(tmp_path):
    p = tmp_path / "bridge.json"
    p.write_text(json.dumps({"allowedLogins": ["a@b.c"], "openAnywhere": True, "hudScript": "~/x.py"}))
    got = config.load_bridge_config(p)
    assert got["allowedLogins"] == ["a@b.c"] and got["openAnywhere"] is True


@pytest.mark.parametrize("text", [
    "not json", "[]", '{"allowedLogins": "a@b.c"}', '{"allowedLogins": [1]}',
    '{"openAnywhere": "yes"}', '{"tailscaleBin": 3}', '{"unknownKey": 1}',
])
def test_bridge_json_이_틀리면_기동_거부(tmp_path, text):
    p = tmp_path / "bridge.json"; p.write_text(text)
    with pytest.raises(SystemExit):
        config.load_bridge_config(p)


# ── 기동 게이트와 Tailscale 부재 ───────────────────────────────────────────────

def test_Tailscale_없고_allowedLogins_비면_경고만_하고_뜬다(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_LOGINS", frozenset())
    monkeypatch.setattr(security, "tailscale_installed", lambda: False)
    monkeypatch.setattr(security, "funnel_active", lambda: (_ for _ in ()).throw(AssertionError("안 불려야 한다")))
    security.assert_safe_to_start()


def test_Tailscale_없는데_allowedLogins_있으면_기동_거부(monkeypatch):
    monkeypatch.setattr(security, "tailscale_installed", lambda: False)
    with pytest.raises(SystemExit):
        security.assert_safe_to_start()


# ── 보안 헤더(S5 관찰) ────────────────────────────────────────────────────────

async def test_CSP_관찰_헤더가_403_에도_붙는다(aiohttp_client):
    client = await aiohttp_client(server.create_app())
    res = await client.get("/api/sessions")                               # 신원 없음 → 403
    assert res.status == 403
    csp = res.headers["Content-Security-Policy-Report-Only"]
    assert "script-src 'self' https://cdn.jsdelivr.net 'sha256-" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    assert res.headers["Referrer-Policy"] == "same-origin"   # no-referrer 면 크롬이 같은 출처 폼 POST 에 Origin: null → /login 403
