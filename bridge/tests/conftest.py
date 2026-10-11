"""브릿지 테스트 공통 전제 — 기기 설정(bridge.json)과 토큰에 기대지 않고 돈다.

기기·사람 값은 코드가 아니라 ~/.frogterminal/bridge.json 에서 오므로(2026-10-11), 테스트는 여기서
시험용 식별자를 직접 꽂는다. 토큰 쿠키 검사는 기본으로 통과시킨다 — 기존 테스트 100곳이 헤더만
붙이고 호출하기 때문. 토큰 경로 자체는 test_token_auth.py 가 `real_token` 으로 되돌려 검사한다.
"""
import pytest

from bridge import config, security

# 윈도우에서 건너뛰는 시험 — 전부 맥·tmux·POSIX 전제를 직접 재는 것들이다. 같은 약속(세션 목록·끄기·에이전트 판별·
# 스크롤·복원·화면 재현)은 윈도우판이 test_winmux.py 와 실서버 e2e(웹소켓·크롬)로 따로 잰다. 이름(또는 nodeid 일부)으로만 고른다.
WINDOWS_SKIP = {
    # tmux 를 직접 부르거나 tmux attach/pty 를 전제
    "test_kill_session_지목한_이름_하나만_끈다": "tmux kill-session",
    "test_copy_buffer_는_tmux_버퍼를_상한까지만_준다": "tmux show-buffer",
    "test_session_agents_는_pane_명령이_codex_인_세션만": "tmux list-panes",
    "test_leave_copy_mode_는_tmux_안에서_판단한다": "tmux 복사 모드",
    "test_복원은_명단에_있던_것만_되살린다": "tmux new-session -d",
    "test_복원은_붙지_않고_만들기만_한다": "tmux new-session -d",
    "test_복원은_끄기_직전_명단만_되살린다": "tmux new-session -d",
    "test_ws_roundtrip_resize_and_survival": "tmux list-clients/has-session",
    "test_ws_closes_and_stops_reading_when_child_dies": "tmux attach 자식 + pty EOF",
    "test_끄지_않은_세션은_re1_로도_그대로_붙는다": "tmux list-clients",
    "test_ws_agent_query_는_고정목록만_받는다": "sessions.attach_cmd(tmux)를 가로챈다 — 윈도우는 win_launch",
    "test_ws_unmode_는_복사모드를_빠져나온다": "tmux 복사 모드",
    # 맥 전용 명령·경로
    "test_open_은_검증된_경로만_화이트리스트_앱으로": "macOS open",
    "test_open_abs_는_열_수_있는_뿌리만_통과시킨다": "macOS open",
    "test_boot_time_은_sec만_읽고_미래면_물러선다": "sysctl kern.boottime",
    "test_candidates_는_업로드_캐시를_배제한다": "Spotlight(mdfind)",
    "test_NFD_NFC_가_달라도_같은_파일로_본다": "Spotlight(mdfind)",
    "test_recent_capture_는_크기가_맞는_최신_파일만_준다": "macOS 스크린샷 폴더",
    "test_list_services_는_브릿지_금지포트를_빼고_프로젝트를_cwd_로_매긴다": "lsof 출력 모의",
    # zsh 별칭·POSIX 경로·권한 비트
    "test_settings_왕복과_검증": "~/.zshrc 별칭 블록",
    "test_빈_별칭은_거부하지_않고_해당_줄과_블록을_지운다": "~/.zshrc 별칭 블록",
    "test_옛_cl_두줄을_마커_블록으로_이동하고_사용자_줄과_백업을_보존한다": "~/.zshrc 별칭 블록",
    "test_launch_command_커스텀_약칭": "윈도우는 별칭을 셸에 심지 않는다",
    "test_루트_검증은_홈_안_실제_디렉터리만_허용": "'/' 가 윈도우에선 절대경로가 아니다",
    "test_공격재현_루트를_슬래시나_홈으로_바꾸면_실제요청이_400": "'/' 가 윈도우에선 절대경로가 아니다",
    "test_토큰_파일_생성은_0600_이고_다시_읽으면_같다": "POSIX 권한 비트",
    "test_읽을_권한이_없어도_빈값": "POSIX chmod 000",
    "test_clear_card_는_정확히_일치할때만_지운다": "파일명에 개행 불가",
    "test_루트_안_평범한_심링크는_그대로_보인다": "심볼릭 링크 권한",
    r"test_앞뒤_공백_붙은_이름도_차단[\t.env]": "파일명에 탭 불가",
}


def pytest_collection_modifyitems(config, items):
    if not config_is_windows():
        return
    for item in items:
        for key, why in WINDOWS_SKIP.items():
            if item.nodeid.split("::")[-1].startswith(key):
                item.add_marker(pytest.mark.skip(reason=f"윈도우 제외 — {why}"))
                break


def config_is_windows() -> bool:
    import sys
    return sys.platform == "win32"


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
