"""웹푸시 — 폰 화면이 꺼져 있어도 PC 클로드 창의 결재·완료를 알린다.

세 가지만 한다.
  * VAPID 키 — 처음 한 번 만들어 ~/.frogterminal/vapid.pem 에 둔다(브라우저 구독과 서버 서명이 같은 키여야 한다).
  * 구독 명부 — 폰 브라우저가 알려 준 푸시 주소(endpoint)와 암호 키. ~/.frogterminal/push-subs.json.
  * 보내기 — 명부 전체에 한 통. 푸시 서비스가 '이 구독은 죽었다'(404·410)고 하면 명부에서 지운다.

endpoint 는 폰이 알려 주는 값이라 그대로 믿지 않는다 — https 이고 알려진 푸시 서비스 도메인일 때만 받는다
(브릿지가 아무 주소에나 POST 하는 구멍을 만들지 않는다). 내용은 푸시 서비스에 암호화돼 가고 폰에서만 풀린다.
pywebpush 는 무거워서 보낼 때만 불러 온다 — 푸시를 안 쓰면 브릿지 기동에 영향이 없다.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

from bridge import config

log = logging.getLogger("frogterminal.webpush")

VAPID_FILE = config.APP_DIR / "vapid.pem"
SUBS_FILE = config.APP_DIR / "push-subs.json"
VAPID_SUB = "mailto:frogterminal@localhost"        # VAPID 'sub' 클레임 — 푸시 서비스가 남용 때 연락할 곳(자기 PC 용이라 형식만 맞춘다)
MAX_SUBS = 8                                       # 폰·PC 브라우저 합쳐 이 정도면 충분하다
PAYLOAD_LIMIT = 2000                               # 푸시 본문 한도는 4KB 안쪽 — 넉넉히 줄인다
SEND_TIMEOUT = 10

# 브라우저가 쓰는 푸시 서비스 — 크롬/삼성(FCM) · 파이어폭스 · 사파리/아이폰 · 엣지(WNS). 접미사로만 받는다.
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", ".push.services.mozilla.com",
              ".push.apple.com", ".notify.windows.com")

_lock = threading.Lock()


# ── VAPID ────────────────────────────────────────────────────────────────────
def _load_vapid():
    from py_vapid import Vapid01
    if not VAPID_FILE.is_file():
        VAPID_FILE.parent.mkdir(parents=True, exist_ok=True)
        v = Vapid01()
        v.generate_keys()
        _atomic_write(VAPID_FILE, v.private_pem())
        return v
    return Vapid01.from_file(str(VAPID_FILE))


def public_key() -> str:
    """브라우저 pushManager.subscribe 의 applicationServerKey — 비압축 점을 base64url 로."""
    from cryptography.hazmat.primitives import serialization
    with _lock:
        v = _load_vapid()
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# ── 구독 명부 ────────────────────────────────────────────────────────────────
def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def endpoint_ok(endpoint: object) -> bool:
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        return False
    u = urlparse(endpoint)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or u.username or u.password or (u.port not in (None, 443)):
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


def clean_subscription(raw: object) -> dict | None:
    """폰이 보낸 subscription.toJSON() 을 검증해 우리가 저장할 모양으로. 못 믿으면 None."""
    if not isinstance(raw, dict) or not endpoint_ok(raw.get("endpoint")):
        return None
    keys = raw.get("keys")
    if not isinstance(keys, dict):
        return None
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (isinstance(p256dh, str) and isinstance(auth, str) and 0 < len(p256dh) <= 200 and 0 < len(auth) <= 100):
        return None
    return {"endpoint": raw["endpoint"], "keys": {"p256dh": p256dh, "auth": auth}}


def load_subs() -> list[dict]:
    try:
        data = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [s for s in (clean_subscription(x) for x in data) if s] if isinstance(data, list) else []


def _save_subs(subs: list[dict]) -> None:
    SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(SUBS_FILE, json.dumps(subs, ensure_ascii=False, indent=1).encode("utf-8"))


def add_sub(raw: object) -> bool:
    sub = clean_subscription(raw)
    if sub is None:
        return False
    with _lock:
        subs = [s for s in load_subs() if s["endpoint"] != sub["endpoint"]]
        subs.append(sub)
        _save_subs(subs[-MAX_SUBS:])               # 넘치면 오래된 것부터 버린다
    return True


def remove_sub(endpoint: object) -> bool:
    with _lock:
        subs = load_subs()
        kept = [s for s in subs if s["endpoint"] != endpoint]
        if len(kept) != len(subs):
            _save_subs(kept)
        return len(kept) != len(subs)


def has_subs() -> bool:
    return bool(load_subs())


# ── 보내기 ───────────────────────────────────────────────────────────────────
def _system_trust_session():
    """운영체제 인증서 저장소를 믿는 requests 세션. requests 기본(certifi 번들)은 HTTPS 를 가로채는 보안 프로그램
    (이 PC 는 Avast Web Shield — 자기 루트 CA 를 윈도우 저장소에만 심는다)의 인증서를 못 믿어 푸시 서비스 호출이 전부
    CERTIFICATE_VERIFY_FAILED 로 죽는다(실측 2026-10-13). 브라우저·stdlib 가 믿는 것과 같은 기준으로 맞춘다."""
    import ssl

    import requests
    from requests.adapters import HTTPAdapter

    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["ssl_context"] = ssl.create_default_context()
            super().init_poolmanager(*args, **kwargs)

    s = requests.Session()
    s.mount("https://", _Adapter())
    return s


def send_all(payload: dict, *, urgency: str = "normal", ttl: int = 600) -> dict:
    """명부 전체에 한 통. 블로킹이라 스레드에서 부른다. {sent, removed, failed} 를 돌려준다."""
    subs = load_subs()
    result = {"sent": 0, "removed": 0, "failed": 0}
    if not subs:
        return result
    from pywebpush import WebPushException, webpush
    with _lock:
        vapid = _load_vapid()
    data = json.dumps(payload, ensure_ascii=False)[:PAYLOAD_LIMIT]
    dead: list[str] = []
    session = _system_trust_session()
    for sub in subs:
        try:
            webpush(subscription_info=sub, data=data, vapid_private_key=vapid, vapid_claims={"sub": VAPID_SUB},   # claims 는 라이브러리가 고치니 매번 새로
                    ttl=ttl, headers={"Urgency": urgency}, timeout=SEND_TIMEOUT, requests_session=session)
            result["sent"] += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                dead.append(sub["endpoint"])
            else:
                result["failed"] += 1
                log.warning("push failed status=%s host=%s", status, urlparse(sub["endpoint"]).hostname)
        except Exception as e:                      # 네트워크 끊김 등 — 다음 구독은 계속
            result["failed"] += 1
            log.warning("push error %s host=%s", type(e).__name__, urlparse(sub["endpoint"]).hostname)
    if dead:
        with _lock:
            _save_subs([s for s in load_subs() if s["endpoint"] not in dead])
        result["removed"] = len(dead)
    return result
