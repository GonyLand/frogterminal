"""인증(fail-closed)과 Funnel 자가점검. 셸을 여는 물건이라 전부 보수적으로.

두 겹이다. ①Tailscale 신원(bridge.json 의 allowedLogins 가 있을 때) ②자체 토큰 쿠키(항상).
①만 있던 시절엔 Tailscale 없이(nginx·공인 IP) 띄우는 순간 클라이언트가 헤더를 직접 써서 통과했다
— 공개 배포 점검 S1(2026-10-11). ②는 끄는 설정이 없다.
"""
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Mapping

from bridge import config
from bridge.config import PORT

# config 는 호출 시점에 읽는다(import 때 굳히지 않는다) — 테스트가 허용 계정을 갈아끼울 수 있게.


def tailscale_required() -> bool:
    """allowedLogins 가 하나라도 있으면 신원 헤더가 필수다. 비어 있으면 토큰만 본다."""
    return bool(config.ALLOWED_LOGINS)


def allowed_login(headers: Mapping) -> bool:
    if not tailscale_required():
        return True
    return headers.get("Tailscale-User-Login") in config.ALLOWED_LOGINS


def allowed_origin(headers: Mapping) -> bool:
    """Origin 헤더가 고정 허용목록에 있나 — 부재는 거짓."""
    return headers.get("Origin") in config.ALLOWED_ORIGINS


# ── 자체 토큰 ────────────────────────────────────────────────────────────────
COOKIE = "frog_token"
COOKIE_MAX_AGE = 365 * 24 * 3600            # 기기마다 한 번 입력 — 토큰 파일을 바꾸면 전부 무효가 된다
TOKEN_MIN_CHARS = 16
LOGIN_FAIL_LIMIT = 10                       # 10분에 10회 넘으면 로그인 시도 전부 429 (단일 사용자 도구 — 전역)
LOGIN_FAIL_WINDOW = 600.0
TOKEN: str | None = None                    # ensure_token() 이 채운다. 테스트는 직접 넣는다.


def load_or_create_token(path: Path = config.TOKEN_FILE) -> str:
    """토큰 파일을 읽고, 없으면 만든다(0600). 사람이 자기 문구로 바꿔도 된다 — 16자 미만은 거부."""
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(24)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token + "\n")
    if len(token) < TOKEN_MIN_CHARS or any(c.isspace() for c in token):
        sys.exit(f"[frogterminal] {path} 의 토큰은 공백 없이 {TOKEN_MIN_CHARS}자 이상이어야 한다")
    return token


def ensure_token() -> str:
    global TOKEN
    if TOKEN is None:
        TOKEN = load_or_create_token()
    return TOKEN


def token_ok(presented: str | None) -> bool:
    """쿠키(또는 로그인 입력)의 토큰이 맞나 — 길이가 달라도 시간이 새지 않게 compare_digest."""
    if TOKEN is None or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), TOKEN.encode("utf-8"))


class LoginLimiter:
    """실패를 세어 무차별 대입을 늦춘다. 프로세스 전역 — 사용자가 한 명이라 IP 를 가를 이유가 없다."""

    def __init__(self, limit: int = LOGIN_FAIL_LIMIT, window: float = LOGIN_FAIL_WINDOW, now=time.monotonic):
        self._fails: deque[float] = deque()
        self._limit, self._window, self._now = limit, window, now

    def _trim(self) -> None:
        cut = self._now() - self._window
        while self._fails and self._fails[0] < cut:
            self._fails.popleft()

    def allowed(self) -> bool:
        self._trim()
        return len(self._fails) < self._limit

    def record_failure(self) -> None:
        self._fails.append(self._now())

    def reset(self) -> None:
        self._fails.clear()


def same_origin(headers: Mapping, host: str) -> bool:
    """Origin 이 이 요청이 들어온 그 주소와 같은가 (= 우리가 서빙한 페이지가 부른 것).

    브릿지는 MagicDNS 이름(<호스트>.<테일넷>.ts.net)뿐 아니라 테일넷 IP(100.x)로도 열린다.
    IP 로 열면 Origin 이 https://100.x.y.z 이라 고정 목록에 없어 WS 만 403 으로 죽었다.
    Host 는 브라우저가 "접속한 주소"로 채우므로, Origin == https://{Host} 는 곧 같은 출처다.

    Host 스푸핑으로 뚫리지 않는 이유: 남의 페이지(evil.example)가 이 조건을 통과하려면 브라우저가
    Host: evil.example 로 우리에게 붙어야 하는데, 그러려면 evil.example 이 브릿지 IP 로 풀리고
    그 이름의 유효한 TLS 인증서까지 있어야 한다. Tailscale Serve 는 ts.net 이름 인증서만 내주므로
    핸드셰이크에서 끝난다. 게다가 신원 헤더(Tailscale-User-Login)는 Serve 가 테일넷 트래픽에만
    붙이므로 로그인 검사는 어떤 경로로도 면제되지 않는다.
    """
    origin = headers.get("Origin")
    return bool(host) and origin == f"https://{host}"


def origin_ok(headers: Mapping, host: str = "") -> bool:
    """요청 게이트용 Origin 판정 — "있으면 허용목록이거나 같은 출처, 없으면 통과".

    같은 출처 내비게이션(주소창으로 페이지 열기)과 same-origin GET fetch 는 브라우저가
    Origin 을 아예 안 붙인다. 부재를 403 으로 막으면 브릿지가 직접 서빙하는 정적 페이지가
    영영 안 열린다. 그래서 부재는 통과시키되, 아래 세 가지가 방어선으로 남는다:
      ① 웹소켓 핸드셰이크에는 브라우저가 Origin 을 항상 붙인다 → 악성 사이트발 WS 하이재킹은 그대로 차단.
      ② cross-origin form POST 도 Origin 을 붙인다 → 업로드 CSRF 차단 유지.
      ③ Origin 없는 요청은 직접 내비게이션·<img> 류인데, 이들은 응답 본문을 읽지 못한다(no-cors).
         게다가 신원 헤더(Tailscale-User-Login)는 Serve 가 테일넷 트래픽에만 붙이므로,
         로그인 검사(allowed_login)는 어떤 요청에서도 면제되지 않는다.
    """
    return "Origin" not in headers or allowed_origin(headers) or same_origin(headers, host)


def _reaches_us(entry, tcp: dict, port: str) -> bool | None:
    """이 Funnel 대상이 우리 포트로 연결되나. True/False/None(판정불가 = 위험 취급).

    Web 핸들러의 Proxy 주소와 TCP 통짜 전달(TCPForward) 둘 다 본다. 모르는 모양이면 None.
    """
    forward = (tcp.get(port) or {}).get("TCPForward")
    if isinstance(forward, str) and forward.endswith(f":{PORT}"):
        return True                              # 웹이 아니라 TCP 통째로 밖에 열린 경우
    if not isinstance(entry, dict):
        return None                              # Funnel 은 켜져 있는데 무엇을 내보내는지 모른다
    handlers = entry.get("Handlers")
    if not isinstance(handlers, dict) or not handlers:
        return None
    for h in handlers.values():
        if not isinstance(h, dict):
            return None
        proxy = h.get("Proxy")
        if proxy is None:                        # 정적 파일·고정 문구 — 우리 포트에 닿지 않는다
            continue
        if not isinstance(proxy, str):
            return None
        if proxy.endswith(f":{PORT}") or f":{PORT}/" in proxy:
            return True
    return False


def parse_funnel_json(text: str) -> bool | None:
    """serve status --json 에서 '우리 포트가 Funnel 로 밖에 열려 있나'. True/False/None(판정불가).

    켜진 Funnel 이 하나라도 있으면 무조건 거부하던 시절이 있었는데, 이 맥의 Tailscale 에는
    다른 서비스(8443→8788)의 Funnel 이 함께 산다. 그 하나 때문에 브릿지가 기동을 거부해 사용자
    화면이 502 로 끊겼다(2026-08-28 실사고). 막아야 할 것은 '우리 포트의 공개 노출' 하나다 —
    그것만 정확히 보고, 무엇을 내보내는지 못 읽으면 예전처럼 거부한다(fail-closed).
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    allow, web, tcp = data.get("AllowFunnel") or {}, data.get("Web") or {}, data.get("TCP") or {}
    if not isinstance(allow, dict) or not isinstance(web, dict) or not isinstance(tcp, dict):
        return None
    for target, on in allow.items():
        if not on:
            continue
        verdict = _reaches_us(web.get(target), tcp, str(target).rsplit(":", 1)[-1])
        if verdict is not False:                 # True(노출) 도 None(판정불가) 도 그대로 올린다
            return verdict
    return False


def funnel_active() -> bool | None:
    try:
        out = subprocess.run(
            [config.TAILSCALE_BIN, "serve", "status", "--json"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None                      # CLI 없음/실패 = 판정불가
    return parse_funnel_json(out)


def tailscale_installed() -> bool:
    return os.path.isfile(config.TAILSCALE_BIN)


def assert_safe_to_start() -> None:
    """Funnel이 꺼져 있다고 '확인'되지 않으면 뜨지 않는다.

    Tailscale 바이너리가 없는 맥: allowedLogins 가 비어 있으면(토큰만 쓰는 설치) 경고만 하고 뜬다 —
    바인드는 127.0.0.1 고정이고 토큰이 지킨다. allowedLogins 가 있는데 바이너리가 없으면 "Tailscale 에
    기대는 설정인데 Tailscale 이 없다"는 모순이라 거부한다.
    """
    if not tailscale_installed():
        if tailscale_required():
            sys.exit(f"[frogterminal] {config.TAILSCALE_BIN} 이 없다 — bridge.json 의 allowedLogins 는 Tailscale 신원인데 "
                     "Tailscale 이 설치돼 있지 않다. 설치하거나 allowedLogins 를 비우고(토큰만) 재시도.")
        print("[frogterminal] Tailscale 없음 — Funnel 점검 생략(토큰 인증만, 127.0.0.1 전용)", file=sys.stderr)
        return
    state = funnel_active()
    if state is not False:
        sys.exit(f"[frogterminal] Funnel 점검 실패(state={state}) — 기동 거부. "
                 f"'{config.TAILSCALE_BIN} serve status' 확인 후 Funnel을 끄고 재시도.")
