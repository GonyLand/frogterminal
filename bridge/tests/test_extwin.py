"""외부 클로드 창 — 콘솔 화면 → ANSI · 웹 키 → 콘솔 키 이벤트 · 목록 판정. 콘솔 API 는 건드리지 않는다(순수 함수와 가짜 카드)."""
from bridge import consolebridge as cb
from bridge import decisions, extwin, sessions

WHITE = 0x07


def test_cells_to_ansi_trims_default_blank_tail_and_keeps_colored_blank():
    plain = [("a", WHITE), (" ", WHITE), (" ", WHITE)]
    assert cb.cells_to_ansi(plain).startswith("\x1b[0ma") and cb.cells_to_ansi(plain).endswith("a\x1b[0m")
    boxed = [("a", WHITE), (" ", 0x40 | WHITE)]            # 빨간 바탕 빈칸은 화면의 일부라 남는다
    assert "\x1b[0;41m " in cb.cells_to_ansi(boxed)


def test_sgr_maps_console_colors_to_ansi_order():
    assert cb.sgr(0x04) == "\x1b[0;31m"                    # 콘솔 빨강(4) → ANSI 31
    assert cb.sgr(0x01) == "\x1b[0;34m"                    # 콘솔 파랑(1) → ANSI 34
    assert cb.sgr(0x0C) == "\x1b[0;91m"                    # 밝은 빨강
    assert cb.sgr(0x07) == "\x1b[0m"                       # 기본색은 터미널 기본으로
    assert cb.sgr(0x4007) == "\x1b[0;7m" and cb.sgr(0x8007) == "\x1b[0;4m"


def test_wide_char_trailing_cell_is_skipped():
    cells = [("한", WHITE | cb.LEADING), ("한", WHITE | cb.TRAILING), ("x", WHITE)]
    assert cb.cells_to_ansi(cells).count("한") == 1


def test_keys_plain_text_enter_backspace_and_ctrl():
    recs = cb.keys_to_records("a가\r\x7f\x03")
    assert [r[1] for r in recs] == ["a", "가", "\r", "\b", "\x03"]
    assert recs[2][0] == cb.VK["RETURN"] and recs[3][0] == cb.VK["BACK"]
    assert recs[4] == (0x43, "\x03", cb.LCTRL)             # Ctrl+C = 'C' 키 + 컨트롤


def test_keys_escape_sequences():
    assert cb.keys_to_records("\x1b")[0][:2] == (cb.VK["ESCAPE"], "\x1b")          # ESC 단독 = Esc 키
    assert cb.keys_to_records("\x1b[A")[0][0] == cb.VK["UP"]
    assert cb.keys_to_records("\x1b[D\x1b[C")[0][0] == cb.VK["LEFT"]
    assert cb.keys_to_records("\x1b[3~")[0][0] == cb.VK["DELETE"]
    assert cb.keys_to_records("\x1b[Z")[0] == (cb.VK["TAB"], "", cb.SHIFT)
    assert cb.keys_to_records("\x1b[1;5A")[0] == (cb.VK["UP"], "", cb.LCTRL)       # Ctrl+↑
    assert cb.keys_to_records("\x1bb")[0] == (ord("B"), "b", cb.LALT)                # Alt+b
    assert cb.keys_to_records("\x1b[200~hi\x1b[201~") == [(ord("H"), "h", 0), (ord("I"), "i", 0)]   # 붙여넣기 표시는 버린다


def test_keys_newline_in_paste_is_shift_enter():
    assert cb.keys_to_records("a\nb")[1] == (cb.VK["RETURN"], "\r", cb.SHIFT)


def _card(pid, start, kind="idle", **extra):
    return {"sessionId": f"s{pid}", "pid": pid, "pidStart": start, "kind": kind, "inTmux": False,
            "cwd": f"Y:/gony_land-wt{pid}", "project": "gony_land", "message": "", "updatedAt": "2026-10-13T12:00:00+09:00", **extra}


def test_list_windows_only_live_external_cards(monkeypatch):
    cards = [_card(10, "t10", "working"), _card(11, "old", "idle"),            # 11: 같은 pid 의 다른 프로세스(시작시각 다름) → 죽은 카드
             _card(12, "t12", "permission"), _card(13, "t13", "idle", inTmux=True),   # 13: frog 세션은 기존 목록 몫
             {"sessionId": "x", "kind": "idle", "inTmux": False}]                     # pid 없는 옛 카드
    monkeypatch.setattr(decisions, "_load_cards", lambda: cards)
    monkeypatch.setattr(sessions, "process_starts", lambda: {10: "t10", 11: "new", 12: "t12", 13: "t13"})
    monkeypatch.setattr(decisions, "context_for", lambda c: {"asked": "무엇을", "said": "", "lastAt": ""})
    got = {w["pid"]: w for w in extwin.list_windows()}
    assert sorted(got) == [10, 12]
    assert got[10]["state"] == "working" and got[12]["state"] == "waiting"
    assert got[10]["project"] == "gony_land-wt10"                                   # 워크트리는 폴더 이름으로 구분
    assert extwin.summary(list(got.values())) == {"total": 2, "working": 1, "waiting": 1, "done": 0}


def test_list_windows_marks_working_when_conversation_moved_on(monkeypatch):
    monkeypatch.setattr(decisions, "_load_cards", lambda: [_card(10, "t10", "permission")])
    monkeypatch.setattr(sessions, "process_starts", lambda: {10: "t10"})
    monkeypatch.setattr(decisions, "context_for", lambda c: {"asked": "", "said": "", "lastAt": "2026-10-13T12:05:00+09:00"})
    assert extwin.list_windows()[0]["state"] == "working"                            # 터미널에서 직접 승인해 훅이 못 울린 경우


def test_registry_refuses_pid_not_in_list(monkeypatch):
    monkeypatch.setattr(extwin.CACHE, "get", lambda: [{"pid": 10}])
    assert extwin.REGISTRY.get_or_create(99) is None                                 # 아무 프로세스에나 붙지 않는다


def test_list_refresh_and_open_do_not_deadlock(monkeypatch):
    """목록 갱신(캐시 락 → is_open 이 레지스트리 락)과 창 열기(get_or_create)가 겹쳐도 멈추지 않는다.
    열기가 레지스트리 락을 쥔 채 캐시 락을 기다리면 서로 물려, 폰에서 창을 여는 순간 API 스레드가 전부 막혔다(실측 — /api/ext · /api/push/key 가 응답 없음)."""
    import threading
    import time
    import types
    monkeypatch.setattr(extwin, "CACHE", extwin._Cache())
    monkeypatch.setattr(extwin, "REGISTRY", extwin._Registry())
    monkeypatch.setattr(decisions, "_load_cards", lambda: [_card(10, "t10", "idle")])
    monkeypatch.setattr(sessions, "process_starts", lambda: {10: "t10"})

    def slow_context(card):
        time.sleep(0.4)                                                              # 갱신이 캐시 락을 쥔 채 is_open 에 닿기 전의 틈
        return {"asked": "", "said": "", "lastAt": ""}
    monkeypatch.setattr(decisions, "context_for", slow_context)
    monkeypatch.setattr(extwin, "ExtWindow", lambda pid: types.SimpleNamespace(pid=pid, alive=True, subscribers=set()))
    done: dict[str, object] = {}

    def run(name, fn):
        done[name] = fn()
    refresh = threading.Thread(target=run, args=("refresh", extwin.CACHE.get), daemon=True)
    refresh.start()
    time.sleep(0.1)
    opener = threading.Thread(target=run, args=("open", lambda: extwin.REGISTRY.get_or_create(10)), daemon=True)
    opener.start()
    refresh.join(3)
    opener.join(3)
    assert not refresh.is_alive() and not opener.is_alive(), "교착"
    assert done["open"] is not None
