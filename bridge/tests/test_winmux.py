"""윈도우 세션 호스트(winmux) — 화면 복원이 원본과 같은지, 모드·대체 화면·구독 순서가 맞는지.

ConPTY 없이 도는 단위 시험은 가짜 pty 를 쓰고, 마지막 시험 둘만 진짜 ConPTY(cmd.exe)를 띄운다.
윈도우가 아니면 통째로 건너뛴다.
"""
import asyncio
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ConPTY 는 윈도우 전용")

if sys.platform == "win32":
    import os

    import pyte

    from bridge import winmux


class FakePty:
    """winpty.PtyProcess 의 읽기·쓰기·크기만 흉내 낸다 — 시험이 직접 글자를 밀어 넣는다."""

    def __init__(self):
        self.pid = os.getpid()
        self.written: list[str] = []
        self.size = None
        self.exitstatus = 0
        self._alive = True

    def read(self, n):
        time.sleep(0.05)
        if not self._alive:
            raise EOFError
        return ""

    def write(self, s):
        self.written.append(s)

    def setwinsize(self, rows, cols):
        self.size = (rows, cols)

    def isalive(self):
        return self._alive

    def close(self, force=False):
        self._alive = False


def make_session(cols=40, rows=10) -> "winmux.Session":
    s = winmux.Session("t", FakePty(), cols, rows, ["x"], None)    # start() 하지 않는다 — 읽기 스레드 없음
    return s


def replay(snapshot: bytes, cols: int, rows: int) -> pyte.HistoryScreen:
    screen = pyte.HistoryScreen(cols, rows, history=500)
    pyte.ByteStream(screen).feed(snapshot)
    return screen


def test_snapshot_reproduces_visible_screen_with_colors_and_hangul():
    s = make_session()
    s.feed("\x1b[31m빨강\x1b[0m hello\r\n\x1b[1;44m굵게\x1b[0m world\r\n한글 ok")
    got = replay(s.snapshot(with_history=False), 40, 10)
    assert got.display == s.main.display
    assert (got.cursor.x, got.cursor.y) == (s.main.cursor.x, s.main.cursor.y)
    # 색·굵기까지 같다
    for y in range(3):
        for x in range(12):
            assert got.buffer[y][x] == s.main.buffer[y][x], (y, x)


def test_snapshot_with_history_restores_scrolled_off_lines():
    s = make_session(cols=30, rows=5)
    for i in range(40):
        s.feed(f"줄 {i}\r\n")
    got = replay(s.snapshot(with_history=True), 30, 5)
    assert got.display == s.main.display
    # 화면 위로 밀려난 줄이 되살아나 있다
    top = ["".join(line[x].data for x in range(30)).rstrip() for line in got.history.top]
    assert "줄 0" in top and "줄 30" in top


def test_snapshot_without_history_skips_scrollback():
    s = make_session(cols=30, rows=5)
    for i in range(40):
        s.feed(f"line {i}\r\n")
    got = replay(s.snapshot(with_history=False), 30, 5)
    assert len(got.history.top) == 0
    assert got.display == s.main.display


def test_alt_screen_is_kept_apart_and_main_survives():
    s = make_session(cols=20, rows=4)
    s.feed("main-text\r\n")
    s.feed("\x1b[?1049h\x1b[2J\x1b[HALT-SCREEN")
    assert s.in_alt
    snap = s.snapshot(with_history=False)
    assert b"\x1b[?1049h" in snap and "ALT-SCREEN".encode() in snap
    s.feed("\x1b[?1049l")
    assert not s.in_alt
    assert s.main.display[0].startswith("main-text")
    snap2 = s.snapshot(with_history=False)
    assert b"\x1b[?1049h" not in snap2
    assert replay(snap2, 20, 4).display[0].startswith("main-text")


def test_alt_sequence_split_across_chunks_still_switches():
    s = make_session(cols=20, rows=4)
    s.feed("keep\r\n\x1b[?10")
    s.feed("49hALT")
    assert s.in_alt
    assert s.alt.display[0].startswith("ALT")
    assert s.main.display[0].startswith("keep")      # 대체 화면 글자가 본 화면을 더럽히지 않았다
    s.feed("\x1b")
    s.feed("[?1049l")
    assert not s.in_alt
    assert s.main.display[0].startswith("keep")


def test_tracked_modes_survive_chunk_boundary_and_are_replayed():
    s = make_session()
    s.feed("\x1b[?20")
    s.feed("04h\x1b[?1000h\x1b[?1006h")
    assert s.modes[2004] and s.modes[1000] and s.modes[1006]
    snap = s.snapshot(False).decode()
    for needle in ("\x1b[?2004h", "\x1b[?1000h", "\x1b[?1006h"):
        assert needle in snap
    s.feed("\x1b[?2004l")
    assert "\x1b[?2004l" in s.snapshot(False).decode()


def test_deccolm_cannot_resize_the_screen():
    s = make_session(cols=40, rows=10)
    s.feed("\x1b[?3h")
    assert s.main.columns == 40
    s.feed("\x1b[?3l")
    assert s.main.columns == 40


def test_cursor_hidden_state_is_replayed():
    s = make_session()
    s.feed("hi\x1b[?25l")
    assert s.snapshot(False).decode().endswith("\x1b[?25l")
    s.feed("\x1b[?25h")
    assert s.snapshot(False).decode().endswith("\x1b[?25h")


def test_resize_updates_pty_and_both_screens():
    s = make_session(cols=40, rows=10)
    s.resize(60, 20)
    assert s.pty.size == (20, 60)
    assert (s.main.columns, s.main.lines, s.alt.columns, s.alt.lines) == (60, 20, 60, 20)
    s.resize(60, 20)                       # 같은 크기는 아무 일도 안 한다
    assert s.cols == 60 and s.rows == 20


def test_shrink_scrolls_top_lines_into_history_and_grow_pulls_them_back():
    s = make_session(cols=20, rows=6)
    for i in range(6):
        s.feed(f"row{i}\r\n" if i < 5 else f"row{i}")
    assert s.main.display[0].startswith("row0") and s.main.display[5].startswith("row5")
    s.resize(20, 3)                         # 위쪽 줄이 사라지는 게 아니라 스크롤백으로 올라간다
    assert [d.rstrip() for d in s.main.display] == ["row3", "row4", "row5"]
    assert len(s.main.history.top) == 3
    assert s.main.cursor.y == 2
    s.resize(20, 6)                         # 다시 늘리면 되돌아와 처음과 같은 화면
    assert [d.rstrip() for d in s.main.display] == [f"row{i}" for i in range(6)]
    assert len(s.main.history.top) == 0


def test_shrink_drops_blank_rows_below_cursor_before_touching_content():
    s = make_session(cols=20, rows=8)
    s.feed("keep")
    s.resize(20, 3)
    assert s.main.display[0].startswith("keep") and len(s.main.history.top) == 0


def test_win_launch_builds_pwsh_command_from_settings(tmp_path, monkeypatch):
    from bridge import sessions, settings
    (tmp_path / "proj").mkdir()
    monkeypatch.setattr(settings, "load", lambda: {
        "agent": "claude", "bypass": False, "rc": False, "cont": True,
        "commands": {"claude": "cl", "codex": ""}, "projectsRoot": str(tmp_path)})
    argv, cwd = sessions.win_launch("proj", root=tmp_path, agent="claude")
    assert os.path.basename(argv[0]).lower() in ("pwsh.exe", "powershell.exe", "pwsh", "powershell")
    assert cwd == str((tmp_path / "proj").resolve())
    i = argv.index("-Command")
    # 별칭(cl)은 셸에 심지 않으므로 실제 명령으로, 이어서 시작은 `||` 로 맨 명령에 물러선다
    assert argv[i + 1] == "claude --continue || claude"
    shell_argv, _ = sessions.win_launch("proj", root=tmp_path, agent="shell")
    assert "-Command" not in shell_argv                   # shell 은 그냥 pwsh
    tab_argv, _ = sessions.win_launch("proj@2", root=tmp_path, agent="claude")
    assert tab_argv[tab_argv.index("-Command") + 1] == "claude"      # 탭은 새 대화(이어가기 없음)


def test_win_launch_monitor_runs_the_cpu_popup_script():
    from bridge import config, sessions
    argv, cwd = sessions.win_launch(config.MONITOR_SESSION)
    assert cwd is None and "monitor_win.py" in argv[-1]


def test_screen_text_is_visible_rows_only():
    s = make_session(cols=20, rows=3)
    s.feed("alpha\r\nbeta")
    assert s.screen_text().splitlines()[:2] == ["alpha", "beta"]


def test_keys_to_text_matches_what_decisions_sends():
    assert winmux.keys_to_text(("2",)) == "2"
    assert winmux.keys_to_text(("-l", "--", "hello world")) == "hello world"
    assert winmux.keys_to_text(("Enter",)) == "\r"
    assert winmux.keys_to_text(("Down", "Enter")) == "\x1b[B\r"


def test_write_goes_to_pty_as_text_and_dead_session_ignores_writes():
    s = make_session()
    s.write("한글".encode("utf-8"))
    assert s.pty.written == ["한글"]
    s.alive = False
    s.write("x")
    assert s.pty.written == ["한글"]


async def test_attach_puts_snapshot_before_live_output_and_detach_stops_it():
    s = make_session()
    s.feed("before\r\n")
    loop = asyncio.get_running_loop()
    sub = winmux.Subscriber(loop)
    s.attach(sub, with_history=False)
    threading.Thread(target=lambda: s.feed("after\r\n")).start()
    first = await asyncio.wait_for(sub.queue.get(), 2)
    second = await asyncio.wait_for(sub.queue.get(), 2)
    assert b"before" in first and second == b"after\r\n"
    s.detach(sub)
    s.feed("later\r\n")
    await asyncio.sleep(0.05)
    assert sub.queue.empty()


async def test_slow_subscriber_is_dropped_not_buffered_forever(monkeypatch):
    monkeypatch.setattr(winmux, "MAX_QUEUE_BYTES", 100)
    loop = asyncio.get_running_loop()
    sub = winmux.Subscriber(loop)
    sub._put(b"x" * 60)
    sub._put(b"y" * 60)                    # 한도 초과 → 끊김 표시
    assert sub.overflowed and sub.closed
    assert await sub.queue.get() == b"x" * 60
    assert await sub.queue.get() is None


# ── 진짜 ConPTY ─────────────────────────────────────────────────────────────
async def collect(sub: "winmux.Subscriber", timeout=15.0) -> bytes:
    out = b""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        item = await asyncio.wait_for(sub.queue.get(), timeout)
        if item is None:
            break
        out += item
    return out


async def test_real_conpty_runs_a_command_and_reports_exit(tmp_path):
    mux = winmux.Mux()
    s = mux.create("real", ["cmd.exe", "/c", "echo frog-ok & exit 3"], str(tmp_path), 80, 24)
    sub = winmux.Subscriber(asyncio.get_running_loop())
    s.attach(sub, with_history=False)
    out = (await collect(sub)).decode("utf-8", "replace")
    assert "frog-ok" in out
    assert "프로세스 종료" in out
    assert not s.alive
    assert mux.get("real") is None         # 끝난 세션은 표에서 빠진다(tmux 와 같다)


async def test_real_conpty_hangul_roundtrip_and_resize(tmp_path):
    mux = winmux.Mux()
    s = mux.create("kr", ["cmd.exe", "/q", "/k", "chcp 65001 >nul"], str(tmp_path), 80, 24)
    try:
        sub = winmux.Subscriber(asyncio.get_running_loop())
        s.attach(sub, with_history=False)
        await asyncio.sleep(1.0)
        s.write("echo 한글-왕복\r")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and "한글-왕복" not in "\n".join(
                l for l in s.screen_text().splitlines()[1:]):
            await asyncio.sleep(0.2)
        assert "한글-왕복" in s.screen_text()
        s.resize(50, 12)
        await asyncio.sleep(0.5)
        assert (s.cols, s.rows) == (50, 12)
    finally:
        await asyncio.to_thread(mux.kill_all)
