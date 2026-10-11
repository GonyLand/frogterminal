import pytest
from bridge import security

GOOD = {"Tailscale-User-Login": "tester@example.com"}

def test_login_ok():
    assert security.allowed_login(GOOD) is True

def test_login_missing_is_rejected():        # fail-closed
    assert security.allowed_login({}) is False

def test_login_wrong_is_rejected():
    assert security.allowed_login({"Tailscale-User-Login": "attacker@x.com"}) is False

def test_allowed_origin_ok():
    assert security.allowed_origin({"Origin": "https://bridge.example.ts.net"}) is True

def test_allowed_origin_missing_is_false():  # 부재는 거짓
    assert security.allowed_origin({}) is False

def test_allowed_origin_evil_is_false():
    assert security.allowed_origin({"Origin": "https://evil.example"}) is False

def test_allowed_origin_vercel_is_false():   # 문패는 브릿지를 부르지 않는다 — 허용목록에서 제거됨
    assert security.allowed_origin({"Origin": "https://frogterminal.vercel.app"}) is False

def test_origin_ok_accepts_allowlisted():
    assert security.origin_ok({"Origin": "https://bridge.example.ts.net"}) is True

def test_origin_ok_accepts_absent():         # 같은 출처 내비게이션·GET fetch 는 Origin 을 안 보낸다
    assert security.origin_ok({}) is True

def test_origin_ok_rejects_evil():           # 있으면 반드시 허용목록 (WS 하이재킹·업로드 CSRF 차단)
    assert security.origin_ok({"Origin": "https://evil.example"}) is False

def test_origin_ok_rejects_vercel():         # 제거된 잉여 오리진은 WS 도 API 도 못 붙는다
    assert security.origin_ok({"Origin": "https://frogterminal.vercel.app"}) is False

def test_origin_ok_rejects_custom_nameplate():  # 새 문패(nameplate.example.com)도 브릿지엔 못 붙는다
    assert security.origin_ok({"Origin": "https://nameplate.example.com"}) is False

# ---- 같은 출처(Host 기준) 허용 — 테일넷 IP·MagicDNS 어느 쪽으로 접속해도 WS 가 붙게 ----

def test_origin_ok_accepts_same_host_tailnet_ip():
    assert security.origin_ok({"Origin": "https://100.100.1.2"}, "100.100.1.2") is True

def test_origin_ok_accepts_same_host_with_port():
    assert security.origin_ok({"Origin": "https://mac-studio:7788"}, "mac-studio:7788") is True

def test_origin_ok_still_rejects_evil_when_host_given():
    """Host 확장이 악성 Origin 통과 경로를 만들면 안 된다 — Origin≠Host 면 그대로 403."""
    assert security.origin_ok({"Origin": "https://evil.example"}, "100.100.1.2") is False

def test_origin_ok_rejects_host_lookalike_origin():
    """Host 를 접두사로 삼은 사칭 오리진(evil 쪽 하위도메인)도 막는다 — 완전일치만 통과."""
    assert security.origin_ok({"Origin": "https://100.100.1.2.evil.example"},
                              "100.100.1.2") is False

def test_origin_ok_rejects_http_scheme_of_same_host():
    """평문 http 오리진은 같은 출처가 아니다 — Serve 는 https 로만 연다."""
    assert security.origin_ok({"Origin": "http://100.100.1.2"}, "100.100.1.2") is False

def test_same_origin_without_host_is_false():   # Host 를 못 읽으면 이 완화는 아예 작동하지 않는다
    assert security.same_origin({"Origin": "https://100.100.1.2"}, "") is False

def test_funnel_json_off():
    assert security.parse_funnel_json('{"TCP":{"443":{"HTTPS":true}}}') is False

# Funnel 판정 = "우리 포트(7788)가 인터넷에 열려 있나" 하나. 켜진 Funnel 이 있다는 것만으로
# 거부하면, 이 맥에 함께 사는 다른 서비스의 Funnel 때문에 브릿지가 못 뜬다(2026-08-28 실사고:
# 8443→8788 Funnel 하나 때문에 기동 거부 → 대표 화면 502). 못 읽는 모양은 예전대로 거부.

_OURS = '"Web": {"h:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:7788"}}}}'
_남의것 = '"Web": {"h:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8788"}}}}'


def test_funnel_이_우리_포트를_열면_거부():
    text = '{"AllowFunnel": {"h:443": true}, %s}' % _OURS
    assert security.parse_funnel_json(text) is True

def test_남의_서비스_funnel_은_우리_기동을_막지_않는다():
    """실사고 그대로 — 8443 Funnel 은 8788 로 가고 우리(7788)는 테일넷 전용이다."""
    text = ('{"AllowFunnel": {"h:8443": true}, "TCP": {"443": {"HTTPS": true},'
            ' "8443": {"HTTPS": true}}, %s}' % _남의것.replace('"Web": {',
            '"Web": {"h:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:7788"}}}, ', 1))
    assert security.parse_funnel_json(text) is False

def test_funnel_이_켜졌는데_무엇을_내보내는지_모르면_거부():
    """옛 계약(무조건 거부)이 지키던 자리 — 정보가 없으면 판정불가로 막는다."""
    text = '{"AllowFunnel": {"bridge.example.ts.net:443": true}}'
    assert security.parse_funnel_json(text) is None

def test_tcp_통짜_전달로_우리_포트가_열려도_거부():
    text = ('{"AllowFunnel": {"h:2222": true},'
            ' "TCP": {"2222": {"TCPForward": "127.0.0.1:7788"}}}')
    assert security.parse_funnel_json(text) is True

def test_정적_파일만_내보내는_funnel_은_통과():
    text = '{"AllowFunnel": {"h:8443": true}, "Web": {"h:8443": {"Handlers": {"/": {"Path": "/tmp/x"}}}}}'
    assert security.parse_funnel_json(text) is False

def test_funnel_json_garbage_is_unknown():
    assert security.parse_funnel_json("not json") is None

def test_funnel_json_null_is_unknown():
    assert security.parse_funnel_json("null") is None

def test_funnel_json_array_is_unknown():
    assert security.parse_funnel_json("[]") is None

def test_assert_safe_to_start_blocks_on_unknown(monkeypatch):
    monkeypatch.setattr(security, "funnel_active", lambda: None)
    with pytest.raises(SystemExit):
        security.assert_safe_to_start()

def test_assert_safe_to_start_blocks_on_funnel(monkeypatch):
    monkeypatch.setattr(security, "funnel_active", lambda: True)
    with pytest.raises(SystemExit):
        security.assert_safe_to_start()

def test_assert_safe_to_start_passes(monkeypatch):
    monkeypatch.setattr(security, "funnel_active", lambda: False)
    security.assert_safe_to_start()
