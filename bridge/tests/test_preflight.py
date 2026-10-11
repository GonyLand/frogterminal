"""재시작 전 점검 테스트 — 이 점검이 통과라고 거짓말하면 사용자 터미널이 꺼진 채로 남는다."""
from bridge import config, preflight, security


def test_모두_정상이면_빈_목록(monkeypatch):
    monkeypatch.setattr(preflight, "import_problem", lambda: None)
    monkeypatch.setattr(security, "funnel_active", lambda: False)
    assert preflight.problems() == []
    assert preflight.main() == 0


def test_우리_포트가_funnel_이면_막는다(monkeypatch):
    """기동 게이트와 같은 판정을 '내리기 전에' 낸다 — 내린 뒤에 알면 이미 끊긴 것이다."""
    monkeypatch.setattr(preflight, "import_problem", lambda: None)
    monkeypatch.setattr(security, "funnel_active", lambda: True)
    found = preflight.problems()
    assert len(found) == 1 and str(config.PORT) in found[0]
    assert "off" in found[0]                       # 어떻게 끄는지까지 적혀야 손이 움직인다
    assert preflight.main() == 1


def test_판정불가도_막는다(monkeypatch):
    """게이트가 fail-closed 라 '모르겠다'는 곧 '안 뜬다'다 — 점검도 같은 편에 서야 한다."""
    monkeypatch.setattr(preflight, "import_problem", lambda: None)
    monkeypatch.setattr(security, "funnel_active", lambda: None)
    found = preflight.problems()
    assert len(found) == 1 and "판정불가" in found[0]


def test_모듈이_깨져_있으면_막는다(monkeypatch):
    """세션 열댓 개가 같은 저장소를 고친다 — 남의 반쯤 저장된 편집이 진짜 위험이다."""
    monkeypatch.setattr(preflight, "import_problem", lambda: "bridge.server 를 못 읽는다 — SyntaxError: x")
    monkeypatch.setattr(security, "funnel_active", lambda: False)
    assert any("SyntaxError" in p for p in preflight.problems())


def test_점검이_터져도_통과로_새지_않는다(monkeypatch):
    """점검 코드의 버그가 '이상 없음'으로 둔갑하면 이 파일이 있으나 마나다."""
    def 폭발():
        raise RuntimeError("ps 없음")
    monkeypatch.setattr(preflight, "import_problem", 폭발)
    monkeypatch.setattr(security, "funnel_active", lambda: False)
    found = preflight.problems()
    assert len(found) == 1 and "점검 자체가 실패" in found[0]


def test_진짜_모듈이_지금_읽힌다():
    """monkeypatch 없이 — 지금 이 순간의 저장소가 기동 가능한 상태인지 실제로 본다."""
    assert preflight.import_problem() is None
