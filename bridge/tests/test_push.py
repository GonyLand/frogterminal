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
    t = notifier.Tracker(done_on=True, min_turn=0)
    t.step([win(1, "working", "a"), win(2, "working", "b")])
    t.step([win(1, "done", "a", said="끝"), win(2, "working", "b")])
    out = t.step([win(1, "done", "a", said="끝"), win(2, "working", "b")])
    assert [a["kind"] for a in out] == ["done"] and out[0]["title"].endswith("a") and out[0]["body"] == "끝"


def test_개별_완료_알림은_기본으로_꺼져_있다():
    t = notifier.Tracker(min_turn=0)
    t.step([win(1, "working", "a"), win(2, "working", "b")])
    t.step([win(1, "done", "a"), win(2, "working", "b")])
    assert t.step([win(1, "done", "a"), win(2, "working", "b")]) == []                     # 결재 대기·전부 끝남만 온다


def test_켜_둬도_짧은_턴은_건너뛴다():
    t = notifier.Tracker(done_on=True, min_turn=180)
    t.step([win(1, "working", "a"), win(2, "working", "b")], now=0)
    t.step([win(1, "done", "a"), win(2, "working", "b")], now=100)
    assert t.step([win(1, "done", "a"), win(2, "working", "b")], now=104) == []            # 104초 만에 끝난 턴
    t.step([win(1, "working", "a"), win(2, "working", "b")], now=110)
    t.step([win(1, "working", "a"), win(2, "working", "b")], now=114)
    t.step([win(1, "done", "a"), win(2, "working", "b")], now=400)
    out = t.step([win(1, "done", "a"), win(2, "working", "b")], now=404)                    # 290초 일한 턴
    assert [a["kind"] for a in out] == ["done"]


def test_결재를_거쳐도_턴_시작은_처음_일한_때():
    t = notifier.Tracker(done_on=True, min_turn=180)
    t.step([win(1, "working", "a"), win(2, "working", "b")], now=0)
    for now in (100, 104):
        t.step([win(1, "waiting", "a"), win(2, "working", "b")], now=now)
    for now in (150, 154):
        t.step([win(1, "working", "a"), win(2, "working", "b")], now=now)
    t.step([win(1, "done", "a"), win(2, "working", "b")], now=200)
    out = t.step([win(1, "done", "a"), win(2, "working", "b")], now=204)
    assert [a["kind"] for a in out] == ["done"]                                              # 204초 — 결재 뒤 재개로 시계가 새로 시작하지 않는다


def test_창_알림은_pid_를_싣고_누르면_그_창으로_간다():
    t = notifier.Tracker(done_on=True, min_turn=0)
    t.step([win(7, "working", "a"), win(9, "working", "b")])
    t.step([win(7, "waiting", "a"), win(9, "working", "b")])
    (waiting,) = t.step([win(7, "waiting", "a"), win(9, "working", "b")])
    p = notifier.payload_for(waiting)
    assert p["pid"] == 7 and p["url"] == "/?ext=7" and p["kind"] == "waiting" and p["tag"] == "ext-wait-7"
    t.step([win(7, "done", "a"), win(9, "working", "b")])
    (fin,) = t.step([win(7, "done", "a"), win(9, "working", "b")])
    assert notifier.payload_for(fin)["url"] == "/?ext=7"


def test_전부_끝났다는_특정_창이_아니라_첫_화면으로():
    t = notifier.Tracker()
    t.step([win(1, "working", "a")])
    t.step([win(1, "done", "a")])
    (all_done,) = t.step([win(1, "done", "a")])
    p = notifier.payload_for(all_done)
    assert p["url"] == "/" and p["pid"] == 0


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
    monkeypatch.setattr(webpush, "PREFS_FILE", tmp_path / "prefs.json")
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


def test_알림_설정은_기본이_꺼짐이고_알려진_칸의_불리언만_받는다(store):
    assert webpush.load_prefs() == {"done": False}
    assert webpush.set_prefs({"done": True, "evil": True}) == {"done": True}              # 모르는 칸은 버린다
    assert webpush.load_prefs() == {"done": True}
    assert webpush.set_prefs({"done": "yes"}) == {"done": True}                             # bool 이 아니면 그대로 둔다
    assert webpush.set_prefs([1]) == {"done": True}
    assert webpush.set_prefs({"done": False}) == {"done": False}


def test_망가진_설정_파일은_기본값(store):
    (store / "prefs.json").write_text("{not json", encoding="utf-8")
    assert webpush.load_prefs() == {"done": False}
    (store / "prefs.json").write_text('{"done": 1}', encoding="utf-8")
    assert webpush.load_prefs() == {"done": False}                                          # 1 은 bool 이 아니다


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


async def test_api_알림_설정_왕복(aiohttp_client, store):
    app = web.Application()
    push_api.add_routes(app)
    c = await aiohttp_client(app)
    assert await (await c.get("/api/push/prefs")).json() == {"done": False}
    assert await (await c.post("/api/push/prefs", json={"done": True})).json() == {"done": True}
    assert await (await c.get("/api/push/prefs")).json() == {"done": True}
    assert (await c.post("/api/push/prefs", data="not json")).status == 400
    assert (await c.post("/api/push/prefs", json=[1])).status == 400


def test_서비스워커와_푸시_스크립트는_정적_목록에_있고_파일이_있다():
    for route in ("/sw.js", "/push.js"):
        name, ctype = STATIC_FILES[route]
        assert ctype == "text/javascript" and (config.WEB_DIR / name).is_file()
