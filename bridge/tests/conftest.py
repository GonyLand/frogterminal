"""브릿지 테스트 공통 전제 — 기기 설정(bridge.json)과 토큰에 기대지 않고 돈다.

기기·사람 값은 코드가 아니라 ~/.frogterminal/bridge.json 에서 오므로(2026-10-11), 테스트는 여기서
시험용 식별자를 직접 꽂는다. 토큰 쿠키 검사는 기본으로 통과시킨다 — 기존 테스트 100곳이 헤더만
붙이고 호출하기 때문. 토큰 경로 자체는 test_token_auth.py 가 `real_token` 으로 되돌려 검사한다.
"""
import pytest

from bridge import config, security

TEST_LOGIN = "tester@example.com"
TEST_ORIGIN = "https://bridge.example.ts.net"
TEST_TOKEN = "test-token-0123456789abcdef"


@pytest.fixture(autouse=True)
def bridge_identity(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_LOGINS", frozenset({TEST_LOGIN}))
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", frozenset({TEST_ORIGIN}))
    monkeypatch.setattr(config, "OPEN_ANYWHERE", True)        # 기존 '어디든 열기' 테스트의 전제
    monkeypatch.setattr(config, "HUD_SCRIPT", None)
    monkeypatch.setattr(security, "TOKEN", TEST_TOKEN)         # ensure_token 이 파일을 건드리지 않게
    monkeypatch.setattr(security, "token_ok", lambda presented: True)
    yield


@pytest.fixture
def real_token(monkeypatch):
    """토큰 검사를 진짜로 되돌린다 — 쿠키 없는 요청은 로그인 관문에 걸린다."""
    monkeypatch.setattr(security, "token_ok", security.__dict__["_real_token_ok"])
    return TEST_TOKEN


# token_ok 원본을 보존한다(autouse 가 매 테스트 덮어쓰므로 모듈 로드 시점에 한 번).
security.__dict__.setdefault("_real_token_ok", security.token_ok)
