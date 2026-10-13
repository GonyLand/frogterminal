"""웹푸시 — 상태 전이 판정(notifier.Tracker) · 구독 검증·명부 · 보내기(죽은 구독 정리) · /api/push/*. 푸시 서비스는 부르지 않는다(가짜)."""
import json

import pytest
from aiohttp import web

from bridge import notifier, push_api, webpush
from bridge.server import STATIC_FILES
from bridge import config

GOOD = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPk", "auth": "au"}}


def win(pid, state, project="p", **kw):
    return {"pid": pid, "state": state, "project": project, "message": kw.get("message", ""), "asked": kw.get("asked", ""),
            "said": kw.get("said", "")}


# ── 상태 전이 ────────────────────────────────────────────────────────────────
def test_처음_보는_창은_조용히_외운다():
    t = notifier.Tracker()
    assert t.step([win(1, "waiting"), win(2, "working")]) == []
    assert t.step([win(1, "waiting"), win(2, "working")]) == []


def test_결재_대기는_두_번_연속_같아야_울린다():
    t = notifier.Tracker()
    t.step([win(1, "working", "alpha")])
    assert t.step([win(1, "waiting", "alpha", message="rm -rf 해도 돼?")]) == []          # 한 번은 출렁임일 수 있다
    out = t.step([win(1, "waiting", "alpha", message="rm -rf 해도 돼?")])
    assert [a["kind"] for a in out] == ["waiting"] and out[0]["urgency"] == "high"
    assert "alpha" in out[0]["title"] and "rm -rf" in out[0]["body"]
    assert t.step([win(1, "waiting", "alpha")]) == []                                      # 같은 결재로 다시 울리지 않는다


def test_한_번_출렁이고_돌아오면_안_운다():
    t = notifier.Tracker()
    t.step([win(1, "working")])
    assert t.step([win(1, "done")]) == []
    assert t.step([win(1, "working")]) == []
    assert t.step([win(1, "done")]) == []                                                   # 대기 횟수가 새로 시작


def test_하나만_끝나면_개별_완료_알림():
    t = notifier.Tracker()
    t.step([win(1, "working", "a"), win(2, "working", "b")])
    t.step([win(1, "done", "a", said="끝"), win(2, "working", "b")])
    out = t.step([win(1, "done", "a", said="끝"), win(2, "working", "b")])
    assert [a["kind"] for a in out] == ["done"] and out[0]["title"].endswith("a") and out[0]["body"] == "끝"


def test_마지막이_끝나면_전부_끝났다_한_통으로_합친다():
    t = notifier.Tracker()
    t.step([win(1, "working", "a"), win(2, "done", "b")])
    t.step([win(1, "done", "a"), win(2, "done", "b")])
    out = t.step([win(1, "done", "a"), win(2, "done", "b")])
    assert [a["kind"] for a in out] == ["all_done"]                                         # 개별 완료까지 두 통 나가지 않는다
    assert "a" in out[0]["body"] and "창 2개" in out[0]["title"]
    assert t.step([win(1, "done", "a"), win(2, "done", "b")]) == []                         # 또 울리지 않는다


def test_일한_적_없으면_전부_끝났다를_안_낸다():
    t = notifier.Tracker()
    t.step([win(1, "done"), win(2, "done")])
    assert t.step([win(1, "done"), win(2, "done")]) == []


def test_쉬던_창이_결재를_만나도_운다():
    t = notifier.Tracker()
    t.step([win(1, "done")])
    t.step([win(1, "waiting")])
    assert [a["kind"] for a in t.step([win(1, "waiting")])] == ["waiting"]


def test_일하던_창이_사라지고_나머지가_쉬면_전부_끝났다():
    t = notifier.Tracker()
    t.step([win(1, "working", "a"), win(2, "done", "b")])
    out = t.step([win(2, "done", "b")])                                                     # 1번 클로드가 종료됨
    assert [a["kind"] for a in out] == ["all_done"]


# ── 구독 검증 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/x",                  # http
    "https://evil.example.com/fcm.googleapis.com",           # 다른 도메인
    "https://fcm.googleapis.com.evil.com/x",                 # 접미사 위장
    "https://user:pw@fcm.googleapis.com/x",                  # 자격 정보
    "https://fcm.googleapis.com:8443/x",                     # 다른 포트
    "https://127.0.0.1/x", "", None, 5,
])
def test_알려진_푸시_서비스가_아니면_거절(endpoint):
    assert webpush.clean_subscription({"endpoint": endpoint, "keys": GOOD["keys"]}) is None


@pytest.mark.parametrize("endpoint", [
    "https://fcm.googleapis.com/fcm/send/x", "https://updates.push.services.mozilla.com/wpush/v2/x",
    "https://web.push.apple.com/x", "https://wns2-par02p.notify.windows.com/w/?token=x"])
def test_알려진_푸시_서비스는_받는다(endpoint):
    assert webpush.clean_subscription({"endpoint": endpoint, "keys": GOOD["keys"]})["endpoint"] == endpoint


def test_키가_없거나_이상하면_거절():
    for keys in (None, {}, {"p256dh": "x"}, {"p256dh": "x", "auth": 3}, {"p256dh": "", "auth": "a"}):
        assert webpush.clean_subscription({"endpoint": GOOD["endpoint"], "keys": keys}) is None


# ── 명부 · 보내기 ────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(webpush, "SUBS_FILE", tmp_path / "subs.json")
    monkeypatch.setattr(webpush, "VAPID_FILE", tmp_path / "vapid.pem")
    return tmp_path


def test_명부는_같은_주소를_중복하지_않고_상한을_지킨다(store):
    assert webpush.add_sub(GOOD) and webpush.add_sub(GOOD)
    assert len(webpush.load_subs()) == 1
    for i in range(webpush.MAX_SUBS + 3):
        webpush.add_sub({**GOOD, "endpoint": f"https://fcm.googleapis.com/fcm/send/{i}"})
    subs = webpush.load_subs()
    assert len(subs) == webpush.MAX_SUBS and subs[-1]["endpoint"].endswith(f"/{webpush.MAX_SUBS + 2}")
    assert webpush.remove_sub(subs[0]["endpoint"]) and not webpush.remove_sub("https://nope")


def test_망가진_명부_파일은_빈_명부(store):
    (store / "subs.json").write_text("{not json", encoding="utf-8")
    assert webpush.load_subs() == [] and not webpush.has_subs()


def test_VAPID_키는_한_번_만들고_같은_공개키를_준다(store):
    pytest.importorskip("py_vapid")
    k1 = webpush.public_key()
    assert len(k1) == 87 and (store / "vapid.pem").is_file()      # 비압축 점 65바이트 → base64url 87자
    assert webpush.public_key() == k1


def test_보내기는_죽은_구독만_지운다(store, monkeypatch):
    pw = pytest.importorskip("pywebpush")
    pytest.importorskip("py_vapid")
    live, dead, flaky = (f"https://fcm.googleapis.com/fcm/send/{n}" for n in ("live", "dead", "flaky"))
    for ep in (live, dead, flaky):
        webpush.add_sub({**GOOD, "endpoint": ep})
    sent = []

    class Resp:
        def __init__(self, code): self.status_code = code

    def fake(subscription_info, data, **kw):
        ep = subscription_info["endpoint"]
        if ep == dead:
            raise pw.WebPushException("gone", response=Resp(410))
        if ep == flaky:
            raise pw.WebPushException("busy", response=Resp(503))
        assert type(kw["requests_session"].get_adapter("https://fcm.googleapis.com")).__name__ == "_Adapter"   # 시스템 저장소 세션(Avast 가로채기 대비)
        sent.append((ep, json.loads(data), kw["headers"]["Urgency"]))

    monkeypatch.setattr(pw, "webpush", fake)
    res = webpush.send_all({"title": "t", "body": "한글"}, urgency="high")
    assert res == {"sent": 1, "removed": 1, "failed": 1}
    assert sent == [(live, {"title": "t", "body": "한글"}, "high")]
    assert {s["endpoint"] for s in webpush.load_subs()} == {live, flaky}      # 일시 오류(503)는 구독을 지키지 않고 남겨 둔다


def test_구독이_없으면_보내지_않는다(store):
    assert webpush.send_all({"title": "t"}) == {"sent": 0, "removed": 0, "failed": 0}


# ── HTTP ─────────────────────────────────────────────────────────────────────
async def test_api_구독_왕복(aiohttp_client, store):
    pytest.importorskip("py_vapid")
    app = web.Application()
    push_api.add_routes(app)
    c = await aiohttp_client(app)
    key = await (await c.get("/api/push/key")).json()
    assert len(key["key"]) == 87 and key["subs"] == 0
    assert (await c.post("/api/push/subscribe", json={"subscription": GOOD})).status == 200
    assert (await (await c.get("/api/push/key")).json())["subs"] == 1
    assert (await c.post("/api/push/subscribe", json={"subscription": {"endpoint": "https://evil.example/x", "keys": GOOD["keys"]}})).status == 400
    assert (await c.post("/api/push/subscribe", data="not json")).status == 400
    assert (await c.post("/api/push/subscribe", json=[1])).status == 400
    r = await (await c.post("/api/push/unsubscribe", json={"endpoint": GOOD["endpoint"]})).json()
    assert r == {"ok": True, "removed": True}


def test_서비스워커와_푸시_스크립트는_정적_목록에_있고_파일이_있다():
    for route in ("/sw.js", "/push.js"):
        name, ctype = STATIC_FILES[route]
        assert ctype == "text/javascript" and (config.WEB_DIR / name).is_file()
